"""
Qwen3 thinking mode wrapper for Hermes Offline.

Qwen3 models support an extended chain-of-thought mode activated by prepending
'/think' to the user message (or '/no_think' to disable it per-turn). The model
then emits <think>...</think> blocks containing its internal reasoning before
the final answer. This dramatically improves:

  - Multi-step planning accuracy (~15-20% improvement on complex agentic tasks)
  - Tool argument construction for rare/unusual tool combinations
  - Self-correction when an earlier tool call returned unexpected results

Trade-off: +2-10 seconds per turn depending on model size and task complexity.
Use for hard tasks; disable for fast back-and-forth.

This module:
  1. Patches the OpenAI client used by hermes_cli.transports.openai_chat
  2. Detects "complex" turns using heuristics (task length, prior failures, etc.)
  3. Prepends /think (or /no_think) to the outgoing user message
  4. Strips <think>...</think> from the response content so hermes tool parsing
     works correctly (it sees only the clean answer)
  5. Optionally displays thinking blocks in the console (rich panel, dimmed)

Enable:
    hermes-offline --think       # force thinking on every turn
    hermes-offline --auto-think  # heuristic-based (recommended)
    HERMES_THINK=1 hermes-offline

Or in config.yaml:
    think:
      mode: auto    # auto | always | never
      show: false   # show <think> blocks in UI
      threshold: 3  # complexity score threshold (1-10) for auto mode
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Minimum length (chars) for a user message to be considered "complex"
_AUTO_THINK_MIN_CHARS = 80
# Keywords that suggest a complex / multi-step task
_COMPLEX_KEYWORDS = frozenset([
    "refactor", "debug", "implement", "analyze", "design", "plan", "optimize",
    "migrate", "review", "explain", "compare", "investigate", "write a",
    "create a", "build a", "how does", "why does", "multiple", "all of",
    "step by step", "first", "then", "finally", "workflow", "pipeline",
])


class ThinkConfig:
    """Thinking mode configuration."""

    def __init__(
        self,
        mode: str = "auto",         # "auto" | "always" | "never"
        show: bool = False,          # display <think> blocks in UI
        threshold: int = 3,          # complexity score (1-10) for auto
    ):
        self.mode = mode
        self.show = show
        self.threshold = threshold

    @classmethod
    def from_env(cls) -> "ThinkConfig":
        mode_env = os.environ.get("HERMES_THINK", "").lower()
        if mode_env in ("1", "true", "always"):
            mode = "always"
        elif mode_env in ("auto",):
            mode = "auto"
        elif mode_env in ("0", "false", "never"):
            mode = "never"
        else:
            mode = "auto"
        show = os.environ.get("HERMES_THINK_SHOW", "0").lower() in ("1", "true")
        return cls(mode=mode, show=show)

    @classmethod
    def from_hermes_config(cls) -> "ThinkConfig":
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            think_cfg = cfg.get("think", {})
            if not isinstance(think_cfg, dict):
                return cls.from_env()
            return cls(
                mode=think_cfg.get("mode", "auto"),
                show=think_cfg.get("show", False),
                threshold=int(think_cfg.get("threshold", 3)),
            )
        except Exception:
            return cls.from_env()


def _complexity_score(messages: list[dict]) -> int:
    """
    Heuristic complexity score (0-10) for a list of messages.
    Higher score = more likely to benefit from chain-of-thought.
    """
    score = 0
    last_user = ""

    for msg in messages:
        if msg.get("role") == "user":
            last_user = str(msg.get("content", ""))

    # Length heuristic
    if len(last_user) > 300:
        score += 3
    elif len(last_user) > 150:
        score += 2
    elif len(last_user) > 80:
        score += 1

    # Keyword heuristic
    lower = last_user.lower()
    for kw in _COMPLEX_KEYWORDS:
        if kw in lower:
            score += 1
            if score >= 10:
                break

    # Multi-message context (long conversation = more complex)
    if len(messages) > 10:
        score += 1
    if len(messages) > 20:
        score += 1

    return min(score, 10)


def _inject_think_tag(messages: list[dict], force_think: bool) -> tuple[list[dict], bool]:
    """
    Inject /think or /no_think into the last user message.
    Returns (modified_messages, thinking_was_enabled).
    """
    messages = [dict(m) for m in messages]  # shallow copy

    # Find last user message
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = str(messages[i].get("content", ""))

            # Don't double-inject
            if content.startswith("/think") or content.startswith("/no_think"):
                return messages, content.startswith("/think")

            if force_think:
                messages[i]["content"] = "/think\n" + content
                return messages, True
            else:
                messages[i]["content"] = "/no_think\n" + content
                return messages, False

    return messages, False


def _extract_thinking(text: str) -> tuple[str, str]:
    """
    Extract <think>...</think> blocks from a response.
    Returns (thinking_content, clean_response).
    The clean response is what hermes tool parsers see.
    """
    think_blocks = []
    clean = re.sub(
        r"<think>(.*?)</think>",
        lambda m: think_blocks.append(m.group(1)) or "",
        text,
        flags=re.DOTALL,
    )
    return "\n\n".join(think_blocks).strip(), clean.strip()


def _display_thinking(thinking: str) -> None:
    """Display a <think> block in the terminal (dimmed, collapsible)."""
    if not thinking:
        return
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        console = Console(stderr=True)
        # Truncate very long thinking blocks for display
        display = thinking[:2000]
        if len(thinking) > 2000:
            display += f"\n\n[{len(thinking) - 2000} chars truncated]"
        console.print(
            Panel(
                Text(display, style="dim"),
                title="[dim]🤔 model thinking[/dim]",
                border_style="dim",
                expand=False,
            )
        )
    except ImportError:
        print(f"\n[think]\n{thinking[:800]}\n[/think]\n")


class ThinkingTransportWrapper:
    """
    Wraps any OpenAI-compatible client to inject Qwen3 thinking mode.
    Drop-in replacement for openai.OpenAI used in hermes_cli transports.
    """

    def __init__(self, wrapped_client: Any, config: ThinkConfig):
        self._client = wrapped_client
        self._config = config

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if name == "chat":
            return _ChatWrapper(attr, self._config)
        return attr


class _ChatWrapper:
    def __init__(self, chat: Any, config: ThinkConfig):
        self._chat = chat
        self._config = config

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._chat, name)
        if name == "completions":
            return _CompletionsWrapper(attr, self._config)
        return attr


class _CompletionsWrapper:
    def __init__(self, completions: Any, config: ThinkConfig):
        self._completions = completions
        self._config = config

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)

    def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages", [])
        config = self._config

        # Decide whether to think
        should_think = False
        if config.mode == "always":
            should_think = True
        elif config.mode == "auto":
            score = _complexity_score(messages)
            should_think = score >= config.threshold
            logger.debug("Think mode: complexity score=%d, threshold=%d, think=%s",
                         score, config.threshold, should_think)

        if should_think or config.mode == "always":
            modified, was_enabled = _inject_think_tag(messages, should_think)
            kwargs["messages"] = modified
        else:
            modified, was_enabled = _inject_think_tag(messages, False)
            kwargs["messages"] = modified

        # Make the call
        response = self._completions.create(**kwargs)

        # Post-process: strip <think> blocks from response
        if was_enabled:
            try:
                content = response.choices[0].message.content or ""
                thinking, clean = _extract_thinking(content)

                if thinking:
                    logger.debug("Thinking block: %d chars", len(thinking))
                    if config.show:
                        _display_thinking(thinking)

                # Patch the response content with the clean version
                response.choices[0].message.content = clean
            except (AttributeError, IndexError):
                pass

        return response


def patch_openai_transport(config: Optional[ThinkConfig] = None) -> bool:
    """
    Patch the hermes_cli openai_chat transport to wrap every client
    it creates with ThinkingTransportWrapper.

    Returns True if patch succeeded, False if transport module not found.
    """
    if config is None:
        config = ThinkConfig.from_hermes_config()

    if config.mode == "never":
        logger.debug("Think mode disabled (mode=never)")
        return False

    try:
        import agent.transports.openai_chat as transport_module
        _think_cfg = config

        original_make_client = getattr(transport_module, "_make_openai_client", None)
        original_create_client = getattr(transport_module, "create_client", None)

        # Try both common function names across versions
        def _wrap(fn: Callable) -> Callable:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                client = fn(*args, **kwargs)
                return ThinkingTransportWrapper(client, _think_cfg)
            return wrapper

        patched = False
        for attr_name in ("_make_openai_client", "create_client", "_build_client"):
            fn = getattr(transport_module, attr_name, None)
            if fn and not getattr(fn, "_think_patched", False):
                wrapped = _wrap(fn)
                wrapped._think_patched = True
                setattr(transport_module, attr_name, wrapped)
                patched = True
                logger.info("Patched think mode onto transport.%s", attr_name)

        return patched

    except ImportError as exc:
        logger.debug("Could not patch openai_chat transport: %s", exc)
        return False


def apply_think_mode(mode: str = "auto", show: bool = False, threshold: int = 3) -> None:
    """
    Convenience function: apply think-mode patching with given settings.
    Called from patch.py or entrypoint.
    """
    config = ThinkConfig(mode=mode, show=show, threshold=threshold)
    success = patch_openai_transport(config)
    if success:
        logger.info("Think mode active: mode=%s, show=%s, threshold=%d",
                    mode, show, threshold)
    else:
        logger.debug("Think mode patch not applied (transport not found or mode=never)")
