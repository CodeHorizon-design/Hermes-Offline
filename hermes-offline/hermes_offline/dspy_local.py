"""
DSPy local model wiring for hermes-offline self-evolution.

hermes-agent has no built-in DSPy integration. This module provides:
  1. DSPy LM configuration → local Ollama (dspy.OllamaLocal or LM fallback)
  2. Version compatibility shim across DSPy 2.4 / 2.5 / 2.6
  3. get_dspy_lm() — shared accessor for evolution.py and any future modules
  4. Evolution config initialisation (population_size=2, mode=lightweight)
  5. Caching — LM object is created once per process

DSPy version compatibility:
  2.4   dspy.OllamaLocal(model, max_tokens, temperature)
  2.5   dspy.LM("ollama_chat/<model>", api_base=..., max_tokens=...)
  2.6   dspy.LM("ollama/<model>", ...) or dspy.OpenAI with Ollama endpoint

All three are tried in order. Falls back to OpenAI-compatible client
pointing at Ollama if none of the native adapters work.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Any

logger = logging.getLogger(__name__)

OLLAMA_BASE    = os.environ.get("OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_BASE_V1 = OLLAMA_BASE.rstrip("/") + "/v1"

# Module-level cache
_dspy_lm: Optional[Any] = None
_dspy_model: Optional[str] = None


# ── Version detection ─────────────────────────────────────────────────────────

def _dspy_version() -> tuple[int, int]:
    """Return (major, minor) DSPy version, e.g. (2, 5)."""
    try:
        from importlib.metadata import version
        v = version("dspy-ai")
        parts = v.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        try:
            import dspy  # type: ignore
            v = getattr(dspy, "__version__", "0.0")
            parts = v.split(".")
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            return (2, 4)


# ── LM factory ───────────────────────────────────────────────────────────────

def _make_lm(model: str) -> Optional[Any]:
    """
    Create a DSPy LM object for local Ollama.
    Tries four strategies in order of preference:
      1. dspy.LM("ollama/<model>") — DSPy 2.5+
      2. dspy.LM("ollama_chat/<model>") — DSPy 2.5 alternate
      3. dspy.OllamaLocal(model) — DSPy 2.4
      4. dspy.OpenAI(api_base=ollama) — universal fallback
    Returns None on complete failure.
    """
    try:
        import dspy  # type: ignore
    except ImportError:
        return None

    major, minor = _dspy_version()

    # Strategy 1 & 2: dspy.LM (DSPy 2.5+)
    if major > 2 or (major == 2 and minor >= 5):
        for prefix in ("ollama", "ollama_chat"):
            try:
                lm = dspy.LM(
                    f"{prefix}/{model}",
                    api_base=OLLAMA_BASE + "/",
                    api_key="ollama",
                    max_tokens=1024,
                    temperature=0.2,
                )
                logger.info("DSPy LM created via dspy.LM('%s/%s')", prefix, model)
                return lm
            except Exception as exc:
                logger.debug("dspy.LM('%s/%s') failed: %s", prefix, model, exc)

    # Strategy 3: dspy.OllamaLocal (DSPy 2.4)
    if hasattr(dspy, "OllamaLocal"):
        try:
            lm = dspy.OllamaLocal(
                model=model,
                base_url=OLLAMA_BASE,
                max_tokens=1024,
                temperature=0.2,
                timeout_s=60,
            )
            logger.info("DSPy LM created via dspy.OllamaLocal(%s)", model)
            return lm
        except Exception as exc:
            logger.debug("dspy.OllamaLocal failed: %s", exc)

    # Strategy 4: OpenAI-compatible fallback (works with any DSPy version)
    if hasattr(dspy, "OpenAI"):
        try:
            lm = dspy.OpenAI(
                model=model,
                api_base=OLLAMA_BASE_V1,
                api_key="ollama",
                max_tokens=1024,
                temperature=0.2,
            )
            logger.info("DSPy LM created via dspy.OpenAI(api_base=ollama) for %s", model)
            return lm
        except Exception as exc:
            logger.debug("dspy.OpenAI fallback failed: %s", exc)

    logger.warning("All DSPy LM strategies failed for model %s", model)
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_dspy_lm(model: Optional[str] = None) -> Optional[Any]:
    """
    Return the configured DSPy LM object (cached per process).
    Creates and configures it on first call.
    """
    global _dspy_lm, _dspy_model

    if model is None:
        model = _detect_local_model()
    if not model:
        return None

    # Return cached if same model
    if _dspy_lm is not None and _dspy_model == model:
        return _dspy_lm

    lm = _make_lm(model)
    if lm is not None:
        _dspy_lm   = lm
        _dspy_model = model
    return lm


def wire_dspy_local(model: Optional[str] = None) -> bool:
    """
    Configure DSPy to use the local Ollama model globally via dspy.configure().
    Returns True if DSPy was found and configured, False otherwise.
    """
    try:
        import dspy  # type: ignore
    except ImportError:
        logger.debug("DSPy not installed — skipping local wiring")
        return False

    if model is None:
        model = _detect_local_model()
    if not model:
        logger.debug("No local model found for DSPy wiring")
        return False

    lm = get_dspy_lm(model)
    if lm is None:
        return False

    try:
        dspy.configure(lm=lm)
        logger.info("dspy.configure(lm=%s) done", model)
        return True
    except Exception as exc:
        logger.debug("dspy.configure failed: %s", exc)
        return False


def _detect_local_model() -> Optional[str]:
    """Read the configured model from hermes config."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            return model_cfg.get("default")
        if isinstance(model_cfg, str):
            return model_cfg
    except Exception:
        pass
    # Fallback: ask Ollama what's available
    try:
        import urllib.request, json
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.loads(r.read()).get("models", [])]
            if models:
                return models[0]
    except Exception:
        pass
    return None


def patch_evolution_config() -> bool:
    """
    Write evolution defaults into config.yaml if not already set:
      evolution.mode = lightweight
      evolution.population_size = 2
      evolution.eval_budget = 5
      evolution.auto_evolve = true
      evolution.evolve_every = 5
    """
    try:
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        evo = cfg.get("evolution", {})
        if not isinstance(evo, dict):
            evo = {}

        changed = False
        defaults = {
            "mode":            "lightweight",
            "population_size": 2,
            "eval_budget":     5,
            "auto_evolve":     True,
            "evolve_every":    5,
        }
        for k, v in defaults.items():
            if k not in evo:
                evo[k] = v
                changed = True
        # Cap population_size for low-end hardware
        if evo.get("population_size", 5) > 2:
            evo["population_size"] = 2
            changed = True

        if changed:
            cfg["evolution"] = evo
            save_config(cfg)
            logger.info(
                "Evolution config updated: mode=%s population=%d",
                evo["mode"], evo["population_size"],
            )
        return True
    except Exception as exc:
        logger.debug("Could not patch evolution config: %s", exc)
        return False


def is_dspy_available() -> bool:
    """Check if DSPy is installed without importing it."""
    try:
        from importlib.metadata import version
        version("dspy-ai")
        return True
    except Exception:
        return False
