"""
Smart tool output truncation for hermes-offline.

Local LLMs have tight context windows. Untruncated tool output — bash
commands printing 10,000 lines, grep matching 500 files, read_file on
a 3,000-line source — wastes context tokens that the model needs for
reasoning and tool calls.

Strategy: per-tool-type intelligent truncation
──────────────────────────────────────────────
  bash / terminal     head 60 lines  + "... [N lines] ..."  + tail 20 lines
  read_file           head 120 lines + "... [N lines] ..."  + tail 10 lines
  web_search          300 chars per result, max 5 results
  grep / search       head 40 matches + count summary
  default             first 1800 chars hard cap

Smart truncation preserves the most diagnostic information:
  - First lines: context, header, early output
  - Last lines: final output, error messages, exit codes
  - Middle marker: exact count so the model knows how much was cut

All limits are tunable via config.yaml (tool_output section) or env vars.
Zero impact on tool correctness — truncation only applies to the string
returned to the LLM, not to what's stored on disk.

Config:
  tool_output:
    max_chars: 2000          # default hard cap
    bash_head_lines: 60
    bash_tail_lines: 20
    file_head_lines: 120
    file_tail_lines: 10
    search_chars_per_result: 300
    search_max_results: 5
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Defaults (all overridable via config.yaml tool_output section) ────────────

_DEFAULTS: dict[str, Any] = {
    "max_chars":               int(os.environ.get("HERMES_OFFLINE_MAX_TOOL_CHARS", "2000")),
    "bash_head_lines":         int(os.environ.get("HERMES_TOOL_BASH_HEAD", "60")),
    "bash_tail_lines":         int(os.environ.get("HERMES_TOOL_BASH_TAIL", "20")),
    "file_head_lines":         int(os.environ.get("HERMES_TOOL_FILE_HEAD", "120")),
    "file_tail_lines":         int(os.environ.get("HERMES_TOOL_FILE_TAIL", "10")),
    "search_chars_per_result": int(os.environ.get("HERMES_TOOL_SEARCH_CHARS", "300")),
    "search_max_results":      int(os.environ.get("HERMES_TOOL_SEARCH_RESULTS", "5")),
    "grep_head_lines":         int(os.environ.get("HERMES_TOOL_GREP_HEAD", "40")),
}

_cfg_cache: Optional[dict] = None


def _get_cfg() -> dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache
    cfg = dict(_DEFAULTS)
    try:
        from hermes_cli.config import load_config
        user_cfg = load_config().get("tool_output", {})
        if isinstance(user_cfg, dict):
            for k, v in user_cfg.items():
                if k in cfg and isinstance(v, int) and v > 0:
                    cfg[k] = v
    except Exception:
        pass
    _cfg_cache = cfg
    return cfg


# ── Core truncation logic ─────────────────────────────────────────────────────

def _head_tail(text: str, head_lines: int, tail_lines: int) -> str:
    """
    Return head_lines from top + tail_lines from bottom with a count marker.
    If text fits within head+tail, return it unchanged.
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    keep = head_lines + tail_lines
    if total <= keep:
        return text

    dropped = total - keep
    head = "".join(lines[:head_lines])
    tail = "".join(lines[-tail_lines:]) if tail_lines else ""
    marker = f"\n... [{dropped} lines truncated] ...\n"
    return head + marker + tail


def _head_chars(text: str, max_chars: int) -> str:
    """Simple character cap with a trailing marker."""
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return text[:max_chars] + f"\n... [{dropped} chars truncated]"


def truncate_tool_output(
    tool_name: str,
    output: str,
    cfg: Optional[dict] = None,
) -> str:
    """
    Apply type-aware truncation to a tool's output string.

    Args:
        tool_name:  The hermes tool name (e.g. "bash", "read_file", "web_search")
        output:     Raw tool output string
        cfg:        Optional config override (uses cached config if None)

    Returns:
        Truncated string, or the original if it fits within limits.
    """
    if not output:
        return output

    c = cfg or _get_cfg()

    # Route by tool type
    name = tool_name.lower()

    if name in ("bash", "terminal", "run_command", "execute_command", "shell"):
        return _head_tail(output, c["bash_head_lines"], c["bash_tail_lines"])

    if name in ("read_file", "view_file", "read", "cat_file"):
        return _head_tail(output, c["file_head_lines"], c["file_tail_lines"])

    if name in ("grep", "search_files", "find_files", "ripgrep", "search"):
        return _head_tail(output, c["grep_head_lines"], 0)

    if name in ("web_search", "search_web", "brave_search", "search_internet"):
        return _truncate_search_results(output, c["search_chars_per_result"], c["search_max_results"])

    if name in ("memory", "recall", "remember", "knowledge_base"):
        return output  # Never truncate memory — it's already compact

    if name in ("list_directory", "ls", "dir"):
        return _head_chars(output, 1500)

    # Default: hard character cap
    return _head_chars(output, c["max_chars"])


