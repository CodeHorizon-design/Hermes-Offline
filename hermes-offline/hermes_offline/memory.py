"""
SqliteVecMemoryProvider — local semantic memory for Hermes Offline.

Implements the hermes-agent MemoryProvider ABC using:
  - sqlite-vec for cosine-similarity vector search (optional)
  - SQLite FTS5 for keyword search (always available)
  - nomic-embed-text via Ollama for embeddings (optional)
  - BEAM tiered architecture (Bright / Extended / Archived / Meta)

Falls back gracefully at every level:
  - No sqlite-vec → FTS5 only (still useful)
  - No Ollama embeddings → keyword search only
  - No Ollama at all → read-only mode (injects existing memories, no new embeddings)

Registration:
  Called from hermes_offline/patch.py → _patch_memory()
  Patches agent.memory_manager.MemoryManager after import.

Config (config.yaml):
  memory:
    semantic_backend: sqlite_vec   # enables vector search
    embedding_model: nomic-embed-text
    embedding_endpoint: http://127.0.0.1:11434
    beam_extended_sessions: 10     # sessions to keep in E tier
    max_system_prompt_chars: 2000  # total memory injected into system prompt
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Graceful import of MemoryProvider ABC ─────────────────────────────────────
try:
    from agent.memory_provider import MemoryProvider as _BaseProvider
    _HAS_BASE = True
except ImportError:
    # hermes-agent not installed or wrong version — create a stub base
    class _BaseProvider:  # type: ignore[no-redef]
        @property
        def name(self): return "sqlite_vec"
        def is_available(self): return False
        def initialize(self, session_id, **kwargs): pass
        def system_prompt_block(self): return ""
        def prefetch(self, query): return ""
        def sync_turn(self, user_msg, assistant_msg): pass
        def get_tool_schemas(self): return []
        def handle_tool_call(self, name, args): return {}
    _HAS_BASE = False


class SqliteVecMemoryProvider(_BaseProvider):
    """
    Local semantic memory provider using sqlite-vec + nomic-embed-text.
    Integrates with hermes MemoryManager as a drop-in external provider.
    """

    @property
    def name(self) -> str:
        return "sqlite_vec"

    def __init__(self) -> None:
        self._session_id: str = ""
        self._hermes_home: Optional[Path] = None
        self._embedder: Optional[Any] = None
        self._store: Optional[Any] = None
        self._use_semantic: bool = False
        self._max_prompt_chars: int = 2000
        self._cfg: Dict[str, Any] = {}

    def is_available(self) -> bool:
        """
        Return True if semantic memory is enabled in config.
        Does NOT check Ollama — that check happens in initialize().
        """
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            mem_cfg = cfg.get("memory", {})
            if not isinstance(mem_cfg, dict):
                return False
            return mem_cfg.get("semantic_backend") == "sqlite_vec"
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id

        # Resolve hermes home
        hermes_home_str = kwargs.get("hermes_home", "")
        if hermes_home_str:
            self._hermes_home = Path(hermes_home_str)
        else:
            try:
                from hermes_constants import get_hermes_home
                self._hermes_home = get_hermes_home()
            except ImportError:
                self._hermes_home = Path.home() / ".hermes"

        db_path = self._hermes_home / "memories" / "beam.db"

        # Load config
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            self._cfg = cfg.get("memory", {}) or {}
        except Exception:
            self._cfg = {}

        self._max_prompt_chars = int(self._cfg.get("max_system_prompt_chars", 2000))

        # Initialize BEAM store
        try:
            from hermes_offline.beam_memory import BEAMStore
            self._store = BEAMStore(db_path)
            logger.info("BEAM memory store initialized: %s", db_path)
        except Exception as exc:
            logger.warning("BEAM store init failed: %s", exc)
            return

        # Initialize embedder (optional — degrades to FTS5 only)
        embed_model = self._cfg.get("embedding_model", "nomic-embed-text")
        embed_endpoint = self._cfg.get("embedding_endpoint", "http://127.0.0.1:11434")

        try:
            from hermes_offline.embeddings import LocalEmbedder
            cache_path = self._hermes_home / "memories" / "embed_cache.db"
            self._embedder = LocalEmbedder(
                model=embed_model,
                cache_path=cache_path,
            )
            # Check if embedder is actually available
            if self._embedder.is_available():
                self._use_semantic = True
                logger.info("Semantic memory enabled: %s", embed_model)
            else:
                logger.info(
                    "nomic-embed-text not found in Ollama — using FTS5 keyword search only. "
                    "Install with: ollama pull nomic-embed-text"
                )
                self._use_semantic = False
        except Exception as exc:
            logger.debug("Embedder init failed: %s", exc)
            self._use_semantic = False

    def system_prompt_block(self) -> str:
        """
        Return a block to inject into the system prompt.
        Returns meta-tier entries (always-relevant) plus a brief note
        about session memory.
        """
        if not self._store:
            return ""
        try:
            from hermes_offline.beam_memory import TIER_PROMPT_LIMITS
            meta_entries = self._store.search_recent("M", limit=8)
            if not meta_entries:
                return ""
            lines = ["<!-- Persistent memory (cross-session) -->"]
            total = 0
            limit = TIER_PROMPT_LIMITS["M"]
            for entry in meta_entries:
                line = f"• {entry.content}"
                if total + len(line) > limit:
                    break
                lines.append(line)
                total += len(line)
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("system_prompt_block error: %s", exc)
            return ""

    def prefetch(self, query: str) -> str:
        """
        Called before each user turn — retrieve relevant memories.
        Returns a formatted string of recalled memories (injected into context).
        Hybrid: semantic (cosine sim) + keyword (FTS5), deduplicated.
        """
        if not self._store or not query:
            return ""

        try:
            from hermes_offline.beam_memory import TIER_PROMPT_LIMITS, MemoryEntry

            results: list[MemoryEntry] = []
            seen_content: set[str] = set()

            # Semantic search (if available)
            if self._use_semantic and self._embedder:
                q_vec = self._embedder.embed(query[:512])
                if q_vec:
                    semantic = self._store.search_semantic(q_vec, limit=4)
                    for e in semantic:
                        key = e.content[:80]
                        if key not in seen_content:
                            seen_content.add(key)
                            results.append(e)

            # FTS5 keyword search (fills gaps semantic misses)
            fts = self._store.search_fts(query, limit=4)
            for e in fts:
                key = e.content[:80]
                if key not in seen_content:
                    seen_content.add(key)
                    results.append(e)

            if not results:
                return ""

            # Format for context injection — compact and prioritized
            lines = ["<!-- Recalled memories -->"]
            total_chars = 0
            max_chars = min(self._max_prompt_chars, 1500)

            # Sort: M tier first, then by importance desc
            tier_order = {"M": 0, "B": 1, "E": 2, "A": 3}
            results.sort(key=lambda e: (tier_order.get(e.tier, 9), -e.importance))

            for entry in results:
                tier_label = {"M": "core", "B": "session", "E": "recent", "A": "archive"}.get(
                    entry.tier, entry.tier
                )
                line = f"[{tier_label}] {entry.content}"
                if total_chars + len(line) > max_chars:
                    break
                lines.append(line)
                total_chars += len(line)

            return "\n".join(lines)

        except Exception as exc:
            logger.debug("prefetch error: %s", exc)
            return ""

    def sync_turn(self, user_msg: str, assistant_msg: str) -> None:
        """
        Called after each turn — extract and store memorable facts.
        Bright tier (B) — current session, high recall priority.
        """
        if not self._store:
            return
        try:
            from hermes_offline.beam_memory import _extract_memories_from_turn

            entries = _extract_memories_from_turn(
                user_msg, assistant_msg, self._session_id
            )
            for entry in entries:
                # Embed if available
                if self._use_semantic and self._embedder:
                    vec = self._embedder.embed(entry.content)
                    if vec:
                        entry.embedding = vec
                self._store.add(entry)

        except Exception as exc:
            logger.debug("sync_turn error: %s", exc)

    def on_pre_compress(self, messages: list) -> str:
        """
        Called before context compression — extract and preserve key memories
        from the messages about to be compressed.
        """
        if not self._store:
            return ""
        try:
            from hermes_offline.beam_memory import MemoryEntry
            import re

            now = int(time.time())
            extracted = 0

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                if role not in ("user", "assistant"):
                    continue
                # Look for tool results with useful facts
                if len(content) > 50:
                    entry = MemoryEntry(
                        tier="E",  # Extended — recent session info
                        content=content[:300].strip(),
                        session_id=self._session_id,
                        created_at=now,
                        importance=0.8,
                        tags=["pre_compress", role],
                    )
                    if self._use_semantic and self._embedder:
                        vec = self._embedder.embed(entry.content)
                        if vec:
                            entry.embedding = vec
                    self._store.add(entry)
                    extracted += 1

            if extracted:
                logger.info("Pre-compress: extracted %d memory entries", extracted)
            return ""

        except Exception as exc:
            logger.debug("on_pre_compress error: %s", exc)
            return ""

    def on_session_end(self, messages: list) -> None:
        """
        Promote Bright (B) entries to Extended (E) tier when session ends.
        """
        if not self._store or not self._session_id:
            return
        try:
            n = self._store.promote_tier("B", "E", [self._session_id])
            if n:
                logger.info("Promoted %d Bright→Extended memories", n)
            # Purge if Extended tier is getting large
            self._store.purge_old("E", keep_n=300)
        except Exception as exc:
            logger.debug("on_session_end error: %s", exc)

    def get_tool_schemas(self) -> list[dict]:
        """Expose a 'semantic_search_memory' tool to the agent."""
        if not self._use_semantic:
            return []
        return [{
            "name": "semantic_search_memory",
            "description": (
                "Search your semantic memory store for relevant past experiences, "
                "user preferences, and learned facts. Use this when the built-in "
                "'memory' tool doesn't find what you're looking for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query to search memories",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results (1-10, default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        }]

    def handle_tool_call(self, name: str, args: dict) -> dict:
        """Handle tool calls routed to this provider."""
        if name == "semantic_search_memory":
            query = args.get("query", "")
            limit = min(int(args.get("limit", 5)), 10)
            results = []

            if self._store:
                if self._use_semantic and self._embedder:
                    vec = self._embedder.embed(query)
                    if vec:
                        results = self._store.search_semantic(vec, limit=limit)
                if not results:
                    results = self._store.search_fts(query, limit=limit)

            if not results:
                return {"result": "No memories found for that query."}

            items = []
            for r in results:
                items.append({
                    "content": r.content,
                    "tier": r.tier,
                    "age_days": round(r.age_days, 1),
                    "importance": r.importance,
                })
            return {"result": items}

        return {"error": f"Unknown tool: {name}"}

    def stats(self) -> dict:
        """Return memory statistics for the tracker."""
        if not self._store:
            return {}
        return {
            "total": self._store.count(),
            "by_tier": {
                t: self._store.count(t) for t in ("B", "E", "A", "M")
            },
            "semantic": self._use_semantic,
            "embed_cache": self._embedder.cache_stats() if self._embedder else {},
        }

    def shutdown(self) -> None:
        if self._store:
            try:
                self.on_session_end([])
            except Exception:
                pass
            self._store.close()
        if self._embedder:
            self._embedder.close()
