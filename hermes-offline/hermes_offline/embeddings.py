"""
Local embedding client for hermes-offline.

Generates text embeddings using nomic-embed-text via the Ollama API.
No internet, no API key, no cloud.

Features:
  - Calls POST http://127.0.0.1:11434/api/embeddings
  - SQLite disk cache — same text is never re-embedded twice
  - Batch support — embed multiple strings in one call
  - Automatic model availability check
  - Configurable model (default: nomic-embed-text)

Cache layout:
  ~/.hermes/memories/embed_cache.db
  table: embeddings(text_hash TEXT PK, model TEXT, vector BLOB, created_at INT)

Usage:
    from hermes_offline.embeddings import LocalEmbedder
    embedder = LocalEmbedder()
    vec = embedder.embed("The user prefers dark mode")
    vecs = embedder.embed_batch(["text 1", "text 2", "text 3"])
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = os.environ.get("HERMES_EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE = os.environ.get("OLLAMA_LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/v1").rstrip("/")
EMBED_DIM = 768   # nomic-embed-text output dimension


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class LocalEmbedder:
    """
    Embeds text locally via Ollama nomic-embed-text.
    Caches results in SQLite so repeated embeds are free.
    """

    def __init__(
        self,
        model: str = DEFAULT_EMBED_MODEL,
        cache_path: Optional[Path] = None,
        use_cache: bool = True,
    ):
        self.model = model
        self.use_cache = use_cache
        self._db: Optional[sqlite3.Connection] = None

        if cache_path is None:
            try:
                from hermes_constants import get_hermes_home
                cache_path = get_hermes_home() / "memories" / "embed_cache.db"
            except ImportError:
                cache_path = Path.home() / ".hermes" / "memories" / "embed_cache.db"

        self.cache_path = cache_path
        if use_cache:
            self._init_cache()

    def _init_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                text_hash TEXT NOT NULL,
                model     TEXT NOT NULL,
                vector    BLOB NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (text_hash, model)
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_model ON embeddings(model)")
        self._db.commit()

    def _cache_get(self, text_hash: str) -> Optional[list[float]]:
        if not self._db:
            return None
        row = self._db.execute(
            "SELECT vector FROM embeddings WHERE text_hash=? AND model=?",
            (text_hash, self.model),
        ).fetchone()
        return _blob_to_vec(row[0]) if row else None

    def _cache_set(self, text_hash: str, vec: list[float]) -> None:
        if not self._db:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO embeddings(text_hash, model, vector, created_at) VALUES(?,?,?,?)",
            (text_hash, self.model, _vec_to_blob(vec), int(time.time())),
        )
        self._db.commit()

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def embed(self, text: str) -> Optional[list[float]]:
        """
        Embed a single string. Returns a float list or None if Ollama unavailable.
        """
        text = text.strip()
        if not text:
            return None

        h = self._hash(text)
        if self.use_cache:
            cached = self._cache_get(h)
            if cached:
                return cached

        url = f"{OLLAMA_BASE}/api/embeddings"
        payload = json.dumps({"model": self.model, "prompt": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                vec = data.get("embedding", [])
                if not vec:
                    logger.warning("Empty embedding returned for model %s", self.model)
                    return None
                if self.use_cache:
                    self._cache_set(h, vec)
                return vec
        except urllib.error.URLError as exc:
            logger.debug("Ollama embedding failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Unexpected embedding error: %s", exc)
            return None

    def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """
        Embed multiple strings. Each is embedded independently (Ollama
        doesn't support batching in a single call).
        Cache hits are returned without any network call.
        """
        return [self.embed(t) for t in texts]

    def is_available(self) -> bool:
        """Check if nomic-embed-text is available via Ollama."""
        try:
            with urllib.request.urlopen(
                f"{OLLAMA_BASE}/api/tags", timeout=2
            ) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                base = self.model.split(":")[0].lower()
                return any(m.lower().startswith(base) for m in models)
        except Exception:
            return False

    def cache_stats(self) -> dict:
        """Return cache statistics."""
        if not self._db:
            return {"cached": 0, "model": self.model}
        row = self._db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model=?", (self.model,)
        ).fetchone()
        return {"cached": row[0] if row else 0, "model": self.model}

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
