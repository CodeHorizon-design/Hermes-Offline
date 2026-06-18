"""
RAM profiler for hermes-offline.

Tracks memory usage during agent operation so users know exactly how
close they are to their hardware limits. Critical for 4-8 GB machines
where OOM-kills end sessions silently.

What it tracks
──────────────
  per-turn RSS delta      How much RAM each turn consumes
  peak RSS                Highest memory use during the session
  model footprint         Ollama process RAM (separate from Python)
  available headroom      How far from OOM (warns at 80% / 95%)
  per-tier comparison     Shows where you are vs 4 GB / 8 GB limits

Integration
───────────
  - Hooks into tracker.py's TurnRecord via monkey-patch
  - Logs warnings to stdout when approaching limits
  - Exposes get_profile_report() for the atexit summary
  - Optional: profile a single function call with @profile_call

Usage
─────
    from hermes_offline.profiler import apply_profiler_patches, get_profile_report
    apply_profiler_patches()          # call from patch.py
    report = get_profile_report()     # call at session end
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Warn when RAM usage hits these fractions of total system RAM
WARN_THRESHOLD  = float(os.environ.get("HERMES_RAM_WARN",  "0.80"))
CRIT_THRESHOLD  = float(os.environ.get("HERMES_RAM_CRIT",  "0.92"))

# Hardware tier reference points (GB) for the comparison table
_TIER_RAM_GB: dict[str, float] = {
    "ultra_low": 4.0,
    "low":       8.0,
    "mid":      12.0,
    "good":     16.0,
    "great":    24.0,
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TurnMemory:
    turn:           int
    rss_before_mb:  float
    rss_after_mb:   float
    ollama_rss_mb:  float = 0.0

    @property
    def delta_mb(self) -> float:
        return self.rss_after_mb - self.rss_before_mb


@dataclass
class MemoryProfile:
    turns:              list[TurnMemory] = field(default_factory=list)
    peak_rss_mb:        float = 0.0
    total_ram_mb:       float = 0.0
    warn_fired:         bool  = False
    crit_fired:         bool  = False
    _lock:              threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def current_rss_mb(self) -> float:
        return _process_rss_mb()

    @property
    def fill_fraction(self) -> float:
        if not self.total_ram_mb:
            return 0.0
        return self.current_rss_mb / self.total_ram_mb

    @property
    def available_mb(self) -> float:
        return max(0.0, self.total_ram_mb - self.current_rss_mb)

    def record_turn(self, turn: int, before_mb: float, after_mb: float, ollama_mb: float = 0.0) -> None:
        with self._lock:
            rec = TurnMemory(turn, before_mb, after_mb, ollama_mb)
            self.turns.append(rec)
            if after_mb > self.peak_rss_mb:
                self.peak_rss_mb = after_mb


_profile = MemoryProfile()


# ── System RAM measurement ────────────────────────────────────────────────────

def _process_rss_mb() -> float:
    """Current Python process RSS in MB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1_048_576
    except Exception:
        return 0.0


def _total_ram_mb() -> float:
    """Total system RAM in MB."""
    try:
        import psutil
        return psutil.virtual_memory().total / 1_048_576
    except Exception:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024
        except Exception:
            pass
        return 8192.0  # fallback: assume 8 GB


def _ollama_rss_mb() -> float:
    """Estimate Ollama process RAM via psutil (best-effort)."""
    try:
        import psutil
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                if "ollama" in (proc.info["name"] or "").lower():
                    mem = proc.info["memory_info"]
                    if mem:
                        return mem.rss / 1_048_576
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    return 0.0


# ── Threshold warnings ────────────────────────────────────────────────────────

def _check_thresholds() -> Optional[str]:
    """Return a warning string if memory is critically low, else None."""
    total = _profile.total_ram_mb or _total_ram_mb()
    current = _process_rss_mb() + _ollama_rss_mb()
    fraction = current / total if total else 0

    if fraction >= CRIT_THRESHOLD and not _profile.crit_fired:
        _profile.crit_fired = True
        avail = total - current
        return (
            f"\n⚠  CRITICAL: RAM at {fraction*100:.0f}% "
            f"({current/1024:.1f}/{total/1024:.1f} GB) — "
            f"{avail/1024:.2f} GB remaining. "
            "Context will be compressed soon."
        )
    if fraction >= WARN_THRESHOLD and not _profile.warn_fired:
        _profile.warn_fired = True
        avail = total - current
        return (
            f"\n⚡  RAM at {fraction*100:.0f}% "
            f"({current/1024:.1f}/{total/1024:.1f} GB) — "
            f"{avail/1024:.2f} GB free. "
            "Consider closing other apps."
        )
    return None


