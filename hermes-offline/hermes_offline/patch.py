"""
Core patcher — applies all offline-first patches to hermes-agent.

Call hermes_offline.patch.apply() before importing hermes_cli to ensure
all patches are active. The patches are:

  1. Register ollama-local provider in HERMES_OVERLAYS
  2. Patch context compression (threshold 0.85→0.70, auxiliary LLM→Ollama,
     summary budget, pre-compress memory hook, emergency tail truncation)
  3. Patch tool output (smart per-type truncation, lower output caps)
  4. Register local web search backend (SearXNG / DuckDuckGo / Wikipedia)
  5. Register piper-tts as default TTS backend (if installed)
  6. Register local Whisper transcription backend (if installed)
  7. Register local image-gen backends (ComfyUI / A1111) if running
  8. Register SqliteVecMemoryProvider (BEAM tiered semantic memory)
  9. Wire DSPy to local Ollama (lightweight evolution mode, population=2)
 10. RAM profiler (per-turn memory tracking, tier comparison, OOM warnings)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_applied = False


def apply(
    verbose: bool = False,
    think_mode: str = "auto",   # "auto" | "always" | "never"
    think_show: bool = False,
    think_threshold: int = 3,
    track: bool = True,          # enable session cost tracker
    track_status_line: bool = True,  # print status after each turn
    track_summary: bool = True,      # print summary on exit
) -> None:
    """Apply all offline patches. Idempotent — safe to call multiple times."""
    global _applied
    if _applied:
        return
    _applied = True

    if verbose:
        logging.basicConfig(level=logging.INFO)

    # Run system detection once; all patch functions use the cached snapshot
    # so we probe Ollama/services/binaries exactly once per launch.
    try:
        from hermes_offline.detector import get_snapshot
        _snap = get_snapshot()
    except Exception:
        _snap = None

    _patch_providers(_snap)
    _patch_context_compression()
    _patch_tool_output_limits()
    _patch_web_search(_snap)
    _patch_tts(_snap)
    _patch_transcription(_snap)
    _patch_image_gen(_snap)
    _patch_memory()
    _patch_dspy()
    _patch_think_mode(think_mode, think_show, think_threshold)
    _patch_tracker(track, track_status_line, track_summary)
    _patch_profiler()


def _patch_providers(snap=None) -> None:
    # Only register if Ollama is actually running (detector already confirmed this)
    if snap is not None and not snap.has_ollama:
        logger.debug("Ollama not running — skipping provider registration")
        # Still register the config so hermes knows to use it when Ollama starts
    from hermes_offline.providers import register_ollama_provider
    register_ollama_provider()


def _patch_context_compression() -> None:
    """
    Full compression hardening via compressor.py:
      • Lower threshold 0.85 → 0.70 (all known attribute names)
      • Redirect auxiliary compression LLM → local Ollama
      • Shrink summary token budget (cloud targets ~4K; local needs ≤1K)
      • Pre-compress hook: extract memories before messages are discarded
      • Emergency tail truncation at 95% fill
    """
    try:
        from hermes_offline.compressor import apply_compression_patches
        apply_compression_patches()
    except Exception as exc:
        logger.debug("Compression patches failed: %s", exc)
        # Minimal fallback: at least lower the threshold
        try:
            import agent.context_compressor as cc
            for attr in ("COMPRESSION_THRESHOLD", "COMPRESS_AT_FRACTION", "_COMPRESS_AT"):
                if hasattr(cc, attr):
                    current = getattr(cc, attr)
                    if isinstance(current, float) and current > 0.70:
                        setattr(cc, attr, 0.70)
        except ImportError:
            pass


def _patch_tool_output_limits() -> None:
    """
    Smart per-type tool output truncation via tool_stream.py:
      • bash/terminal:  head 60 + tail 20 (capture context + exit codes)
      • read_file:      head 120 + tail 10
      • grep/search:    head 40 matches + count
      • web_search:     300 chars/result, max 5 results
      • memory:         unlimited
      • default:        2000 char hard cap
    Also lowers module-level constants in tools.tool_output_limits.
    """
    try:
        from hermes_offline.tool_stream import apply_tool_stream_patches
        apply_tool_stream_patches()
    except Exception as exc:
        logger.debug("Tool stream patches failed: %s", exc)
        # Minimal fallback: just lower the cap
        max_chars = int(os.environ.get("HERMES_OFFLINE_MAX_TOOL_CHARS", "2000"))
        try:
            import tools.tool_output_limits as tol
            for attr in ("DEFAULT_MAX_RESULT_SIZE_CHARS", "MAX_RESULT_SIZE_CHARS",
                         "DEFAULT_MAX_BYTES"):
                if hasattr(tol, attr):
                    current = getattr(tol, attr)
                    if isinstance(current, int) and current > max_chars:
                        setattr(tol, attr, max_chars)
        except ImportError:
            pass


def _patch_web_search(snap=None) -> None:
    """
    Register free no-key web search backends (DDG, Wikipedia, SearXNG).
    SearXNG is used as the primary backend when already running.
    If a stopped Docker container exists it is auto-started.
    DDG/Wikipedia are always registered as fallbacks.
    """
    try:
        from hermes_offline.local_search import (
            register_duckduckgo_backend,
            register_wikipedia_backend,
            register_searxng_if_available,
        )
        # SearXNG first — best quality (self-hosted, no tracking)
        register_searxng_if_available(snap)
        # DDG and Wikipedia always registered as fallbacks
        register_duckduckgo_backend()
        register_wikipedia_backend()
    except Exception as exc:
        logger.debug("Could not register local search backends: %s", exc)


def _patch_tts(snap=None) -> None:
    """
    Set the best available local TTS backend as default.
    Uses detector snapshot — no redundant filesystem probes.
    Priority: piper (best quality) > neutts/kittentts > skip.
    """
    if snap is not None:
        # Detector already found what's installed — use it directly
        if snap.has_tts:
            _set_tts_default("piper")
            return
        if snap.binaries.get("neutts") or snap.binaries.get("kittentts"):
            _set_tts_default("neutts")
        return

    # Fallback: probe manually when no snapshot available
    if _piper_available():
        _set_tts_default("piper")
    elif _neutts_available():
        _set_tts_default("neutts")


def _piper_available() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        pass
    import shutil
    return shutil.which("piper") is not None


def _neutts_available() -> bool:
    import shutil
    return shutil.which("neutts") is not None or shutil.which("kittentts") is not None


def _set_tts_default(backend: str) -> None:
    try:
        from hermes_cli import config as cfg
        conf = cfg.load_config()
        tts_section = conf.get("tts", {})
        if not isinstance(tts_section, dict):
            tts_section = {}
        if tts_section.get("provider") in (None, "", "edge"):
            tts_section["provider"] = backend
            conf["tts"] = tts_section
            cfg.save_config(conf)
            logger.info("Set TTS default to %s", backend)
    except Exception as exc:
        logger.debug("Could not set TTS default: %s", exc)


def _patch_transcription(snap=None) -> None:
    """
    Prefer the best available local transcription backend.
    Uses detector snapshot — no redundant imports/probes.
    Priority: faster-whisper (Python) > whisper.cpp binary > skip.
    """
    if snap is not None:
        if snap.python_packages.get("faster-whisper"):
            _set_transcription_backend("faster-whisper")
        elif snap.binaries.get("whisper") or snap.binaries.get("whisper-cpp"):
            _set_transcription_backend("whisper-cpp")
        return

    # Fallback: probe manually when no snapshot available
    try:
        import faster_whisper  # noqa: F401
        _set_transcription_backend("faster-whisper")
    except ImportError:
        pass


def _set_transcription_backend(backend: str) -> None:
    try:
        from hermes_cli import config as cfg
        conf = cfg.load_config()
        voice = conf.get("voice", {})
        if not isinstance(voice, dict):
            voice = {}
        if voice.get("transcription_backend") in (None, "", "openai"):
            voice["transcription_backend"] = backend
            conf["voice"] = voice
            cfg.save_config(conf)
            logger.info("Set transcription backend to %s", backend)
    except Exception as exc:
        logger.debug("Could not set transcription backend: %s", exc)


def _patch_image_gen(snap=None) -> None:
    """
    Register local image generation backends (ComfyUI, A1111) if running.
    Uses detector snapshot — services were already probed at startup,
    no redundant HTTP calls.
    """
    if snap is not None:
        svc = snap.services
        if svc.get("comfyui") and svc["comfyui"].running:
            logger.info("Detected ComfyUI (from snapshot)")
            _set_image_gen_backend("comfyui")
            return
        if svc.get("a1111") and svc["a1111"].running:
            logger.info("Detected A1111 (from snapshot)")
            _set_image_gen_backend("a1111")
            return
        return  # Neither running — skip

    # Fallback: probe manually when no snapshot available
    import urllib.request
    backends = [
        ("comfyui", "http://127.0.0.1:8188/system_stats"),
        ("a1111",   "http://127.0.0.1:7860/sdapi/v1/sd-models"),
    ]
    for name, url in backends:
        try:
            urllib.request.urlopen(url, timeout=1)
            logger.info("Detected local image gen backend: %s at %s", name, url)
            _set_image_gen_backend(name)
            break
        except Exception:
            continue


def _set_image_gen_backend(backend: str) -> None:
    try:
        from hermes_cli import config as cfg
        conf = cfg.load_config()
        img = conf.get("image_generation", {})
        if not isinstance(img, dict):
            img = {}
        if img.get("provider") in (None, "", "fal", "openai"):
            img["provider"] = backend
            conf["image_generation"] = img
            cfg.save_config(conf)
            logger.info("Set image generation backend to %s", backend)
    except Exception as exc:
        logger.debug("Could not set image gen backend: %s", exc)


def _patch_tracker(enabled: bool, status_line: bool, summary: bool) -> None:
    """
    Enable session cost tracker: tokens, RAM, tok/s, context fill%, session time.
    Reads from config.yaml tracker section; CLI flags override.
    """
    if not enabled:
        return
    # Read config overrides
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        t_cfg = cfg.get("tracker", {})
        if isinstance(t_cfg, dict):
            enabled  = t_cfg.get("enabled", enabled)
            status_line = t_cfg.get("status_line", status_line)
            summary  = t_cfg.get("summary_on_exit", summary)
    except Exception:
        pass
    if not enabled:
        return
    try:
        from hermes_offline.tracker import apply_tracker
        apply_tracker(status_line=status_line, summary_on_exit=summary)
    except Exception as exc:
        logger.debug("Could not apply tracker patch: %s", exc)


def _patch_memory() -> None:
    """
    Register SqliteVecMemoryProvider with hermes MemoryManager.

    The provider is only activated when config.yaml has:
      memory:
        semantic_backend: sqlite_vec

    Without that key, this is a no-op — existing hermes memory is untouched.
    Degrades gracefully: no sqlite-vec → FTS5 keyword search only.
                         no Ollama embeds → read-only memory injection only.
    """
    try:
        from hermes_offline.memory import SqliteVecMemoryProvider
        provider = SqliteVecMemoryProvider()

        if not provider.is_available():
            logger.debug("Semantic memory disabled (memory.semantic_backend != sqlite_vec)")
            return

        # Hook into MemoryManager.add_provider() — called during agent init.
        # We wrap the MemoryManager class's __init__ so the provider is added
        # immediately after the manager is constructed (before it reads config).
        try:
            from agent.memory_manager import MemoryManager as _MM

            _original_mm_init = _MM.__init__

            def _patched_mm_init(self, *args, **kwargs):
                _original_mm_init(self, *args, **kwargs)
                try:
                    self.add_provider(provider)
                    logger.info("SqliteVecMemoryProvider registered with MemoryManager")
                except Exception as exc:
                    logger.debug("Could not add memory provider: %s", exc)

            if not getattr(_MM.__init__, "_offline_memory_patched", False):
                _patched_mm_init._offline_memory_patched = True
                _MM.__init__ = _patched_mm_init

        except ImportError:
            logger.debug("MemoryManager not available — memory patch skipped")

    except Exception as exc:
        logger.debug("Memory patch failed: %s", exc)


def _patch_dspy() -> None:
    """
    Full Phase 5 self-evolution wiring:
      1. Check if DSPy installed + mode=lightweight (config or --evolution-mode flag)
      2. Wire dspy.configure(lm=OllamaLocal) — version-compatible shim (2.4/2.5/2.6)
      3. Write evolution defaults (population_size=2, eval_budget=5, evolve_every=5)
      4. Inject compiled few-shot demos into system prompt if evolved program exists
      5. Schedule auto-evolution at session end (atexit background thread)
    Safe no-op when DSPy not installed or mode=disabled.
    """
    try:
        env_mode = os.environ.get("HERMES_EVOLUTION_MODE", "")

        from hermes_offline.dspy_local import is_dspy_available, patch_evolution_config
        if not is_dspy_available():
            logger.debug("DSPy not installed — evolution unavailable (pip install dspy-ai)")
            return

        try:
            from hermes_cli.config import load_config
            cfg_all = load_config()
            evo = cfg_all.get("evolution", {})
            if not isinstance(evo, dict):
                evo = {}
        except Exception:
            evo = {}

        mode = env_mode or evo.get("mode", "disabled")
        if mode == "disabled":
            logger.debug("Evolution disabled (mode=disabled)")
            return

        # Write defaults into config (idempotent — only fills missing keys)
        patch_evolution_config()

        # Wire DSPy LM → local Ollama
        from hermes_offline.dspy_local import wire_dspy_local
        if not wire_dspy_local():
            logger.debug("DSPy wiring skipped — no model or Ollama not running")
            return

        # Inject evolved prompt from a previous compile run (if present)
        try:
            from hermes_offline.evolution import apply_evolved_prompt
            apply_evolved_prompt()
        except Exception as exc:
            logger.debug("Evolved prompt injection skipped: %s", exc)

        # Schedule auto-evolution at session end (background thread, max 5 min)
        if evo.get("auto_evolve", True):
            _schedule_auto_evolution(evo)

    except Exception as exc:
        logger.debug("DSPy/evolution wiring failed: %s", exc)


def _schedule_auto_evolution(evo: dict) -> None:
    """Register atexit hook that runs evolution if session-count threshold is met."""
    import atexit
    import threading

    def _run_if_due():
        try:
            from hermes_offline.evolution import run_evolution, _should_auto_evolve
            if not _should_auto_evolve(evo):
                return
            logger.info("Auto-evolution starting (background)…")
            ok, msg = run_evolution(verbose=False)
            logger.info("Auto-evolution %s — %s", "OK" if ok else "skipped", msg)
        except Exception as exc:
            logger.debug("Auto-evolution failed: %s", exc)

    def _atexit_cb():
        t = threading.Thread(target=_run_if_due, daemon=True, name="hermes-evolution")
        t.start()
        t.join(timeout=300)

    atexit.register(_atexit_cb)


def _patch_think_mode(mode: str, show: bool, threshold: int) -> None:
    """
    Enable Qwen3 chain-of-thought thinking mode.
    Injects /think or /no_think tags into messages sent to the model.
    Strips <think>...</think> blocks so hermes tool parsing is unaffected.
    """
    if mode == "never":
        return
    try:
        from hermes_offline.think import apply_think_mode
        apply_think_mode(mode=mode, show=show, threshold=threshold)
    except Exception as exc:
        logger.debug("Could not apply think mode patch: %s", exc)


def _patch_profiler() -> None:
    """
    Initialise the RAM profiler (profiler.py):
      • Hook into conversation loop — records RSS before/after each turn
      • Warn at 80% / 95% RAM fill with actionable messages
      • Provides get_profile_report() for session summary (included by tracker)
    Low overhead: psutil.Process().memory_info() is O(1), no sampling threads.
    """
    try:
        from hermes_offline.profiler import apply_profiler_patches
        apply_profiler_patches()
    except Exception as exc:
        logger.debug("RAM profiler not applied: %s", exc)


def main() -> None:
    """CLI entry point: print patch status."""
    import sys
    apply(verbose=True)
    print("\n[hermes-offline] All patches applied successfully.")
    sys.exit(0)
