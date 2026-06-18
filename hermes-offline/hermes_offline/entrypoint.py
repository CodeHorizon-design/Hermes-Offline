"""
Offline-patched Hermes Agent entry point.

This wraps the standard `hermes` entry point with offline patches applied
before any hermes_cli code runs.

Subcommands (handled here, never forwarded to hermes):
    update           Check for new hermes-agent versions, run compat checks,
                     and upgrade everything with one command.
                       hermes-offline update [--check|--force|--dry-run|--skip-compat]

    evolve           Run local DSPy BootstrapFewShot prompt evolution on
                     your session history and save a compiled program.
                       hermes-offline evolve [--dry-run|--reset|--model|--verbose]

Extra flags (consumed here, stripped before forwarding to hermes):
    --think             Force chain-of-thought thinking on every turn
    --auto-think        Heuristic-based thinking (default)
    --no-think          Disable thinking mode entirely
    --show-thinking     Print <think> blocks in the terminal
    --evolution-mode=X  Override config evolution mode (lightweight|disabled)

Usage: hermes-offline [update|evolve|--think|--evolution-mode=X] [hermes args...]
"""

from __future__ import annotations

import os
import sys


def _parse_offline_flags(argv: list[str]) -> tuple[dict, list[str]]:
    """
    Extract all hermes-offline-specific flags from argv.
    Returns (flags_dict, remaining_argv_for_hermes).

    Flags consumed here (never forwarded to hermes):
        --think / --auto-think / --no-think
        --show-thinking
        --track / --no-track
        --no-status          suppress per-turn status line
        --no-summary         suppress exit summary
    """
    flags: dict = {
        "think_mode":        os.environ.get("HERMES_THINK", "auto"),
        "think_show":        os.environ.get("HERMES_THINK_SHOW", "0") in ("1", "true"),
        "track":             os.environ.get("HERMES_TRACK", "1") not in ("0", "false"),
        "track_status_line": True,
        "track_summary":     True,
        "tui_footer":        True,
        "evolution_mode":    os.environ.get("HERMES_EVOLUTION_MODE", ""),
    }
    remaining = []

    for arg in argv:
        if arg == "--think":
            flags["think_mode"] = "always"
        elif arg == "--auto-think":
            flags["think_mode"] = "auto"
        elif arg == "--no-think":
            flags["think_mode"] = "never"
        elif arg == "--show-thinking":
            flags["think_show"] = True
        elif arg == "--track":
            flags["track"] = True
        elif arg == "--no-track":
            flags["track"] = False
        elif arg == "--no-status":
            flags["track_status_line"] = False
        elif arg == "--no-summary":
            flags["track_summary"] = False
        elif arg.startswith("--evolution-mode="):
            flags["evolution_mode"] = arg.split("=", 1)[1].strip()
        elif arg == "--evolution-mode":
            # positional value handled on next iteration — set sentinel
            flags["_consume_next_as_evolution_mode"] = True
        elif flags.pop("_consume_next_as_evolution_mode", False):
            flags["evolution_mode"] = arg
        else:
            remaining.append(arg)

    return flags, remaining


def main() -> None:
    # Intercept subcommands before any hermes_cli code runs
    args = sys.argv[1:]

    if args and args[0] == "update":
        from hermes_offline.updater import main as update_main
        update_main(args[1:])
        return

    if args and args[0] == "evolve":
        from hermes_offline.evolution import main as evolve_main
        evolve_main(args[1:])
        return

    # Parse our custom flags before hermes sees argv
    flags, clean_argv = _parse_offline_flags(sys.argv[1:])

    # Propagate --evolution-mode flag to env so patch.py picks it up
    if flags.get("evolution_mode"):
        os.environ["HERMES_EVOLUTION_MODE"] = flags["evolution_mode"]

    # Apply offline patches before hermes_cli loads
    from hermes_offline.patch import apply
    apply(
        think_mode=flags["think_mode"],
        think_show=flags["think_show"],
        track=flags["track"],
        track_status_line=flags["track_status_line"],
        track_summary=flags["track_summary"],
    )

    # Inject TUI footer if --tui is in the remaining args
    if "--tui" in clean_argv and flags.get("tui_footer"):
        try:
            from hermes_offline.tracker_tui import try_inject_tui_footer
            try_inject_tui_footer()
        except Exception:
            pass

    # Signal to sitecustomize that we're in offline mode
    os.environ.setdefault("HERMES_OFFLINE", "1")

    # Restore cleaned argv (hermes main() reads sys.argv directly)
    sys.argv = [sys.argv[0]] + clean_argv

    # Delegate to standard hermes entry point
    try:
        from hermes_cli.main import main as hermes_main
        hermes_main()
    except ImportError:
        print("hermes-agent not installed. Run: pip install hermes-agent")
        sys.exit(1)
    except SystemExit as exc:
        sys.exit(exc.code)
