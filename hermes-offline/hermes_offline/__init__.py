"""
hermes-offline — Offline-first extension for Hermes Agent.

Patches hermes-agent at import time to:
  - Register 'ollama-local' as a first-class provider (127.0.0.1:11434/v1)
  - Set offline-first defaults in config
  - Wire local search, local TTS, local image-gen backends
  - Apply context-compression tuning for small local models
  - Register BEAM tiered semantic memory (sqlite-vec + nomic-embed-text)
  - Wire DSPy to local Ollama (lightweight self-evolution, population=2)

Usage:
    import hermes_offline
    hermes_offline.apply()   # call before any hermes_cli imports
"""

__version__ = "1.0.0"
__all__ = [
    "apply",
    "register_ollama_provider",
    "get_hardware_profile",
    "apply_think_mode",
    "get_session",
    "SqliteVecMemoryProvider",
    "LocalEmbedder",
    "BEAMStore",
]

from hermes_offline.patch import apply
from hermes_offline.hardware import get_hardware_profile
from hermes_offline.providers import register_ollama_provider
from hermes_offline.think import apply_think_mode
from hermes_offline.tracker import get_session

# Phase 2 — lazy public surface (avoid pulling in heavy deps at import)
def SqliteVecMemoryProvider():  # noqa: N802
    from hermes_offline.memory import SqliteVecMemoryProvider as _P
    return _P()

def LocalEmbedder(*args, **kwargs):  # noqa: N802
    from hermes_offline.embeddings import LocalEmbedder as _E
    return _E(*args, **kwargs)

def BEAMStore(db_path):  # noqa: N802
    from hermes_offline.beam_memory import BEAMStore as _B
    return _B(db_path)
