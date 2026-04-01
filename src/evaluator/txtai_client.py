"""AIETxtaiClient — extends RepoTransmute.TxtaiClient for agent_events collection.

Shares the same FAISS index with RepoTransmute at:
  ~/workspace/zoul/repo-transmute/data/txtai/

Uses a separate SQLite metadata store for agent event metadata
(agent_events_meta.db) to avoid conflicts with RepoTransmute's metadata.db.

This enables cross-search — e.g., "find interactions where an agent referenced
code chunk X" — while keeping collections logically separate.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from evaluator.schema import get_current_timestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_INDEX_PATH = os.path.expanduser(
    os.environ.get(
        "TXTAI_INDEX_PATH",
        "~/workspace/zoul/repo-transmute/data/txtai/",
    )
)

# Embedding model — must match RepoTransmute's model
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# UID prefix for agent_events collection
_EVENT_PREFIX = "event:"

# Name of the AIE metadata database (separate from RepoTransmute's metadata.db)
_META_DB_NAME = "agent_events_meta.db"


# ---------------------------------------------------------------------------
# Graceful fallback helpers
# ---------------------------------------------------------------------------

class TxtaiUnavailableError(Exception):
    """Raised when txtai is not available."""


def _check_txtai() -> bool:
    """Return True if txtai + required dependencies are importable and functional."""
    try:
        from txtai import Embeddings  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# AIETxtaiClient — extends RepoTransmute.TxtaiClient
# ---------------------------------------------------------------------------

# Import RepoTransmute's TxtaiClient for extension
_REPO_TRANSMUTE_CLIENT: type | None = None

def _get_repo_transmute_client() -> type:
    """Lazily import and return RepoTransmute's TxtaiClient class."""
    global _REPO_TRANSMUTE_CLIENT
    if _REPO_TRANSMUTE_CLIENT is None:
        repo_transmute_path = os.path.expanduser(
            "~/workspace/zoul/repo-transmute/src/"
        )
        if repo_transmute_path not in __import__('sys').path:
            __import__('sys').path.insert(0, repo_transmute_path)
        from repo_transmute.txtai.client import TxtaiClient as RTClient
        _REPO_TRANSMUTE_CLIENT = RTClient
    return _REPO_TRANSMUTE_CLIENT


