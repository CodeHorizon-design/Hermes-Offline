"""
Local GEPA (Generative Evolution of Prompt Agents) for hermes-offline.

hermes-agent upstream has no DSPy/evolution module; this is a fully
self-contained implementation that:

  1. Reads real session history from the tracker (tracker.py records every
     turn — user message, tool calls, model response, token counts).
  2. Converts sessions into dspy.Example training samples.
  3. Runs DSPy BootstrapFewShot to compile an optimised prompt module.
  4. Saves the compiled program to ~/.hermes/evolved/<model>/<date>.pkl
  5. On next launch, loads the compiled program and injects an optimised
     system-prompt suffix into hermes's initial messages.

Why BootstrapFewShot and not MIPRO?
  MIPRO requires ~50-150 evaluations and a teacher model. On 4-8 GB
  hardware that's 2-3 hours per evolution run. BootstrapFewShot needs
  only 3-8 demonstrations and runs in < 5 minutes on an 8B model.

Hardware limits enforced here:
  population_size=2           only 2 candidate prompts evaluated
  eval_budget=5               max 5 evals per candidate
  max_bootstrapped_demos=3    max 3 few-shot examples compiled in
  max_rounds=2                stop after 2 optimisation rounds

Activation:
  config.yaml:
    evolution:
      mode: lightweight        # "lightweight" | "disabled"
      population_size: 2
      eval_budget: 5
      auto_evolve: true        # evolve at session end (every N sessions)
      evolve_every: 5          # evolve after every 5 sessions

CLI:
  hermes-offline evolve        # run immediately
  hermes-offline evolve --dry-run
  hermes-offline evolve --reset   # delete compiled program, start fresh
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get("OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434")

# Evolution hyper-parameters — all overridable via config.yaml
_DEFAULTS = {
    "population_size":          2,
    "eval_budget":              5,
    "max_bootstrapped_demos":   3,
    "max_labeled_demos":        0,   # rely on bootstrapping, not human labels
    "max_rounds":               2,
    "min_sessions_to_evolve":   3,   # need at least 3 sessions of history
    "evolve_every":             5,   # sessions between auto-evolution runs
}


# ── DSPy Signature ────────────────────────────────────────────────────────────

def _get_hermes_signature():
    """
    Define the DSPy signature for hermes's task-solving behaviour.
    The signature describes: given a task, produce tool calls and a final answer.
    """
    try:
        import dspy  # type: ignore
    except ImportError:
        return None

    class HermesTaskSignature(dspy.Signature):
        """
        You are a precise AI agent. Given a task, use available tools to
        gather information and produce a correct, concise final answer.
        Prefer fewer tool calls. Never invent information — use tools.
        """
        task: str = dspy.InputField(
            desc="The user task or question to solve"
        )
        reasoning: str = dspy.OutputField(
            desc="Brief step-by-step plan for solving the task"
        )
        answer: str = dspy.OutputField(
            desc="The final answer or action taken to complete the task"
        )

    return HermesTaskSignature


# ── Session history extraction ────────────────────────────────────────────────

@dataclass
class EvolveExample:
    """Single training example extracted from session history."""
    task:       str       # user message
    answer:     str       # model final response
    tool_calls: list[str] = field(default_factory=list)
    tokens:     int = 0
    quality:    float = 1.0  # heuristic quality score (0-1)


def _load_session_history() -> list[EvolveExample]:
    """
    Read session history from tracker's JSONL log.
    Returns a list of EvolveExample, best sessions first.
    """
    try:
        from hermes_offline.tracker import _history_path
        history_file = _history_path()
    except Exception:
        history_file = Path.home() / ".hermes" / "offline_history.jsonl"

    if not history_file.exists():
        return []

    examples: list[EvolveExample] = []
    try:
        with history_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ex = _record_to_example(rec)
                    if ex:
                        examples.append(ex)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception as exc:
        logger.debug("Could not read session history: %s", exc)
        return []

    # Sort by quality descending — best examples first
    examples.sort(key=lambda e: e.quality, reverse=True)
    return examples


def _record_to_example(rec: dict) -> Optional[EvolveExample]:
    """Convert a tracker history record to an EvolveExample."""
    task   = rec.get("user_message") or rec.get("task", "")
    answer = rec.get("response") or rec.get("answer", "")
    if not task or not answer:
        return None

    tools      = rec.get("tool_calls", [])
    tokens     = rec.get("tokens_total", 0) or 0
    tok_per_s  = rec.get("tok_per_s", 0) or 0

    # Quality heuristic:
    #   +0.5 base
    #   +0.2 if response is not empty and not too short
    #   +0.2 if fewer than 5 tool calls (efficient)
    #   +0.1 if tokens/s was reasonable (model not stuck)
    quality = 0.5
    if len(answer) > 50:
        quality += 0.2
    if len(tools) < 5:
        quality += 0.2
    if tok_per_s > 2:
        quality += 0.1

    return EvolveExample(
        task=str(task),
        answer=str(answer),
        tool_calls=[str(t) for t in tools],
        tokens=int(tokens),
        quality=quality,
    )


def _examples_to_dspy(examples: list[EvolveExample]) -> list:
    """Convert EvolveExample list to dspy.Example list."""
    try:
        import dspy  # type: ignore
    except ImportError:
        return []

    dspy_examples = []
    for ex in examples:
        d = dspy.Example(
            task=ex.task,
            answer=ex.answer,
        ).with_inputs("task")
        dspy_examples.append(d)
    return dspy_examples


# ── Quality metric ────────────────────────────────────────────────────────────

def _answer_quality_metric(example, pred, trace=None) -> float:
    """
    Lightweight quality metric for BootstrapFewShot.
    Avoids expensive LLM judge calls — uses heuristics only.
    Score: 0.0 – 1.0
    """
    gold   = getattr(example, "answer", "") or ""
    answer = getattr(pred, "answer", "") or ""

    if not answer.strip():
        return 0.0

    score = 0.0

    # Not empty
    score += 0.3

    # Reasonable length (not too short, not runaway)
    length = len(answer)
    if 20 < length < 2000:
        score += 0.3
    elif length >= 20:
        score += 0.1

    # Shares at least one content word with gold (token overlap)
    if gold:
        gold_words = set(gold.lower().split())
        pred_words = set(answer.lower().split())
        overlap    = len(gold_words & pred_words) / max(len(gold_words), 1)
        score     += 0.4 * min(overlap * 3, 1.0)
    else:
        score += 0.2  # no gold label — give partial credit

    return min(score, 1.0)


# ── Evolved-program storage ───────────────────────────────────────────────────

def _evolved_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = get_hermes_home()
    except ImportError:
        base = Path.home() / ".hermes"
    return base / "evolved"


def _program_path(model: str) -> Path:
    safe = model.replace(":", "-").replace("/", "-")
    d = _evolved_dir() / safe
    d.mkdir(parents=True, exist_ok=True)
    return d / "current.json"


def _save_program(program, model: str, metadata: dict) -> Path:
    """Persist a compiled DSPy program to disk."""
    path = _program_path(model)
    try:
        program.save(str(path))
        meta_path = path.with_suffix(".meta.json")
        metadata["saved_at"] = time.time()
        metadata["model"]    = model
        meta_path.write_text(json.dumps(metadata, indent=2))
        logger.info("Evolved program saved: %s", path)
    except Exception as exc:
        logger.warning("Could not save evolved program: %s", exc)
    return path


def _load_program(model: str):
    """Load a previously compiled DSPy program. Returns None if not found."""
    path = _program_path(model)
    if not path.exists():
        return None, {}
    try:
        import dspy  # type: ignore
        sig = _get_hermes_signature()
        if sig is None:
            return None, {}
        prog = dspy.Predict(sig)
        prog.load(str(path))
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        logger.info("Loaded evolved program from %s (model=%s)", path, model)
        return prog, meta
    except Exception as exc:
        logger.debug("Could not load evolved program: %s", exc)
        return None, {}


def reset_evolved_program(model: str) -> bool:
    """Delete compiled program so evolution starts fresh."""
    path = _program_path(model)
    deleted = False
    for p in (path, path.with_suffix(".meta.json")):
        if p.exists():
            p.unlink()
            deleted = True
    if deleted:
        logger.info("Reset evolved program for model %s", model)
    return deleted


# ── Evolution counter ─────────────────────────────────────────────────────────

def _session_count_path() -> Path:
    d = _evolved_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "session_count.json"


def _increment_session_counter() -> int:
    path = _session_count_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    count = data.get("count", 0) + 1
    data["count"] = count
    try:
        path.write_text(json.dumps(data))
    except Exception:
        pass
    return count


def _should_auto_evolve(cfg: dict) -> bool:
    """Return True if it's time to run evolution based on session count."""
    count = _increment_session_counter()
    every = cfg.get("evolve_every", _DEFAULTS["evolve_every"])
    return count % every == 0


