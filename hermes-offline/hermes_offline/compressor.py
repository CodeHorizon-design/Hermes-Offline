"""
Context compression hardening for hermes-offline.

Two problems with hermes's default compression on local hardware:

  1. THRESHOLD — compresses at 85% context fill. Local LLMs degrade
     significantly around 70-75%, so we trigger earlier.

  2. AUXILIARY LLM — the compressor calls an OpenAI-compatible API to
     summarize. On cloud builds this is a cheap fast model (gpt-4o-mini,
     claude-haiku). We redirect this to local Ollama so compression
     stays fully offline.

What this module does
─────────────────────
  _patch_threshold()          Lower all known threshold constants to 0.70
  _patch_auxiliary_client()   Wire the compressor's LLM client to Ollama
  _patch_summary_budget()     Scale summary token budget to local model limits
  _patch_pre_compress_hook()  Extract memories before messages are discarded

Threshold ladder (what fires and when):
  < 70%   full context available, no compression needed
  70-85%  hermes-offline triggers compression (patched threshold)
  85%+    default hermes would trigger (too late for local models)
  95%+    emergency: last-resort truncation regardless of any threshold

Auxiliary compression model selection:
  Prefers a small fast model for summarization (not the main chat model).
  On 4 GB machines: qwen3:1.7b
  On 8 GB machines: qwen3:4b
  Falls back to the main configured model if nothing smaller is available.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get(
    "OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"
)

# Which models are acceptable for fast local summarization (ordered by preference)
_COMPRESS_MODEL_CANDIDATES = [
    "qwen3:1.7b",
    "qwen3:4b",
    "llama3.2:3b",
    "phi3:mini",
    "gemma3:1b",
]

# Target threshold — fire compression before context gets dangerously full
OFFLINE_COMPRESS_THRESHOLD = float(
    os.environ.get("HERMES_OFFLINE_COMPRESS_THRESHOLD", "0.70")
)


# ── Threshold patching ────────────────────────────────────────────────────────

def _patch_threshold() -> bool:
    """
    Lower all known compression-threshold attributes across hermes modules.
    Returns True if at least one attribute was successfully patched.
    """
    patched = 0

    # Primary location: agent/context_compressor.py
    try:
        import agent.context_compressor as cc
        for attr in (
            "COMPRESSION_THRESHOLD",
            "COMPRESS_AT_FRACTION",
            "_COMPRESS_AT",
            "CONTEXT_COMPRESS_THRESHOLD",
            "AUTO_COMPRESS_THRESHOLD",
        ):
            if hasattr(cc, attr):
                current = getattr(cc, attr)
                if isinstance(current, float) and current > OFFLINE_COMPRESS_THRESHOLD:
                    setattr(cc, attr, OFFLINE_COMPRESS_THRESHOLD)
                    logger.info(
                        "Patched %s.%s: %.2f → %.2f",
                        "context_compressor", attr, current, OFFLINE_COMPRESS_THRESHOLD,
                    )
                    patched += 1
    except ImportError:
        pass

    # Secondary location: agent/conversation_compression.py
    try:
        import agent.conversation_compression as cv
        for attr in ("COMPRESSION_THRESHOLD", "COMPRESS_AT_FRACTION"):
            if hasattr(cv, attr):
                current = getattr(cv, attr)
                if isinstance(current, float) and current > OFFLINE_COMPRESS_THRESHOLD:
                    setattr(cv, attr, OFFLINE_COMPRESS_THRESHOLD)
                    patched += 1
    except ImportError:
        pass

    # Tertiary: agent/context_engine.py context management
    try:
        import agent.context_engine as ce
        for attr in ("COMPRESSION_THRESHOLD", "COMPRESS_FRACTION"):
            if hasattr(ce, attr):
                current = getattr(ce, attr)
                if isinstance(current, float) and current > OFFLINE_COMPRESS_THRESHOLD:
                    setattr(ce, attr, OFFLINE_COMPRESS_THRESHOLD)
                    patched += 1
    except ImportError:
        pass

    if not patched:
        logger.debug("No compression threshold attributes found to patch")
    return patched > 0


# ── Auxiliary LLM wiring ──────────────────────────────────────────────────────

def _select_compression_model(available_models: list[str]) -> Optional[str]:
    """
    Pick the best available small model for summarization.
    Returns None if no candidate is available (falls back to main model).
    """
    for candidate in _COMPRESS_MODEL_CANDIDATES:
        base = candidate.split(":")[0].lower()
        for m in available_models:
            if m.lower().startswith(base):
                return m
    return None


def _get_available_ollama_models() -> list[str]:
    import urllib.request, json
    try:
        base = OLLAMA_BASE.rstrip("/v1").rstrip("/")
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def _patch_auxiliary_client() -> bool:
    """
    Redirect the compressor's auxiliary LLM client to local Ollama.

    The compressor uses `agent.auxiliary_client.call_llm()` which in turn
    builds an OpenAI client. We patch the client factory so it points at
    Ollama instead of the cloud endpoint.
    """
    try:
        import agent.auxiliary_client as ac

        if getattr(ac, "_offline_patched", False):
            return True

        # Find a good small local model for compression
        available = _get_available_ollama_models()
        compress_model = _select_compression_model(available)
        if not compress_model and available:
            compress_model = available[0]   # use whatever is pulled
        if not compress_model:
            logger.debug("No local model available for auxiliary compression client")
            return False

        original_call_llm = getattr(ac, "call_llm", None)
        if original_call_llm is None:
            return False

        def _offline_call_llm(
            messages,
            model=None,
            *args,
            **kwargs,
        ):
            """Redirect compression LLM calls to local Ollama."""
            # Always use the local compress model
            kwargs.pop("api_key", None)
            kwargs.pop("base_url", None)
            try:
                from openai import OpenAI
                client = OpenAI(
                    base_url=OLLAMA_BASE,
                    api_key="ollama",
                    timeout=60,
                )
                resp = client.chat.completions.create(
                    model=compress_model,
                    messages=messages,
                    max_tokens=kwargs.get("max_tokens", 1024),
                    temperature=kwargs.get("temperature", 0.2),
                    stream=False,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                logger.warning(
                    "Local compression LLM failed (%s), falling back to original", exc
                )
                return original_call_llm(messages, model=model, *args, **kwargs)

        _offline_call_llm.__doc__ = (
            f"hermes-offline: redirected to local Ollama ({compress_model})"
        )
        ac.call_llm = _offline_call_llm
        ac._offline_patched = True
        logger.info(
            "Auxiliary compression LLM → local Ollama (%s)", compress_model
        )
        return True

    except ImportError:
        logger.debug("agent.auxiliary_client not available")
        return False


# ── Summary token budget ──────────────────────────────────────────────────────

def _patch_summary_budget() -> None:
    """
    Local models produce shorter, more focused summaries than cloud models.
    Lower the maximum summary token budget to match.

    Default in hermes: typically 2000-4000 tokens.
    Our target: 512-1024 tokens (sufficient for local agentic context).
    """
    try:
        import agent.context_compressor as cc
        for attr in ("MAX_SUMMARY_TOKENS", "SUMMARY_MAX_TOKENS", "_SUMMARY_BUDGET"):
            if hasattr(cc, attr):
                current = getattr(cc, attr)
                if isinstance(current, int) and current > 1024:
                    setattr(cc, attr, 1024)
                    logger.info(
                        "Patched summary budget: %s → 1024 tokens", current
                    )
    except ImportError:
        pass


# ── Pre-compress memory extraction ───────────────────────────────────────────

def _patch_pre_compress_hook() -> None:
    """
    Hook into compress_context() to extract important facts into BEAM memory
    before messages are discarded. This way even compressed-away context
    can be recalled via semantic search in future turns.
    """
    try:
        import agent.conversation_compression as cv

        original_compress = getattr(cv, "compress_context", None)
        if original_compress is None:
            return
        if getattr(original_compress, "_offline_pre_compress_patched", False):
            return

        def _patched_compress(agent_self, *args, **kwargs):
            # Extract messages about to be compressed
            _run_pre_compress_extraction(agent_self)
            return original_compress(agent_self, *args, **kwargs)

        _patched_compress._offline_pre_compress_patched = True
        cv.compress_context = _patched_compress
        logger.debug("Pre-compress memory extraction hook installed")

    except (ImportError, AttributeError):
        pass


def _run_pre_compress_extraction(agent_self: Any) -> None:
    """Extract key facts from messages before they get compressed away."""
    try:
        from hermes_offline.memory import SqliteVecMemoryProvider
        memory_manager = getattr(agent_self, "_memory_manager", None)
        if memory_manager is None:
            return

        messages = getattr(agent_self, "messages", []) or []
        # Find our provider and call on_pre_compress
        providers = getattr(memory_manager, "_providers", [])
        for provider in providers:
            if isinstance(provider, SqliteVecMemoryProvider):
                provider.on_pre_compress(messages)
                break
    except Exception as exc:
        logger.debug("Pre-compress extraction failed: %s", exc)


# ── Emergency tail truncation ─────────────────────────────────────────────────

def _patch_emergency_truncation() -> None:
    """
    Last-resort safety net: if context reaches 95%+ fill and compression
    fails (e.g. Ollama busy), drop the oldest non-system messages rather
    than crashing or hanging.
    """
    try:
        import agent.context_engine as ce

        original_check = getattr(ce, "check_context_pressure", None)
        if original_check is None:
            return
        if getattr(original_check, "_offline_patched", False):
            return

        def _patched_check(messages, model, num_ctx, *args, **kwargs):
            result = original_check(messages, model, num_ctx, *args, **kwargs)
            # If fill > 95% and we couldn't compress, force-drop oldest turns
            try:
                fill = _estimate_fill(messages, num_ctx)
                if fill > 0.95 and len(messages) > 4:
                    to_drop = max(1, len(messages) // 6)
                    # Keep system message (index 0), drop oldest non-system
                    non_sys = [m for m in messages if m.get("role") != "system"]
                    to_remove = {id(m) for m in non_sys[:to_drop]}
                    messages[:] = [m for m in messages if id(m) not in to_remove]
                    logger.warning(
                        "Emergency truncation: dropped %d oldest messages (%.0f%% fill)",
                        to_drop, fill * 100,
                    )
            except Exception:
                pass
            return result

        _patched_check._offline_patched = True
        ce.check_context_pressure = _patched_check

    except (ImportError, AttributeError):
        pass


def _estimate_fill(messages: list, num_ctx: int) -> float:
    if not num_ctx:
        return 0.0
    # Rough token estimate: 1 token ≈ 4 chars
    total_chars = sum(
        len(str(m.get("content", ""))) for m in messages
    )
    return (total_chars / 4) / num_ctx


# ── Public apply function ─────────────────────────────────────────────────────

def apply_compression_patches() -> None:
    """Apply all compression hardening patches. Called from patch.py."""
    _patch_threshold()
    _patch_auxiliary_client()
    _patch_summary_budget()
    _patch_pre_compress_hook()
    _patch_emergency_truncation()