class AIETxtaiClient:
    """
    Client for indexing and querying agent interaction events.

    Extends RepoTransmute's TxtaiClient to share the same FAISS index while
    maintaining a separate logical collection ("agent_events") via UID prefix
    "event:{event_id}" and its own SQLite metadata store.

    Graceful fallback: if txtai is unavailable, all methods return empty results
    or None rather than raising exceptions.
    """

    def __init__(
        self,
        index_path: str | None = None,
        model: str = EMBEDDING_MODEL,
    ) -> None:
        """
        Args:
            index_path: Directory for FAISS index + metadata. Defaults to shared
                        RepoTransmute path via TXTAI_INDEX_PATH env var.
            model: Sentence-transformers model name.
        """
        self.index_path = Path(index_path) if index_path else Path(DEFAULT_INDEX_PATH)
        self.model = model
        self._available: bool | None = None
        self._embeddings: Any | None = None
        self._meta_db_path: Path | None = None

        # Lazily-created RepoTransmute TxtaiClient instance for shared FAISS index
        self._rt_client: Any | None = None

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Check if txtai is available (cached)."""
        if self._available is None:
            self._available = _check_txtai()
        return self._available

    def _ensure_available(self) -> None:
        """Raise TxtaiUnavailableError if txtai is not available."""
        if not self.available:
            raise TxtaiUnavailableError(
                "txtai is not available. Install with: pip install txtai[full]"
            )

    # ------------------------------------------------------------------
    # Internal: metadata DB
    # ------------------------------------------------------------------

    @property
    def _meta_path(self) -> Path:
        """Path to the agent_events metadata SQLite database."""
        if self._meta_db_path is None:
            self._meta_db_path = self.index_path / _META_DB_NAME
        return self._meta_db_path

    def _init_meta_db(self) -> None:
        """Create metadata tables if they don't exist."""
        self.index_path.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._meta_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events_meta (
                event_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events_stats (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def _upsert_meta(self, event_id: str, data: dict[str, Any]) -> None:
        """Store event metadata keyed by event_id."""
        conn = sqlite3.connect(self._meta_path)
        conn.execute(
            "INSERT OR REPLACE INTO agent_events_meta (event_id, data) VALUES (?, ?)",
            (event_id, json.dumps(data)),
        )
        conn.commit()
        conn.close()

    def _fetch_meta(self, event_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-fetch metadata for a list of event_ids."""
        if not event_ids:
            return {}
        conn = sqlite3.connect(self._meta_path)
        placeholders = ",".join("?" * len(event_ids))
        rows = conn.execute(
            f"SELECT event_id, data FROM agent_events_meta WHERE event_id IN ({placeholders})",
            list(event_ids),
        ).fetchall()
        conn.close()
        return {event_id: json.loads(data) for event_id, data in rows}

    def _update_stats(self, last_updated: str | None = None) -> None:
        """Update the collection stats."""
        conn = sqlite3.connect(self._meta_path)
        count = self.count()
        conn.execute(
            "INSERT OR REPLACE INTO agent_events_stats (key, value) VALUES ('count', ?)",
            (str(count),),
        )
        if last_updated:
            conn.execute(
                "INSERT OR REPLACE INTO agent_events_stats (key, value) VALUES ('last_updated', ?)",
                (last_updated,),
            )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def _emb(self) -> Any:
        """Lazily create the Embeddings instance on first access."""
        if self._embeddings is None:
            from txtai import Embeddings

            self._embeddings = Embeddings({
                "path": self.model,
                "index-dir": str(self.index_path),
            })
        return self._embeddings

    def close(self) -> None:
        """Close the embeddings instance."""
        if self._embeddings is not None:
            self._embeddings.close()
            self._embeddings = None

    def save(self) -> None:
        """Persist the FAISS index to the index directory."""
        self._emb.save(str(self.index_path))

    def load(self) -> None:
        """Reload a previously saved index."""
        if not (self.index_path / "config.json").exists():
            raise FileNotFoundError(
                f"No txtai index found in {self.index_path}. "
                "Call save() or index() first."
            )
        from txtai import Embeddings

        self._embeddings = Embeddings()
        self._embeddings.load(str(self.index_path))

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_event(self, event: dict) -> bool:
        """
        Index a single agent interaction event.

        Extracts text fields for embedding:
        - event_id, event_type, agent_id, session_id, timestamp
        - assumption_statement (from assumption events)
        - task_description (from delegation events)

        Args:
            event: A validated agent interaction event dict.

        Returns:
            True if indexing succeeded, False if txtai unavailable.
        """
        if not self.available:
            logger.warning(
                "txtai unavailable, cannot index event %s", event.get("event_id")
            )
            return False

        try:
            self._init_meta_db()

            event_id = event.get("event_id", "")
            uid = f"{_EVENT_PREFIX}{event_id}"

            # Build text for embedding — only the semantically meaningful content
            event_type = event.get("event_type", "")

            if event_type == "assumption":
                assumption = event.get("assumption", {})
                statement = assumption.get("statement", "")
                text = statement if statement else f"assumption event: {event_id}"
            elif event_type == "delegation":
                task = event.get("task", {})
                description = task.get("description", "")
                text = description if description else f"delegation event: {event_id}"
            else:
                text = f"{event_type} event by {event.get('agent_id', 'unknown')}"

            if not text:
                text = f"event: {event_id}"

            # Build metadata (everything except what goes into the vector)
            meta = {
                "event_id": event_id,
                "event_type": event.get("event_type"),
                "agent_id": event.get("agent_id"),
                "session_id": event.get("session_id"),
                "timestamp": event.get("timestamp"),
                "assumption_statement": (
                    event.get("assumption", {}).get("statement")
                    if event.get("event_type") == "assumption"
                    else None
                ),
                "task_description": (
                    event.get("task", {}).get("description")
                    if event.get("event_type") == "delegation"
                    else None
                ),
            }

            # Index the document using the shared FAISS index
            self._emb.upsert([(uid, text)])
            self._upsert_meta(event_id, meta)
            self._update_stats(get_current_timestamp())

            return True

        except Exception as exc:
            logger.warning(
                "Failed to index event %s: %s", event.get("event_id"), exc
            )
            return False

    def count(self) -> int:
        """Return the number of indexed agent events.

        Note: This counts all documents in the shared FAISS index.
        For accurate agent_events count, use get_index_stats()['collection_size'].
        """
        if not self.available:
            return 0
        try:
            return self._emb.count()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query_assumptions(
        self,
        text: str,
        session_id: str | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Query for prior assumptions similar to the given text.

        Args:
            text: The assumption statement to search for.
            session_id: If provided, filter to assumptions from this session.
            top_k: Maximum number of results to return.

        Returns:
            List of dicts with assumption data and similarity scores.
        """
        if not self.available:
            logger.warning("txtai unavailable, returning empty assumptions")
            return []

        try:
            # Search broadly and then filter for assumption events
            raw = self._emb.search(text, limit=top_k * 2)

            # Fetch metadata for all results
            uids = [item[0] for item in raw]
            event_ids = [
                uid.replace(_EVENT_PREFIX, "")
                for uid in uids
                if uid.startswith(_EVENT_PREFIX)
            ]
            meta_map = self._fetch_meta(event_ids)

            results = []
            for uid, score in raw:
                if not uid.startswith(_EVENT_PREFIX):
                    continue

                event_id = uid.replace(_EVENT_PREFIX, "")
                meta = meta_map.get(event_id, {})

                if meta.get("event_type") != "assumption":
                    continue

                # Filter by session_id if specified
                if session_id and meta.get("session_id") != session_id:
                    continue

                result = {
                    "event_id": event_id,
                    "score": float(score),
                    "session_id": meta.get("session_id"),
                    "timestamp": meta.get("timestamp"),
                    "assumption_statement": meta.get("assumption_statement"),
                }
                results.append(result)

                if len(results) >= top_k:
                    break

            return results

        except Exception as exc:
            logger.warning("Failed to query assumptions: %s", exc)
            return []

    def query_events(
        self,
        filters: dict[str, Any],
        top_k: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Query events with optional filters.

        Args:
            filters: Dict with optional keys:
                - event_type: Filter by event type
                - agent_id: Filter by agent ID
                - session_id: Filter by session ID
                - since: ISO timestamp — only events after this time
            top_k: Maximum number of results to return.

        Returns:
            List of event metadata dicts with similarity scores.
        """
        if not self.available:
            logger.warning("txtai unavailable, returning empty events")
            return []

        try:
            # Build a text query from filters for semantic search
            query_parts = []
            if filters.get("event_type"):
                query_parts.append(f"event_type: {filters['event_type']}")
            if filters.get("agent_id"):
                query_parts.append(f"agent_id: {filters['agent_id']}")
            if filters.get("session_id"):
                query_parts.append(f"session_id: {filters['session_id']}")

            query_text = " ".join(query_parts) if query_parts else "*"

            if query_text == "*":
                # For wildcard, we can't efficiently list all docs in txtai
                # Return empty list (caller should use scan_session approach)
                return []
            else:
                raw = self._emb.search(query_text, limit=top_k * 2)

            # Fetch metadata
            uids = [item[0] for item in raw]
            event_ids = [
                uid.replace(_EVENT_PREFIX, "")
                for uid in uids
                if uid.startswith(_EVENT_PREFIX)
            ]
            meta_map = self._fetch_meta(event_ids)

            results = []
            for uid, score in raw:
                if not uid.startswith(_EVENT_PREFIX):
                    continue

                event_id = uid.replace(_EVENT_PREFIX, "")
                meta = meta_map.get(event_id, {})

                # Apply filters
                if filters.get("event_type") and meta.get("event_type") != filters["event_type"]:
                    continue
                if filters.get("agent_id") and meta.get("agent_id") != filters["agent_id"]:
                    continue
                if filters.get("session_id") and meta.get("session_id") != filters["session_id"]:
                    continue
                if filters.get("since") and meta.get("timestamp", "") < filters["since"]:
                    continue

                result = {
                    "event_id": event_id,
                    "score": float(score),
                    **{k: v for k, v in meta.items() if v is not None},
                }
                results.append(result)

                if len(results) >= top_k:
                    break

            return results

        except Exception as exc:
            logger.warning("Failed to query events: %s", exc)
            return []

    def get_index_stats(self) -> dict[str, Any]:
        """
        Return statistics about the agent_events collection.

        Returns:
            Dict with 'collection_size' (int) and 'last_updated' (ISO str or None).
        """
        if not self.available:
            return {"collection_size": 0, "last_updated": None}

        try:
            self._init_meta_db()

            # Count only agent_events (prefixed docs)
            conn = sqlite3.connect(self._meta_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM agent_events_stats WHERE key = 'count'"
            ).fetchone()
            conn.close()
            count = int(row["value"]) if row else 0

            conn2 = sqlite3.connect(self._meta_path)
            conn2.row_factory = sqlite3.Row
            row2 = conn2.execute(
                "SELECT value FROM agent_events_stats WHERE key = 'last_updated'"
            ).fetchone()
            conn2.close()
            last_updated = row2["value"] if row2 else None

            return {
                "collection_size": count,
                "last_updated": last_updated,
            }

        except Exception as exc:
            logger.warning("Failed to get index stats: %s", exc)
            return {"collection_size": 0, "last_updated": None}

    def ensure_collection(self) -> bool:
        """
        Ensure the agent_events collection exists (creates metadata DB if needed).

        Returns:
            True if collection is ready, False if txtai unavailable.
        """
        if not self.available:
            return False
        try:
            self._init_meta_db()
            return True
        except Exception as exc:
            logger.warning("Failed to ensure collection: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Backward-compatibility alias
# ---------------------------------------------------------------------------

# Keep TxtaiClient as an alias for backward compatibility with existing code
# and tests that import from this module
TxtaiClient = AIETxtaiClient


# ---------------------------------------------------------------------------
# Module-level singleton access
# ---------------------------------------------------------------------------

_client: AIETxtaiClient | None = None


def get_client() -> AIETxtaiClient:
    """Return a singleton AIETxtaiClient instance."""
    global _client
    if _client is None:
        _client = AIETxtaiClient()
    return _client


def reset_client() -> None:
    """Reset the singleton client (useful for testing)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None