# ── Patch into conversation loop ──────────────────────────────────────────────

def _patch_conversation_loop() -> bool:
    """
    Hook into hermes's conversation loop to record RAM before/after each turn.
    Tries to patch the turn execution method — degrades gracefully if not found.
    """
    try:
        import agent.conversation_loop as cl

        if not hasattr(cl, "_run_one_turn") and not hasattr(cl, "run_one_turn"):
            return False

        fn_name = "_run_one_turn" if hasattr(cl, "_run_one_turn") else "run_one_turn"
        original = getattr(cl, fn_name)
        if getattr(original, "_offline_profiled", False):
            return True

        turn_counter = [0]

        def _profiled_turn(*args, **kwargs):
            turn_counter[0] += 1
            before_mb = _process_rss_mb()
            try:
                result = original(*args, **kwargs)
            finally:
                after_mb  = _process_rss_mb()
                ollama_mb = _ollama_rss_mb()
                _profile.record_turn(turn_counter[0], before_mb, after_mb, ollama_mb)

                warning = _check_thresholds()
                if warning:
                    print(warning, flush=True)

            return result

        _profiled_turn._offline_profiled = True
        setattr(cl, fn_name, _profiled_turn)
        logger.debug("RAM profiler hooked into conversation loop")
        return True

    except ImportError:
        return False


# ── Profile report ────────────────────────────────────────────────────────────

def get_profile_report() -> str:
    """
    Return a formatted RAM usage report for the completed session.
    Shown in the tracker atexit summary.
    """
    total_mb = _profile.total_ram_mb or _total_ram_mb()
    current_mb = _process_rss_mb()
    ollama_mb = _ollama_rss_mb()
    combined_mb = current_mb + ollama_mb

    lines = ["── RAM Profile ─────────────────────────────────"]

    if _profile.turns:
        # Per-turn deltas
        big_turns = sorted(_profile.turns, key=lambda t: abs(t.delta_mb), reverse=True)[:3]
        for t in big_turns:
            sign = "+" if t.delta_mb >= 0 else ""
            lines.append(f"  Turn {t.turn:>3}:  {sign}{t.delta_mb:.0f} MB delta")

    lines += [
        f"  Peak Python:  {_profile.peak_rss_mb:.0f} MB",
        f"  Ollama est:   {ollama_mb:.0f} MB",
        f"  Combined:     {combined_mb:.0f} MB  /  {total_mb/1024:.1f} GB total",
        f"  Fill:         {combined_mb/total_mb*100:.0f}%",
        "",
        "── Hardware Tier Comparison ────────────────────────",
    ]

    for tier, tier_gb in _TIER_RAM_GB.items():
        tier_mb = tier_gb * 1024
        pct = combined_mb / tier_mb * 100
        bar_len = int(pct / 5)
        bar = "█" * min(bar_len, 20) + ("▓" if pct > 100 else "")
        marker = " ◀ YOU" if abs(tier_mb - total_mb) < 1024 else ""
        lines.append(
            f"  {tier:<10} {tier_gb:.0f} GB  [{bar:<20}] {pct:>5.0f}%{marker}"
        )

    return "\n".join(lines)


# ── Public apply function ─────────────────────────────────────────────────────

def apply_profiler_patches() -> None:
    """Initialise profiler and hook into conversation loop. Called from patch.py."""
    _profile.total_ram_mb = _total_ram_mb()
    _patch_conversation_loop()
    logger.debug(
        "RAM profiler initialised: %.1f GB total, warn@%.0f%%, crit@%.0f%%",
        _profile.total_ram_mb / 1024, WARN_THRESHOLD * 100, CRIT_THRESHOLD * 100,
    )