def _truncate_search_results(output: str, chars_per: int, max_results: int) -> str:
    """
    Truncate web search output: limit chars per result and total results.
    Search results are typically JSON or numbered text blocks.
    """
    # Try to parse as structured result blocks (numbered [1], [2], ...)
    import re
    blocks = re.split(r"\n(?=\[\d+\])", output.strip())
    if len(blocks) > 1:
        trimmed = []
        for block in blocks[:max_results]:
            trimmed.append(block[:chars_per] + ("…" if len(block) > chars_per else ""))
        result = "\n".join(trimmed)
        if len(blocks) > max_results:
            result += f"\n... [{len(blocks) - max_results} more results truncated]"
        return result
    # Plain text fallback
    return _head_chars(output, chars_per * max_results)


# ── Patch hermes tool result pipeline ────────────────────────────────────────

def _patch_make_tool_result_message() -> bool:
    """
    Patch agent.tool_dispatch_helpers.make_tool_result_message to apply
    smart truncation before results are added to the message history.
    """
    try:
        import agent.tool_dispatch_helpers as tdh

        original_fn = getattr(tdh, "make_tool_result_message", None)
        if original_fn is None:
            return False
        if getattr(original_fn, "_offline_truncated", False):
            return True

        def _truncating_make_tool_result_message(
            tool_call_id: str,
            function_name: str,
            result: Any,
            *args,
            **kwargs,
        ) -> dict:
            # Truncate string results; leave dicts/lists for hermes to handle
            if isinstance(result, str):
                result = truncate_tool_output(function_name, result)
            elif isinstance(result, dict):
                # Truncate 'output', 'content', 'text' fields if present
                for key in ("output", "content", "text", "result", "stdout", "stderr"):
                    if key in result and isinstance(result[key], str):
                        result = dict(result)
                        result[key] = truncate_tool_output(function_name, result[key])
                        break
            return original_fn(tool_call_id, function_name, result, *args, **kwargs)

        _truncating_make_tool_result_message._offline_truncated = True
        tdh.make_tool_result_message = _truncating_make_tool_result_message
        logger.info("Smart tool output truncation patch applied")
        return True

    except ImportError:
        logger.debug("agent.tool_dispatch_helpers not available")
        return False


def _patch_tool_output_limit_constants() -> None:
    """
    Also lower the module-level constants in tools.tool_output_limits
    so hermes's own truncation code uses our tighter limits.
    """
    c = _get_cfg()
    try:
        import tools.tool_output_limits as tol

        pairs = [
            ("DEFAULT_MAX_BYTES",       c["max_chars"]),
            ("MAX_RESULT_SIZE_CHARS",    c["max_chars"]),
            ("DEFAULT_MAX_RESULT_SIZE_CHARS", c["max_chars"]),
            ("DEFAULT_MAX_LINES",        c["file_head_lines"] + c["file_tail_lines"]),
            ("MAX_LINES",                c["file_head_lines"] + c["file_tail_lines"]),
        ]
        for attr, new_val in pairs:
            if hasattr(tol, attr):
                current = getattr(tol, attr)
                if isinstance(current, int) and current > new_val:
                    setattr(tol, attr, new_val)
                    logger.debug("Patched %s: %d → %d", attr, current, new_val)

        # Invalidate the module's cache so the new values take effect
        if hasattr(tol, "_cached_limits"):
            tol._cached_limits = None

    except ImportError:
        pass


# ── Public apply function ─────────────────────────────────────────────────────

def apply_tool_stream_patches() -> None:
    """Apply all tool output patches. Called from patch.py."""
    _patch_tool_output_limit_constants()
    _patch_make_tool_result_message()
