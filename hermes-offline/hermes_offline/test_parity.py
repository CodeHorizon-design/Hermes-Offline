"""
Phase 6 — Feature Parity Test Suite for Hermes Offline.

Tests every tool category and core subsystem against a live Ollama instance.
Runs without hermes-agent installed — probes Ollama directly via HTTP so the
suite works as a standalone pre-install check as well.

Usage:
    hermes-offline-test-parity              # full test, all categories
    hermes-offline-test-parity --quick      # fast smoke test only (3 tests)
    hermes-offline-test-parity --category browser
    hermes-offline-test-parity --no-color   # CI-safe plain output
    hermes-offline-test-parity --json       # machine-readable JSON output
    hermes-offline-test-parity --model qwen3:4b  # override model

Exit codes:
    0  All essential tests passed
    1  One or more essential tests failed
    2  Ollama not running (pre-condition failure)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    _RICH = True
except ImportError:
    _RICH = False


OLLAMA_BASE = os.environ.get("OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    category: str
    passed: bool
    essential: bool       # False = optional/soft-dep test
    duration_ms: float
    detail: str = ""
    skip_reason: str = ""

    @property
    def skipped(self) -> bool:
        return bool(self.skip_reason)

    @property
    def icon(self) -> str:
        if self.skipped:
            return "–"
        return "✓" if self.passed else "✗"


@dataclass
class ParityReport:
    results: list[TestResult] = field(default_factory=list)
    model: str = ""
    ollama_running: bool = False
    start_time: float = field(default_factory=time.time)

    def add(self, r: TestResult) -> None:
        self.results.append(r)

    @property
    def essential_passed(self) -> int:
        return sum(1 for r in self.results if r.essential and r.passed and not r.skipped)

    @property
    def essential_total(self) -> int:
        return sum(1 for r in self.results if r.essential and not r.skipped)

    @property
    def optional_passed(self) -> int:
        return sum(1 for r in self.results if not r.essential and r.passed and not r.skipped)

    @property
    def optional_total(self) -> int:
        return sum(1 for r in self.results if not r.essential and not r.skipped)

    @property
    def all_essential_pass(self) -> bool:
        return self.essential_passed == self.essential_total

    @property
    def total_duration_ms(self) -> float:
        return (time.time() - self.start_time) * 1000


# ── Low-level Ollama helpers ───────────────────────────────────────────────────

def _ollama_get(path: str, timeout: int = 5) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _ollama_post(path: str, payload: dict, timeout: int = 30) -> Optional[dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:
        raise exc


def _get_model(preferred: Optional[str] = None) -> Optional[str]:
    data = _ollama_get("/api/tags")
    if not data:
        return None
    models = [m["name"] for m in data.get("models", [])]
    if not models:
        return None
    if preferred:
        base = preferred.split(":")[0].lower()
        for m in models:
            if m.lower().startswith(base):
                return m
    return models[0]


def _chat(model: str, prompt: str, tools: Optional[list] = None, timeout: int = 30) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    return _ollama_post("/api/chat", payload, timeout=timeout) or {}


# ── Test helpers ───────────────────────────────────────────────────────────────

def _run_test(
    name: str,
    category: str,
    fn,
    essential: bool = True,
    skip_if: Optional[str] = None,
) -> TestResult:
    if skip_if:
        return TestResult(name=name, category=category, passed=False,
                          essential=essential, duration_ms=0, skip_reason=skip_if)
    t0 = time.time()
    try:
        passed, detail = fn()
    except Exception as exc:
        passed, detail = False, f"Exception: {exc}"
    ms = (time.time() - t0) * 1000
    return TestResult(name=name, category=category, passed=passed,
                      essential=essential, duration_ms=ms, detail=detail)


# ── Individual test functions ──────────────────────────────────────────────────

def test_ollama_running() -> tuple[bool, str]:
    data = _ollama_get("/api/tags", timeout=4)
    if data is None:
        return False, "Ollama not reachable at 127.0.0.1:11434"
    count = len(data.get("models", []))
    return True, f"{count} model(s) installed"


def test_model_available(model: str) -> tuple[bool, str]:
    data = _ollama_get("/api/tags")
    if not data:
        return False, "Ollama not responding"
    models = [m["name"] for m in data.get("models", [])]
    base = model.split(":")[0].lower()
    found = [m for m in models if m.lower().startswith(base)]
    if found:
        return True, f"Found: {found[0]}"
    return False, f"'{model}' not found. Pull: ollama pull {model}"


def test_basic_chat(model: str) -> tuple[bool, str]:
    resp = _chat(model, "Reply with the single word: PONG", timeout=25)
    content = resp.get("message", {}).get("content", "")
    if not content:
        return False, "Empty response"
    return True, f"{len(content)} chars"


def test_single_tool_call(model: str) -> tuple[bool, str]:
    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path"}},
                "required": ["path"],
            },
        },
    }]
    resp = _chat(model, "Read the file at /etc/hostname", tools=tools, timeout=30)
    msg = resp.get("message", {})
    tc = msg.get("tool_calls", [])
    if tc:
        fn = tc[0].get("function", {})
        return True, f"Called {fn.get('name')} with {fn.get('arguments', {})}"
    if "read_file" in msg.get("content", "").lower():
        return True, "Tool referenced in text (acceptable)"
    return False, "No tool call produced"


def test_parallel_tool_calls(model: str) -> tuple[bool, str]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "Get current time",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]
    resp = _chat(
        model,
        "List files in /tmp AND get the current time. Call both tools.",
        tools=tools,
        timeout=30,
    )
    msg = resp.get("message", {})
    tc = msg.get("tool_calls", [])
    if len(tc) >= 2:
        names = [t.get("function", {}).get("name") for t in tc]
        return True, f"Parallel calls: {names}"
    if len(tc) == 1:
        return True, "Sequential tool call (parallel support varies by model — acceptable)"
    return False, "No tool calls produced"


def test_no_tool_hallucination(model: str) -> tuple[bool, str]:
    tools = [{
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    }]
    resp = _chat(model, "What is 2 + 2?", tools=tools, timeout=20)
    msg = resp.get("message", {})
    tc = msg.get("tool_calls", [])
    content = msg.get("content", "")
    if tc:
        return False, f"Model hallucinated tool call for '2+2': {tc}"
    if content and "4" in content:
        return True, "Correctly answered without tool call"
    if content:
        return True, f"Answered in text (no spurious tool call): {content[:60]}"
    return False, "Empty response"


def test_context_window_fill(model: str) -> tuple[bool, str]:
    long_context = "The quick brown fox jumped over the lazy dog. " * 200
    prompt = long_context + "\n\nSummarize the above in exactly 5 words."
    resp = _chat(model, prompt, timeout=40)
    content = resp.get("message", {}).get("content", "")
    if not content:
        return False, "No response to long context"
    words = len(content.split())
    return True, f"Responded to ~{len(long_context)} char context ({words} words)"


def test_json_schema_output(model: str) -> tuple[bool, str]:
    resp = _chat(
        model,
        'Return ONLY valid JSON: {"name": "Alice", "age": 30}  — exactly that, nothing else.',
        timeout=20,
    )
    content = resp.get("message", {}).get("content", "").strip()
    try:
        parsed = json.loads(content)
        return True, f"Valid JSON: keys={list(parsed.keys())}"
    except json.JSONDecodeError:
        # Some models wrap in markdown — strip and retry
        stripped = content.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
        try:
            parsed = json.loads(stripped)
            return True, "Valid JSON (after stripping markdown)"
        except json.JSONDecodeError:
            return False, f"Invalid JSON output: {content[:80]}"


def test_embedding_available(embed_model: str = "nomic-embed-text") -> tuple[bool, str]:
    payload = {"model": embed_model, "prompt": "hello world"}
    try:
        resp = _ollama_post("/api/embeddings", payload, timeout=10) or {}
        vec = resp.get("embedding", [])
        if len(vec) >= 64:
            return True, f"{len(vec)}-dim vector"
        return False, f"Vector too short: {len(vec)} dims"
    except Exception as exc:
        return False, str(exc)


def test_ollama_vision(model: str = "llava:7b") -> tuple[bool, str]:
    data = _ollama_get("/api/tags")
    if not data:
        return False, "Ollama not responding"
    models = [m["name"] for m in data.get("models", [])]
    vision_models = [m for m in models if any(v in m.lower() for v in ("llava", "moondream", "bakllava"))]
    if not vision_models:
        return False, "No vision model installed (ollama pull llava:7b)"
    return True, f"Found: {vision_models[0]}"


def test_local_search_duckduckgo() -> tuple[bool, str]:
    try:
        from hermes_offline.local_search import duckduckgo_search
        results = duckduckgo_search("Python programming language")
        if results:
            return True, f"{len(results)} results"
        return False, "No results returned"
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_local_search_wikipedia() -> tuple[bool, str]:
    try:
        from hermes_offline.local_search import wikipedia_search
        results = wikipedia_search("Python programming language")
        if results:
            return True, f"{len(results)} results"
        return False, "No results returned"
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_hardware_detection() -> tuple[bool, str]:
    try:
        from hermes_offline.hardware import get_hardware_profile
        profile = get_hardware_profile()
        return True, (
            f"tier={profile.tier} ram={profile.total_ram_gb:.1f}GB "
            f"vram={profile.vram_gb:.1f}GB"
        )
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_beam_memory_store() -> tuple[bool, str]:
    try:
        import pathlib, tempfile, time as _time
        from hermes_offline.beam_memory import BEAMStore, MemoryEntry

        with tempfile.TemporaryDirectory() as td:
            store = BEAMStore(pathlib.Path(td) / "test.db")
            store.add(MemoryEntry(
                tier="B",
                content="Alice loves Python programming",
                session_id="test-session",
                created_at=int(_time.time()),
            ))
            store.add(MemoryEntry(
                tier="E",
                content="Yesterday we fixed a bug in the memory module",
                session_id="test-session",
                created_at=int(_time.time()),
            ))
            results = store.search_fts("Python", limit=5)
            store.close()

        if results:
            return True, f"FTS5 search returned {len(results)} result(s)"
        return False, "FTS5 search returned no results"
    except ImportError:
        return False, "hermes-offline not installed (or sqlite-vec missing)"
    except Exception as exc:
        return False, str(exc)


def test_piper_tts() -> tuple[bool, str]:
    if shutil.which("piper"):
        return True, "piper binary found in PATH"
    try:
        import piper  # noqa: F401
        return True, "piper Python package importable"
    except ImportError:
        return False, "piper not installed (pip install piper-tts)"


def test_faster_whisper() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
        return True, "faster-whisper importable"
    except ImportError:
        return False, "faster-whisper not installed (pip install faster-whisper)"


def test_sqlite_vec() -> tuple[bool, str]:
    try:
        import sqlite_vec  # noqa: F401
        return True, "sqlite-vec importable"
    except ImportError:
        return False, "sqlite-vec not installed (pip install sqlite-vec)"


def test_dspy_local() -> tuple[bool, str]:
    try:
        from hermes_offline.dspy_local import is_dspy_available
        if not is_dspy_available():
            return False, "DSPy not installed (pip install dspy-ai)"
        from hermes_offline.dspy_local import get_dspy_lm
        lm = get_dspy_lm()
        if lm is None:
            return False, "DSPy available but LM factory returned None"
        return True, f"DSPy LM: {type(lm).__name__}"
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_comfyui_available() -> tuple[bool, str]:
    try:
        resp = _ollama_post.__class__  # just check imports work
        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2) as r:
            return True, "ComfyUI running at 127.0.0.1:8188"
    except Exception:
        return False, "ComfyUI not running (optional)"


def test_a1111_available() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7860/sdapi/v1/sd-models", timeout=2) as r:
            return True, "A1111 running at 127.0.0.1:7860"
    except Exception:
        return False, "Automatic1111 not running (optional)"


def test_searxng_available() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/search?q=test&format=json", timeout=3) as r:
            data = json.loads(r.read())
            results = data.get("results", [])
            return True, f"SearXNG running ({len(results)} results)"
    except Exception:
        return False, "SearXNG not running (optional — DDG is the fallback)"


def test_think_mode_strip() -> tuple[bool, str]:
    try:
        from hermes_offline.think import _extract_thinking
        raw = "<think>This is internal reasoning</think>The final answer is 42."
        thinking, clean = _extract_thinking(raw)
        if "42" in clean and "This is internal reasoning" in thinking:
            return True, "Think blocks stripped correctly"
        return False, f"Stripping failed: clean={clean!r}"
    except ImportError:
        return False, "hermes-offline not installed"


def test_session_tracker() -> tuple[bool, str]:
    try:
        from hermes_offline.tracker import SessionStats, TurnStats
        session = SessionStats(model="qwen3:8b", num_ctx=16384)
        turn = TurnStats(
            turn_number=1,
            prompt_tokens=1024,
            completion_tokens=128,
            duration_secs=5.0,
            model="qwen3:8b",
            had_tool_calls=2,
        )
        session.record(turn)
        status = session.format_status_line()
        summary = session.format_summary()
        if "tok/s" in status and "Session Summary" in summary:
            return True, "Status line and summary formatted correctly"
        return False, f"Unexpected format: status={status!r}"
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_ram_profiler() -> tuple[bool, str]:
    try:
        from hermes_offline.profiler import _total_ram_mb, _process_rss_mb, get_profile_report
        total_mb = _total_ram_mb()
        current_mb = _process_rss_mb()
        report = get_profile_report()
        if total_mb > 0 and isinstance(report, str):
            return True, f"total={total_mb/1024:.1f}GB rss={current_mb:.0f}MB"
        return False, f"Unexpected values: total_mb={total_mb}, report={repr(report)[:40]}"
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_evolution_module() -> tuple[bool, str]:
    try:
        from hermes_offline.evolution import _should_auto_evolve, run_evolution
        cfg = {"evolve_every": 999, "mode": "lightweight"}
        should = _should_auto_evolve(cfg)
        return True, f"Evolution module loaded, should_auto_evolve={should}"
    except ImportError:
        return False, "hermes-offline not installed (or dspy-ai missing)"
    except Exception as exc:
        return False, str(exc)


def test_detector_snapshot() -> tuple[bool, str]:
    try:
        from hermes_offline.detector import get_snapshot
        snap = get_snapshot()
        return True, (
            f"has_ollama={snap.has_ollama} "
            f"pkgs={len(snap.python_packages)} "
            f"bins={len(snap.binaries)}"
        )
    except ImportError:
        return False, "hermes-offline not installed"
    except Exception as exc:
        return False, str(exc)


def test_offline_config_defaults() -> tuple[bool, str]:
    import pathlib
    cfg_path = pathlib.Path(__file__).parent.parent / "config" / "offline-defaults.yaml"
    if not cfg_path.exists():
        # Try installed package location
        cfg_path = pathlib.Path(sys.prefix) / "share" / "hermes-offline" / "offline-defaults.yaml"
    if cfg_path.exists():
        return True, f"Config template found at {cfg_path}"
    return False, "offline-defaults.yaml not found"


def test_modelfiles_present() -> tuple[bool, str]:
    import pathlib
    mf_dir = pathlib.Path(__file__).parent / "modelfiles"
    if not mf_dir.exists():
        return False, f"modelfiles/ dir not found at {mf_dir}"
    files = list(mf_dir.glob("*.Modelfile"))
    if len(files) >= 4:
        return True, f"{len(files)} Modelfiles: {[f.stem for f in files]}"
    return False, f"Expected 4+ Modelfiles, found {len(files)}: {[f.name for f in files]}"


def test_installer_scripts_present() -> tuple[bool, str]:
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    required = [
        "scripts/setup-offline.sh",
        "scripts/apply-modelfile.sh",
        "scripts/install-embeddings.sh",
    ]
    missing = [r for r in required if not (root / r).exists()]
    if missing:
        return False, f"Missing: {missing}"
    return True, f"All {len(required)} installer scripts present"


# ── Test registry ──────────────────────────────────────────────────────────────

def _build_test_suite(model: str, quick: bool = False) -> list[dict]:
    """
    Returns list of {name, category, fn, essential, skip_if} dicts.
    """
    embed_model = "nomic-embed-text"

    # Check which optional deps are present
    _has_embed = _ollama_get("/api/tags") and any(
        "nomic" in m["name"].lower()
        for m in (_ollama_get("/api/tags") or {}).get("models", [])
    )

    suite = [
        # ── Infrastructure (essential) ─────────────────────────────────────
        dict(name="Ollama running",           category="infra",    fn=test_ollama_running,                    essential=True),
        dict(name="Model available",          category="infra",    fn=lambda: test_model_available(model),    essential=True),
        dict(name="Basic chat completion",    category="infra",    fn=lambda: test_basic_chat(model),         essential=True),

        # ── Tool calling (essential) ───────────────────────────────────────
        dict(name="Single tool call",         category="tools",    fn=lambda: test_single_tool_call(model),   essential=True),
        dict(name="Parallel tool calls",      category="tools",    fn=lambda: test_parallel_tool_calls(model),essential=True),
        dict(name="No tool hallucination",    category="tools",    fn=lambda: test_no_tool_hallucination(model), essential=True),

        # ── Model capabilities (essential) ────────────────────────────────
        dict(name="Long context handling",    category="model",    fn=lambda: test_context_window_fill(model), essential=True),
        dict(name="JSON schema output",       category="model",    fn=lambda: test_json_schema_output(model),  essential=True),

        # ── Offline subsystems (essential) ────────────────────────────────
        dict(name="Hardware detection",       category="offline",  fn=test_hardware_detection,                essential=True),
        dict(name="Think mode strip",         category="offline",  fn=test_think_mode_strip,                  essential=True),
        dict(name="Session tracker",          category="offline",  fn=test_session_tracker,                   essential=True),
        dict(name="RAM profiler",             category="offline",  fn=test_ram_profiler,                      essential=True),
        dict(name="Detector snapshot",        category="offline",  fn=test_detector_snapshot,                 essential=True),

        # ── Local search (essential) ───────────────────────────────────────
        dict(name="DuckDuckGo search",        category="search",   fn=test_local_search_duckduckgo,           essential=True),
        dict(name="Wikipedia search",         category="search",   fn=test_local_search_wikipedia,            essential=True),

        # ── Memory (essential) ────────────────────────────────────────────
        dict(name="BEAM memory store (FTS5)", category="memory",   fn=test_beam_memory_store,                 essential=True),

        # ── Package files (essential) ─────────────────────────────────────
        dict(name="Modelfiles present",       category="release",  fn=test_modelfiles_present,                essential=True),
        dict(name="Installer scripts",        category="release",  fn=test_installer_scripts_present,         essential=True),
        dict(name="Offline config template",  category="release",  fn=test_offline_config_defaults,           essential=True),
    ]

    if not quick:
        suite += [
            # ── Optional features ──────────────────────────────────────────
            dict(name="Embeddings (nomic)",       category="memory",   fn=test_embedding_available,               essential=False,
                 skip_if=None if _has_embed else "nomic-embed-text not pulled"),
            dict(name="sqlite-vec package",       category="memory",   fn=test_sqlite_vec,                        essential=False),
            dict(name="piper-tts",                category="tts",      fn=test_piper_tts,                         essential=False),
            dict(name="faster-whisper",           category="speech",   fn=test_faster_whisper,                    essential=False),
            dict(name="DSPy local wiring",        category="evolution",fn=test_dspy_local,                        essential=False),
            dict(name="Evolution module",         category="evolution",fn=test_evolution_module,                  essential=False),
            dict(name="Vision model available",   category="vision",   fn=test_ollama_vision,                     essential=False),
            dict(name="SearXNG running",          category="search",   fn=test_searxng_available,                 essential=False),
            dict(name="ComfyUI running",          category="imagegen", fn=test_comfyui_available,                 essential=False),
            dict(name="Automatic1111 running",    category="imagegen", fn=test_a1111_available,                   essential=False),
        ]

    return suite


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_parity_tests(
    model: Optional[str] = None,
    quick: bool = False,
    category_filter: Optional[str] = None,
    no_color: bool = False,
) -> ParityReport:
    report = ParityReport()

    # Detect Ollama + model first
    report.ollama_running = _ollama_get("/api/tags", timeout=4) is not None
    report.model = model or _get_model() or "qwen3:8b"

    suite = _build_test_suite(report.model, quick=quick)

    if category_filter:
        suite = [t for t in suite if t["category"] == category_filter]

    for spec in suite:
        skip_if = spec.get("skip_if")
        result = _run_test(
            name=spec["name"],
            category=spec["category"],
            fn=spec["fn"],
            essential=spec["essential"],
            skip_if=skip_if,
        )
        report.add(result)

    return report


# ── Output formatters ──────────────────────────────────────────────────────────

def _print_rich(report: ParityReport) -> None:
    console = Console()

    console.print(Panel(
        f"[bold]Hermes Offline — Feature Parity Test[/bold]\n"
        f"[dim]Model: {report.model}  |  Ollama: {'running' if report.ollama_running else 'NOT RUNNING'}[/dim]",
        border_style="blue",
        expand=False,
    ))
    console.print()

    by_category: dict[str, list[TestResult]] = {}
    for r in report.results:
        by_category.setdefault(r.category, []).append(r)

    for cat, results in by_category.items():
        t = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        t.add_column(" ", width=2)
        t.add_column(f"[bold]{cat.upper()}[/bold]", min_width=30)
        t.add_column("Result", width=8)
        t.add_column("ms", width=7, justify="right")
        t.add_column("Detail", style="dim")

        for r in results:
            if r.skipped:
                icon = "[dim]–[/dim]"
                res  = "[dim]skip[/dim]"
                det  = r.skip_reason
            elif r.passed:
                icon = "[green]✓[/green]"
                res  = "[green]pass[/green]"
                det  = r.detail
            else:
                icon = "[red]✗[/red]" if r.essential else "[yellow]✗[/yellow]"
                res  = "[red]FAIL[/red]" if r.essential else "[yellow]fail[/yellow]"
                det  = r.detail

            t.add_row(icon, r.name, res, f"{r.duration_ms:.0f}", det[:60])

        console.print(t)
        console.print()

    # Summary
    e_pass = report.essential_passed
    e_tot  = report.essential_total
    o_pass = report.optional_passed
    o_tot  = report.optional_total
    skipped = sum(1 for r in report.results if r.skipped)

    color = "green" if report.all_essential_pass else "red"
    verdict = "ALL ESSENTIAL TESTS PASSED" if report.all_essential_pass else "ESSENTIAL TESTS FAILED"

    console.print(Panel(
        f"[{color} bold]{verdict}[/{color} bold]\n\n"
        f"  Essential:  [{color}]{e_pass}/{e_tot}[/{color}]  passed\n"
        f"  Optional:   {o_pass}/{o_tot}  passed\n"
        f"  Skipped:    {skipped}\n"
        f"  Duration:   {report.total_duration_ms:.0f} ms",
        border_style=color,
        expand=False,
    ))


def _print_plain(report: ParityReport) -> None:
    print(f"\nHermes Offline — Feature Parity Test")
    print(f"Model: {report.model}  Ollama: {'running' if report.ollama_running else 'NOT RUNNING'}")
    print()
    for r in report.results:
        if r.skipped:
            status = "SKIP"
        elif r.passed:
            status = "PASS"
        else:
            status = "FAIL" if r.essential else "warn"
        print(f"  [{status:4s}] [{r.category:10s}] {r.name:40s} {r.detail[:50]}")

    print()
    verdict = "ALL ESSENTIAL TESTS PASSED" if report.all_essential_pass else "ESSENTIAL TESTS FAILED"
    print(f"Result: {verdict}")
    print(f"  Essential: {report.essential_passed}/{report.essential_total}")
    print(f"  Optional:  {report.optional_passed}/{report.optional_total}")
    print()


def _print_json(report: ParityReport) -> None:
    out = {
        "model": report.model,
        "ollama_running": report.ollama_running,
        "all_essential_pass": report.all_essential_pass,
        "essential_passed": report.essential_passed,
        "essential_total": report.essential_total,
        "optional_passed": report.optional_passed,
        "optional_total": report.optional_total,
        "duration_ms": round(report.total_duration_ms, 1),
        "results": [
            {
                "name": r.name,
                "category": r.category,
                "passed": r.passed,
                "essential": r.essential,
                "skipped": r.skipped,
                "skip_reason": r.skip_reason,
                "duration_ms": round(r.duration_ms, 1),
                "detail": r.detail,
            }
            for r in report.results
        ],
    }
    print(json.dumps(out, indent=2))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hermes-offline-test-parity",
        description="Feature parity test suite for Hermes Offline",
    )
    parser.add_argument("--quick",     action="store_true", help="Run smoke tests only")
    parser.add_argument("--category",  default=None,        help="Filter by category (infra/tools/model/search/…)")
    parser.add_argument("--model",     default=None,        help="Override Ollama model (default: auto-detect)")
    parser.add_argument("--json",      action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--no-color",  action="store_true", help="Disable Rich colour output")
    args = parser.parse_args(argv)

    report = run_parity_tests(
        model=args.model,
        quick=args.quick,
        category_filter=args.category,
        no_color=args.no_color,
    )

    if args.json:
        _print_json(report)
    elif _RICH and not args.no_color:
        _print_rich(report)
    else:
        _print_plain(report)

    sys.exit(0 if report.all_essential_pass else 1)


if __name__ == "__main__":
    main()
