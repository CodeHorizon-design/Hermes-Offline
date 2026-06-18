"""
TUI footer injection for the session tracker.

Attempts to hook into Hermes's Textual TUI to add a persistent footer bar
showing live session stats. Falls back gracefully if the TUI structure has
changed or Textual is not available.

The footer shows:
  [model: qwen3:8b]  [ctx: 24%  3,932/16,384]  [5.2 GB RAM]  [42 tok/s]  [12:34]

This module is separate from tracker.py so it's only imported when --tui
is active (avoids pulling in Textual for non-TUI sessions).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def try_inject_tui_footer() -> bool:
    """
    Attempt to inject a live status footer into the Hermes Textual TUI.
    Returns True if successful.

    Strategy: monkey-patch Hermes's App class compose() to append our
    HermesOfflineFooter widget. This runs before the TUI starts.
    """
    try:
        from textual.app import App
        from textual.widgets import Footer, Static
        from textual.reactive import reactive

        from hermes_offline.tracker import get_session, _fmt_duration as fmt

        class HermesOfflineFooter(Static):
            """Live session stats footer for Hermes TUI."""

            DEFAULT_CSS = """
            HermesOfflineFooter {
                dock: bottom;
                height: 1;
                background: $surface-darken-1;
                color: $text-muted;
                text-style: dim;
                padding: 0 1;
            }
            """

            def on_mount(self) -> None:
                self.set_interval(2.0, self._update)

            def _update(self) -> None:
                session = get_session()
                if not session.turns:
                    self.update("  hermes-offline  ·  no turns yet")
                    return

                last = session.turns[-1]
                ctx_pct = session.context_fill_pct
                ctx_str = (
                    f"{last.prompt_tokens:,}/{session.num_ctx:,} ({ctx_pct:.0f}%)"
                    if session.num_ctx else f"{last.prompt_tokens:,} tok"
                )

                self.update(
                    f"  {session.model}  ·  "
                    f"ctx {ctx_str}  ·  "
                    f"{session.ram_gb} GB RAM  ·  "
                    f"{last.tokens_per_sec:.1f} tok/s  ·  "
                    f"turns {len(session.turns)}  ·  "
                    f"tools {session.total_tool_calls}  ·  "
                    f"{fmt(session.elapsed_secs)}"
                )

        # Find Hermes App class and patch its compose
        _original_compose: Optional[Any] = None

        def _find_hermes_app() -> Optional[type]:
            """Try common module paths for the Hermes TUI App class."""
            candidates = [
                ("hermes_cli.tui", "HermesApp"),
                ("hermes_cli.main", "HermesApp"),
                ("hermes_cli.app", "HermesApp"),
                ("hermes_cli.tui_app", "HermesApp"),
            ]
            for module_path, class_name in candidates:
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    cls = getattr(mod, class_name, None)
                    if cls and issubclass(cls, App):
                        return cls
                except ImportError:
                    continue
            return None

        app_cls = _find_hermes_app()
        if app_cls is None:
            logger.debug("Could not find Hermes TUI App class — skipping footer injection")
            return False

        original_compose = app_cls.compose

        async def patched_compose(self: Any):  # type: ignore[override]
            async for widget in original_compose(self):
                yield widget
            yield HermesOfflineFooter()

        app_cls.compose = patched_compose
        logger.info("Injected HermesOfflineFooter into TUI")
        return True

    except ImportError:
        logger.debug("Textual not available — TUI footer skipped")
        return False
    except Exception as exc:
        logger.debug("TUI footer injection failed: %s", exc)
        return False
