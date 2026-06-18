"""
Session cost tracker for Hermes Offline.

Intercepts every Ollama completion response to capture:
  - Prompt tokens used per turn and cumulative
  - Completion tokens generated per turn and cumulative
  - Tokens/second for each turn
  - Total session duration
  - Estimated RAM in use (based on model name + quant)
  - Number of tool calls made
  - Context window fill % (so you can see how close you are to the limit)

Output modes:
  1. Live status line — printed after each assistant turn (to stderr, below the
     Hermes output, so it doesn't interfere with streaming)
  2. Session summary — printed on exit (atexit hook)
  3. Rich footer — injected into Hermes TUI status bar when possible

Enable:
    hermes-offline --track              # enable tracker with status lines
    hermes-offline --track --no-status  # summary on exit only
    HERMES_TRACK=1 hermes-offline

Config (config.yaml):
    tracker:
      enabled: true
      status_line: true       # print after each turn
      summary_on_exit: true   # print on exit
      show_tui_footer: true   # try to inject into TUI footer
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# RAM estimates in GB for common Ollama model names.
# Format: (prefix_lower, q_tag_fragment) → GB
# These are conservative "loaded + overhead" values measured at typical usage.
_MODEL_RAM_TABLE: list[tuple[str, float]] = [
    ("qwen3:0.6b",        0.8),
    ("qwen3:1.7b",        1.5),
    ("qwen3:4b",          3.0),
    ("qwen3:8b",          5.5),
    ("qwen3:14b",         9.5),
    ("qwen3:30b",        20.0),
    ("qwen3-coder:30b",  20.0),
    ("qwen2.5-coder:7b",  5.0),
    ("qwen2.5-coder:14b", 9.5),
    ("qwen2.5-coder:32b", 21.0),
    ("llama3.2:1b",       1.2),
    ("llama3.2:3b",       2.4),
    ("llama3.1:8b",       5.5),
    ("llama3.1:70b",     43.0),
    ("phi4-mini",         3.0),
    ("phi4:14b",          9.5),
    ("mistral:7b",        5.0),
    ("gemma3:4b",         3.2),
    ("gemma3:12b",        8.5),
    ("nomic-embed-text",  0.3),
    ("smollm2:1.7b",      1.2),
]

# Q4_K_M is the default Ollama quant — no adjustment needed.
# Q8_0 uses ~1.7x more RAM than Q4.
_QUANT_MULTIPLIERS: list[tuple[str, float]] = [
    ("q8_0", 1.7),
    ("q8",   1.7),
    ("q6",   1.5),
    ("fp16", 2.0),
    ("f16",  2.0),
    ("q2",   0.6),
]


def estimate_model_ram_gb(model_name: str) -> float:
    """Return estimated RAM usage in GB for a given Ollama model name."""
    lower = model_name.lower()

    base_gb = 5.5  # default fallback (8B-class)
    for prefix, gb in _MODEL_RAM_TABLE:
        if lower.startswith(prefix):
            base_gb = gb
            break
        # Fuzzy: match by family name without tag
        family = prefix.split(":")[0]
        if lower.startswith(family) and ":" in lower:
            # Try to extract parameter count from tag
            tag = lower.split(":")[1]
            m = re.search(r"(\d+(?:\.\d+)?)b", tag)
            if m:
                params = float(m.group(1))
                base_gb = params * 0.7  # rough estimate: 0.7 GB per B params at Q4
            else:
                base_gb = gb
            break

    # Apply quantization multiplier if present in name
    for quant_tag, mult in _QUANT_MULTIPLIERS:
        if quant_tag in lower:
            base_gb *= mult
            break

    return round(base_gb, 1)


@dataclass
class TurnStats:
    """Statistics for a single model turn."""
    turn_number: int
    prompt_tokens: int
    completion_tokens: int
    duration_secs: float
    model: str
    had_tool_calls: int = 0
    think_chars: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_sec(self) -> float:
        if self.duration_secs <= 0:
            return 0.0
        return self.completion_tokens / self.duration_secs


@dataclass
class SessionStats:
    """Cumulative session statistics."""
    start_time: float = field(default_factory=time.time)
    turns: list[TurnStats] = field(default_factory=list)
    model: str = "unknown"
    num_ctx: int = 0            # context window size from config

    def record(self, turn: TurnStats) -> None:
        self.turns.append(turn)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(t.prompt_tokens for t in self.turns)

    @property
    def total_completion_tokens(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_tool_calls(self) -> int:
        return sum(t.had_tool_calls for t in self.turns)

    @property
    def elapsed_secs(self) -> float:
        return time.time() - self.start_time

    @property
    def avg_tps(self) -> float:
        speeds = [t.tokens_per_sec for t in self.turns if t.tokens_per_sec > 0]
        return sum(speeds) / len(speeds) if speeds else 0.0

    @property
    def context_fill_pct(self) -> float:
        """Latest prompt tokens as % of context window."""
        if not self.num_ctx or not self.turns:
            return 0.0
        return min(self.turns[-1].prompt_tokens / self.num_ctx * 100, 100.0)

    @property
    def ram_gb(self) -> float:
        return estimate_model_ram_gb(self.model)

    def format_status_line(self) -> str:
        """One-line status for after each turn."""
        t = self.turns[-1] if self.turns else None
        if not t:
            return ""

        ctx_fill = self.context_fill_pct
        ctx_str = f"{ctx_fill:.0f}% ctx" if self.num_ctx else ""
        tool_str = f" · {t.had_tool_calls} tool{'s' if t.had_tool_calls != 1 else ''}" if t.had_tool_calls else ""
        think_str = f" · 💭{t.think_chars}c" if t.think_chars else ""
        ram_str = f" · {self.ram_gb}GB RAM"

        return (
            f"  ↳ turn {t.turn_number} · "
            f"{t.completion_tokens} tok out · "
            f"{t.tokens_per_sec:.1f} tok/s"
            f"{tool_str}{think_str} · "
            f"ctx {t.prompt_tokens:,}/{self.num_ctx:,} ({ctx_fill:.0f}%)"
            f"{ram_str} · "
            f"session {_fmt_duration(self.elapsed_secs)}"
        )

    def format_summary(self) -> str:
        """Multi-line session summary for exit."""
        ram = self.ram_gb
        elapsed = self.elapsed_secs

        lines = [
            "",
            "─" * 56,
            "  Hermes Offline — Session Summary",
            "─" * 56,
            f"  Model:         {self.model}",
            f"  RAM in use:    ~{ram} GB",
            f"  Session time:  {_fmt_duration(elapsed)}",
            f"  Turns:         {len(self.turns)}",
            f"  Tool calls:    {self.total_tool_calls}",
            f"  Prompt tokens: {self.total_prompt_tokens:,}",
            f"  Output tokens: {self.total_completion_tokens:,}",
            f"  Total tokens:  {self.total_tokens:,}",
            f"  Avg speed:     {self.avg_tps:.1f} tok/s",
        ]

        if self.num_ctx:
            lines.append(f"  Final ctx use: {self.context_fill_pct:.0f}% of {self.num_ctx:,}")

        lines.append("─" * 56)
        lines.append("")
        return "\n".join(lines)


def _fmt_duration(secs: float) -> str:
    """Format seconds as human-readable duration."""
    if secs < 60:
        return f"{secs:.0f}s"
    m, s = divmod(int(secs), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# Module-level singleton
_session: Optional[SessionStats] = None


def get_session() -> SessionStats:
    global _session
    if _session is None:
        _session = SessionStats()
    return _session


class TrackingTransportWrapper:
    """
    Wraps an OpenAI-compatible client to capture usage stats from every
    chat completion response.
    """

    def __init__(
        self,
        wrapped: Any,
        session: SessionStats,
        status_line: bool = True,
    ):
        self._wrapped = wrapped
        self._session = session
        self._status_line = status_line

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._wrapped, name)
        if name == "chat":
            return _TrackingChat(attr, self._session, self._status_line)
        return attr


class _TrackingChat:
    def __init__(self, chat: Any, session: SessionStats, status_line: bool):
        self._chat = chat
        self._session = session
        self._status_line = status_line

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._chat, name)
        if name == "completions":
            return _TrackingCompletions(attr, self._session, self._status_line)
        return attr


class _TrackingCompletions:
    def __init__(self, completions: Any, session: SessionStats, status_line: bool):
        self._completions = completions
        self._session = session
        self._status_line = status_line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)

    def create(self, **kwargs: Any) -> Any:
        t_start = time.time()
        response = self._completions.create(**kwargs)
        duration = time.time() - t_start

        # Extract model from response or kwargs
        model = getattr(response, "model", None) or kwargs.get("model", "unknown")
        if self._session.model == "unknown" and model != "unknown":
            self._session.model = model
            # Also update RAM estimate now that we know the model
            if not self._session.num_ctx:
                # Try to read num_ctx from config
                try:
                    from hermes_cli.config import load_config
                    cfg = load_config()
                    ollama_opts = cfg.get("ollama_options", {})
                    if isinstance(ollama_opts, dict):
                        self._session.num_ctx = int(ollama_opts.get("num_ctx", 0))
                except Exception:
                    pass

        # Extract usage
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Count tool calls
        tool_calls_count = 0
        try:
            msg = response.choices[0].message
            tc = getattr(msg, "tool_calls", None)
            if tc:
                tool_calls_count = len(tc)
        except (AttributeError, IndexError):
            pass

        # Count think chars already stripped (tracked via env var set by think.py)
        think_chars = int(os.environ.get("_HERMES_LAST_THINK_CHARS", "0"))
        os.environ.pop("_HERMES_LAST_THINK_CHARS", None)

        turn = TurnStats(
            turn_number=len(self._session.turns) + 1,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_secs=duration,
            model=model,
            had_tool_calls=tool_calls_count,
            think_chars=think_chars,
        )
        self._session.record(turn)

        if self._status_line and completion_tokens > 0:
            _print_status(self._session.format_status_line())

        return response


def _print_status(line: str) -> None:
    """Print status to stderr (doesn't interfere with hermes stdout streaming)."""
    try:
        from rich.console import Console
        Console(stderr=True).print(f"[dim]{line}[/dim]")
    except ImportError:
        import sys
        print(line, file=sys.stderr)


def _register_exit_summary(session: SessionStats) -> None:
    """Register atexit hook to print session summary."""
    def _on_exit() -> None:
        if session.turns:
            try:
                from rich.console import Console
                Console(stderr=True).print(session.format_summary())
            except ImportError:
                import sys
                print(session.format_summary(), file=sys.stderr)
    atexit.register(_on_exit)


def patch_transport(
    status_line: bool = True,
    summary_on_exit: bool = True,
) -> bool:
    """
    Patch the hermes_cli openai_chat transport to track usage stats.
    Wraps every client it creates with TrackingTransportWrapper.

    Returns True if patch succeeded.
    """
    session = get_session()

    if summary_on_exit:
        _register_exit_summary(session)

    try:
        import agent.transports.openai_chat as transport_module

        def _wrap(fn: Any) -> Any:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                client = fn(*args, **kwargs)
                return TrackingTransportWrapper(client, session, status_line)
            wrapper._track_patched = True  # type: ignore[attr-defined]
            return wrapper

        patched = False
        for attr_name in ("_make_openai_client", "create_client", "_build_client"):
            fn = getattr(transport_module, attr_name, None)
            if fn and not getattr(fn, "_track_patched", False):
                setattr(transport_module, attr_name, _wrap(fn))
                patched = True
                logger.info("Patched tracker onto transport.%s", attr_name)

        return patched

    except ImportError as exc:
        logger.debug("Could not patch transport for tracking: %s", exc)
        return False


def apply_tracker(
    status_line: bool = True,
    summary_on_exit: bool = True,
) -> None:
    """Top-level entry point called from patch.py."""
    success = patch_transport(status_line=status_line, summary_on_exit=summary_on_exit)
    if success:
        logger.info("Session tracker active (status_line=%s, summary=%s)",
                    status_line, summary_on_exit)