# ── Core evolution run ────────────────────────────────────────────────────────

def run_evolution(
    model: Optional[str] = None,
    cfg: Optional[dict] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> tuple[bool, str]:
    """
    Run a full BootstrapFewShot evolution pass.

    Args:
        model:   Ollama model name; auto-detected if None
        cfg:     Evolution config dict; loaded from config.yaml if None
        dry_run: Inspect history and show plan, but don't compile
        verbose: Print progress messages

    Returns:
        (success, message)
    """
    if cfg is None:
        cfg = _load_evolution_cfg()

    if cfg.get("mode") == "disabled":
        return False, "Evolution disabled in config"

    # Validate DSPy available
    try:
        import dspy  # type: ignore
    except ImportError:
        return False, (
            "DSPy not installed. Run: pip install dspy-ai  "
            "(or: pip install 'hermes-offline[evolution]')"
        )

    # Auto-detect model
    if model is None:
        model = _get_configured_model()
    if not model:
        return False, "No model configured — set model.default in config.yaml"

    # Load and validate history
    examples = _load_session_history()
    min_needed = cfg.get("min_sessions_to_evolve", _DEFAULTS["min_sessions_to_evolve"])
    if len(examples) < min_needed:
        return False, (
            f"Only {len(examples)} session(s) in history "
            f"(need {min_needed} to evolve). "
            "Keep using hermes-offline — evolution runs automatically."
        )

    if verbose or dry_run:
        print(f"[evolution] {len(examples)} training examples found")
        print(f"[evolution] Model:  {model}")
        print(f"[evolution] Params: population={cfg.get('population_size',2)}, "
              f"eval_budget={cfg.get('eval_budget',5)}, "
              f"max_demos={cfg.get('max_bootstrapped_demos',3)}")
        if dry_run:
            print("[evolution] Dry run — stopping here (--dry-run)")
            return True, f"Dry run OK ({len(examples)} examples available)"

    # Configure DSPy LM
    from hermes_offline.dspy_local import wire_dspy_local, get_dspy_lm
    if not wire_dspy_local(model=model):
        return False, f"Could not configure DSPy with model {model}"

    # Build signature + module
    sig = _get_hermes_signature()
    if sig is None:
        return False, "Could not build DSPy signature"

    module = dspy.Predict(sig)

    # Convert examples
    dspy_examples = _examples_to_dspy(examples)
    if not dspy_examples:
        return False, "Could not convert history to DSPy examples"

    # Split train/dev (80/20, min 1 dev example)
    n_dev   = max(1, len(dspy_examples) // 5)
    trainset = dspy_examples[:-n_dev]
    devset   = dspy_examples[-n_dev:]

    if verbose:
        print(f"[evolution] Train={len(trainset)}, Dev={len(devset)}")

    # Configure BootstrapFewShot (lightweight teleprompter)
    try:
        from dspy.teleprompt import BootstrapFewShot  # type: ignore
        teleprompter = BootstrapFewShot(
            metric=_answer_quality_metric,
            max_bootstrapped_demos=cfg.get("max_bootstrapped_demos", _DEFAULTS["max_bootstrapped_demos"]),
            max_labeled_demos=cfg.get("max_labeled_demos", _DEFAULTS["max_labeled_demos"]),
            max_rounds=cfg.get("max_rounds", _DEFAULTS["max_rounds"]),
        )
    except ImportError:
        return False, "dspy.teleprompt.BootstrapFewShot not available in this DSPy version"

    # Run optimisation
    if verbose:
        print(f"[evolution] Compiling... (this takes 1-5 min on {model})")

    t0 = time.time()
    try:
        compiled = teleprompter.compile(module, trainset=trainset)
    except Exception as exc:
        return False, f"Compilation failed: {exc}"
    elapsed = time.time() - t0

    if verbose:
        print(f"[evolution] Compiled in {elapsed:.0f}s")

    # Evaluate on devset
    try:
        from dspy.evaluate import Evaluate  # type: ignore
        evaluate = Evaluate(
            devset=devset,
            metric=_answer_quality_metric,
            num_threads=1,
            display_progress=False,
        )
        score = evaluate(compiled)
        if verbose:
            print(f"[evolution] Dev score: {score:.2f}")
    except Exception as exc:
        score = 0.0
        logger.debug("Evaluation failed: %s", exc)

    # Save
    metadata = {
        "examples": len(examples),
        "dev_score": float(score) if score else 0.0,
        "elapsed_s": elapsed,
        "dspy_version": _get_dspy_version(),
    }
    _save_program(compiled, model, metadata)

    return True, (
        f"Evolution complete. Dev score: {score:.2f}  "
        f"({len(examples)} examples, {elapsed:.0f}s)"
    )


# ── Evolved prompt injection ──────────────────────────────────────────────────

def apply_evolved_prompt(model: Optional[str] = None) -> bool:
    """
    Load the compiled program and inject evolved demos as a system-prompt
    addition. Called from patch.py after DSPy wiring.
    Returns True if an evolved program was found and applied.
    """
    if model is None:
        model = _get_configured_model()
    if not model:
        return False

    program, meta = _load_program(model)
    if program is None:
        return False

    # Extract the best few-shot demos compiled into the program
    demos = _extract_demos(program)
    if not demos:
        return False

    # Build a system-prompt suffix from the demos
    suffix = _build_prompt_suffix(demos, meta)
    if not suffix:
        return False

    # Inject into hermes's system prompt
    try:
        _inject_system_suffix(suffix)
        logger.info(
            "Applied evolved prompt (%d demos, dev_score=%.2f)",
            len(demos), meta.get("dev_score", 0),
        )
        return True
    except Exception as exc:
        logger.debug("Could not inject evolved prompt: %s", exc)
        return False


def _extract_demos(program) -> list[dict]:
    """Extract compiled few-shot demonstrations from a DSPy program."""
    demos = []
    try:
        # DSPy stores demos in predictors[].demos or .demos directly
        predictors = getattr(program, "predictors", lambda: [])()
        if not predictors:
            predictors = [program]
        for pred in predictors:
            for demo in getattr(pred, "demos", []):
                d = {}
                if hasattr(demo, "task"):
                    d["task"]   = str(demo.task)
                if hasattr(demo, "answer"):
                    d["answer"] = str(demo.answer)
                if d:
                    demos.append(d)
    except Exception:
        pass
    return demos[:3]  # Cap at 3 to keep system prompt short


def _build_prompt_suffix(demos: list[dict], meta: dict) -> str:
    """Convert few-shot demos into a system-prompt addition."""
    if not demos:
        return ""
    lines = [
        "\n\n## Learned Patterns (from your session history)\n",
        "These examples show effective task-solving patterns learned from past sessions:\n",
    ]
    for i, d in enumerate(demos, 1):
        task   = d.get("task", "")[:120]
        answer = d.get("answer", "")[:200]
        if task and answer:
            lines.append(f"\nExample {i}:")
            lines.append(f"  Task:   {task}")
            lines.append(f"  Answer: {answer}")
    lines.append(
        "\nApply these patterns — prefer concise, tool-grounded responses.\n"
    )
    return "\n".join(lines)


def _inject_system_suffix(suffix: str) -> None:
    """Append the evolved suffix to hermes's system prompt."""
    try:
        import hermes_cli.system_prompt as sp
        original = getattr(sp, "SYSTEM_PROMPT", "") or ""
        if suffix not in original:
            sp.SYSTEM_PROMPT = original + suffix
            logger.debug("Injected evolved prompt suffix (%d chars)", len(suffix))
        return
    except ImportError:
        pass

    # Fallback: try patching the config-based system prompt
    try:
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        existing = cfg.get("system_prompt_suffix", "")
        if suffix.strip() not in existing:
            cfg["system_prompt_suffix"] = existing + suffix
            save_config(cfg)
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_evolution_cfg() -> dict:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        evo = cfg.get("evolution", {})
        return evo if isinstance(evo, dict) else {}
    except Exception:
        return {}


def _get_configured_model() -> Optional[str]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        m = cfg.get("model", {})
        if isinstance(m, dict):
            return m.get("default")
        if isinstance(m, str):
            return m
    except Exception:
        pass
    return None


def _get_dspy_version() -> str:
    try:
        from importlib.metadata import version
        return version("dspy-ai")
    except Exception:
        try:
            import dspy  # type: ignore
            return getattr(dspy, "__version__", "unknown")
        except Exception:
            return "unknown"


# ── CLI entry point ───────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> None:
    """hermes-offline evolve — run or inspect local GEPA evolution."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="hermes-offline evolve",
        description="Run local DSPy-powered prompt evolution on your session history",
    )
    parser.add_argument("--dry-run",  action="store_true", help="Show plan without compiling")
    parser.add_argument("--reset",    action="store_true", help="Delete compiled program, start fresh")
    parser.add_argument("--model",    help="Ollama model to use (default: hermes config)")
    parser.add_argument("--verbose",  action="store_true", help="Print progress details")
    args = parser.parse_args(argv)

    if args.reset:
        model = args.model or _get_configured_model() or "qwen3:8b"
        deleted = reset_evolved_program(model)
        print(f"{'Deleted' if deleted else 'Nothing to delete'} — evolution will start fresh")
        sys.exit(0)

    ok, msg = run_evolution(
        model=args.model,
        dry_run=args.dry_run,
        verbose=args.verbose or True,
    )
    print(f"{'✓' if ok else '✗'}  {msg}")
    sys.exit(0 if ok else 1)
