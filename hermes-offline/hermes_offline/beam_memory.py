"""
BEAM Tiered Memory Architecture for Hermes Offline.

Four memory tiers, each with different retrieval priority and retention policy:

  B — Bright   (current session working memory, highest priority recall)
  E — Extended (recent N sessions, warm, FTS5 keyword search)
  A — Archived (older sessions, cold, compressed, cosine similarity)
  M — Meta     (cross-session patterns, skills, user model — always recalled)

Storage layout under ~/.hermes/memories/:
  beam.db            — main SQLite database (B, E, M tiers)
  beam_archive.db    — archive SQLite database (A tier, larger, less accessed)
  embed_cache.db     — embedding cache (shared with LocalEmbedder)

Why BEAM?
  - Small local LLMs benefit from tight, relevant context more than large dumps
  - Tiering ensures the most important memories are always injected first
  - Archive prevents unbounded growth while keeping long-term recall possible
  - Meta tier handles "things about the user" separately from "things I learned"
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Max chars injected into system prompt per tier
TIER_PROMPT_LIMITS = {
    "M": 800,   # Meta — always shown, tight limit
    "B": 600,   # Bright — current session context
    "E": 500,   # Extended — recent sessions
    "A": 300,   # Archived — background long-term
}

# How many recent sessions qualify as "Extended" (warm)
EXTENDED_SESSION_COUNT = 10

# Archive after this many sessions
ARCHIVE_AFTER_SESSIONS = 30


@dataclass
class MemoryEntry:
    tier: str           # B / E / A / M
    content: str
    session_id: str
    created_at: int
    importance: float = 1.0
    tags: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400


class BEAMStore:
    """
    SQLite-backed BEAM tiered memory store.
    Thread-safe via check_same_thread=False (SQLite WAL mode).
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tier       TEXT    NOT NULL CHECK(tier IN ('B','E','A','M')),
                content    TEXT    NOT NULL,
                session_id TEXT    NOT NULL,
                created_at INTEGER NOT NULL,
                importance REAL    NOT NULL DEFAULT 1.0,
                tags       TEXT    NOT NULL DEFAULT '[]',
                vector     BLOB
            );

            CREATE INDEX IF NOT EXISTS idx_tier        ON memories(tier);
            CREATE INDEX IF NOT EXISTS idx_session     ON memories(session_id);
            CREATE INDEX IF NOT EXISTS idx_created     ON memories(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_importance  ON memories(importance DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, content=memories, content_rowid=id);

            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES('delete', old.id, old.content);
                INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
            END;
        """)
        self._db.commit()

    def add(self, entry: MemoryEntry) -> int:
        import struct
        vec_blob = None
        if entry.embedding:
            vec_blob = struct.pack(f"{len(entry.embedding)}f", *entry.embedding)
        cur = self._db.execute(
            "INSERT INTO memories(tier,content,session_id,created_at,importance,tags,vector) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                entry.tier,
                entry.content,
                entry.session_id,
                entry.created_at,
                entry.importance,
                json.dumps(entry.tags),
                vec_blob,
            ),
        )
        self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def search_fts(self, query: str, tier: Optional[str] = None, limit: int = 5) -> list[MemoryEntry]:
        """Full-text search across memory content."""
        # Sanitize query for FTS5
        safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
        if not safe_query:
            return []
        try:
            if tier:
                rows = self._db.execute(
                    "SELECT m.tier, m.content, m.session_id, m.created_at, m.importance, m.tags "
                    "FROM memories m JOIN memories_fts f ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? AND m.tier=? "
                    "ORDER BY rank, m.importance DESC LIMIT ?",
                    (safe_query, tier, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT m.tier, m.content, m.session_id, m.created_at, m.importance, m.tags "
                    "FROM memories m JOIN memories_fts f ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? "
                    "ORDER BY rank, m.importance DESC LIMIT ?",
                    (safe_query, limit),
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("FTS5 search error: %s", e)
            return []

        return [
            MemoryEntry(
                tier=r[0], content=r[1], session_id=r[2],
                created_at=r[3], importance=r[4],
                tags=json.loads(r[5] or "[]"),
            )
            for r in rows
        ]

    def search_recent(self, tier: str, limit: int = 10) -> list[MemoryEntry]:
        """Return most recent entries for a tier."""
        rows = self._db.execute(
            "SELECT tier,content,session_id,created_at,importance,tags FROM memories "
            "WHERE tier=? ORDER BY created_at DESC, importance DESC LIMIT ?",
            (tier, limit),
        ).fetchall()
        return [
            MemoryEntry(
                tier=r[0], content=r[1], session_id=r[2],
                created_at=r[3], importance=r[4],
                tags=json.loads(r[5] or "[]"),
            )
            for r in rows
        ]

    def search_semantic(
        self, query_vec: list[float], tier: Optional[str] = None, limit: int = 5
    ) -> list[MemoryEntry]:
        """
        Cosine similarity search over stored embeddings.
        sqlite-vec is optional — falls back to FTS5 gracefully.
        """
        import struct

        try:
            import sqlite_vec  # type: ignore
            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
        except (ImportError, Exception):
            return []

        q_blob = struct.pack(f"{len(query_vec)}f", *query_vec)
        try:
            if tier:
                rows = self._db.execute(
                    "SELECT tier,content,session_id,created_at,importance,tags "
                    "FROM memories WHERE vector IS NOT NULL AND tier=? "
                    "ORDER BY vec_distance_cosine(vector, ?) LIMIT ?",
                    (tier, q_blob, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT tier,content,session_id,created_at,importance,tags "
                    "FROM memories WHERE vector IS NOT NULL "
                    "ORDER BY vec_distance_cosine(vector, ?) LIMIT ?",
                    (q_blob, limit),
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("sqlite-vec search error: %s", e)
            return []

        return [
            MemoryEntry(
                tier=r[0], content=r[1], session_id=r[2],
                created_at=r[3], importance=r[4],
                tags=json.loads(r[5] or "[]"),
            )
            for r in rows
        ]

    def promote_tier(self, from_tier: str, to_tier: str, session_ids: list[str]) -> int:
        """Move entries from one tier to another (e.g. B→E after session ends)."""
        result = self._db.execute(
            f"UPDATE memories SET tier=? WHERE tier=? AND session_id IN ({','.join('?'*len(session_ids))})",
            [to_tier, from_tier] + session_ids,
        )
        self._db.commit()
        return result.rowcount

    def count(self, tier: Optional[str] = None) -> int:
        if tier:
            return self._db.execute("SELECT COUNT(*) FROM memories WHERE tier=?", (tier,)).fetchone()[0]
        return self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def purge_old(self, tier: str, keep_n: int = 200) -> int:
        """Keep only the top-N most important entries in a tier; delete the rest."""
        rows = self._db.execute(
            "SELECT id FROM memories WHERE tier=? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (tier, keep_n),
        ).fetchall()
        keep_ids = [r[0] for r in rows]
        if not keep_ids:
            return 0
        placeholders = ",".join("?" * len(keep_ids))
        result = self._db.execute(
            f"DELETE FROM memories WHERE tier=? AND id NOT IN ({placeholders})",
            [tier] + keep_ids,
        )
        self._db.commit()
        return result.rowcount

    def close(self) -> None:
        self._db.close()


def _extract_memories_from_turn(
    user_msg: str,
    assistant_msg: str,
    session_id: str,
) -> list[MemoryEntry]:
    """
    Heuristically extract memorable facts from a turn.
    This is a lightweight rule-based extractor — no LLM call needed.
    For richer extraction, the full hermes memory tool handles this.
    """
    entries = []
    now = int(time.time())

    # Extract explicit "remember that" / "note that" directives
    patterns = [
        r"(?:remember|note|keep in mind|don't forget)[:\s]+(.{20,200})",
        r"(?:my preference is|i prefer|i like|i always)[:\s]+(.{10,150})",
        r"(?:the project uses|this codebase|this repo)[:\s]+(.{10,150})",
        r"(?:my name is|call me|i'm called)[:\s]+(\w[\w\s]{1,50})",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, user_msg, re.IGNORECASE):
            content = match.strip()
            if len(content) > 15:
                entries.append(MemoryEntry(
                    tier="M",  # explicit user instructions go to Meta
                    content=content,
                    session_id=session_id,
                    created_at=now,
                    importance=1.5,
                    tags=["user_instruction"],
                ))

    return entries
