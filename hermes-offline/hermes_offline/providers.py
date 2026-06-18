"""
Ollama local provider registration for Hermes Agent.

Patches hermes_cli.providers.HERMES_OVERLAYS at runtime to add:
  - ollama-local:  http://127.0.0.1:11434/v1  (default offline provider)
  - ollama-custom: user-configurable endpoint via OLLAMA_LOCAL_BASE_URL

Also patches the provider display list so 'ollama-local' appears in
`hermes model` and `hermes setup` as a first-class option.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

OLLAMA_LOCAL_BASE_URL = os.environ.get(
    "OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"
)


def register_ollama_provider() -> bool:
    """
    Inject the ollama-local provider into hermes_cli.providers.HERMES_OVERLAYS.
    Returns True on success, False if hermes_cli is not installed.
    """
    try:
        from hermes_cli.providers import HERMES_OVERLAYS, HermesOverlay
    except ImportError:
        logger.warning("hermes_cli not found — cannot register Ollama provider")
        return False

    # Only register once
    if "ollama-local" in HERMES_OVERLAYS:
        return True

    HERMES_OVERLAYS["ollama-local"] = HermesOverlay(
        transport="openai_chat",
        auth_type="api_key",
        base_url_override=OLLAMA_LOCAL_BASE_URL,
        base_url_env_var="OLLAMA_LOCAL_BASE_URL",
    )

    # Also register a generic alias so user can type "ollama"
    if "ollama" not in HERMES_OVERLAYS:
        HERMES_OVERLAYS["ollama"] = HermesOverlay(
            transport="openai_chat",
            auth_type="api_key",
            base_url_override=OLLAMA_LOCAL_BASE_URL,
            base_url_env_var="OLLAMA_LOCAL_BASE_URL",
        )

    logger.info("Registered ollama-local provider at %s", OLLAMA_LOCAL_BASE_URL)
    return True


def check_ollama_running(base_url: str = OLLAMA_LOCAL_BASE_URL) -> Optional[list]:
    """
    Check if Ollama is running and return list of installed models.
    Returns None if Ollama is not running.
    """
    import urllib.request
    import urllib.error
    import json

    tags_url = base_url.rstrip("/v1").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=2) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return None


def detect_best_installed_model(models: list[str], tier: str) -> Optional[str]:
    """
    Given a list of installed Ollama model names and a hardware tier,
    return the best model to use.
    """
    PRIORITY_BY_TIER = {
        "great":     ["qwen3-coder:30b", "qwen2.5-coder:32b", "qwen3:14b", "qwen3:8b", "llama3.1:8b"],
        "good":      ["qwen2.5-coder:14b", "qwen3:8b", "llama3.1:8b", "qwen3:4b"],
        "mid":       ["qwen3:8b", "llama3.1:8b", "qwen3:4b", "phi4-mini", "llama3.1:7b"],
        "low":       ["qwen3:4b", "phi4-mini", "llama3.2:3b", "qwen3:1.7b"],
        "ultra_low": ["qwen3:1.7b", "qwen3:0.6b", "smollm2:1.7b", "llama3.2:1b"],
    }
    priority = PRIORITY_BY_TIER.get(tier, PRIORITY_BY_TIER["mid"])
    model_names_lower = {m.lower(): m for m in models}

    for preferred in priority:
        # Exact match
        if preferred in models:
            return preferred
        # Prefix match (e.g., "qwen3:8b" matches "qwen3:8b-q4_K_M")
        for installed in models:
            if installed.lower().startswith(preferred.split(":")[0].lower()):
                if len(preferred.split(":")) > 1:
                    tag = preferred.split(":")[1]
                    if tag in installed.lower():
                        return installed
        # Just match by family
        for installed in models:
            family = preferred.split(":")[0].lower()
            if installed.lower().startswith(family):
                return installed

    # Fallback: return the first installed model
    return models[0] if models else None
