"""OpikLogger — ships AIE events to Opik via REST API.

Opik ( Comet ML ) provides a UI for browsing traces, running LLM-as-judge
evaluations, and managing datasets.  This client wraps the Opik REST API
so the AIE pipeline can emit traces without depending on Opik's broken SDK
async streamer.

API base: http://localhost:5173/api
Docs:    http://localhost:5173 (running in "production" mode)

Trace naming convention:
  Each AIE event_type → its own trace.
  project_name = "aie-events" (all AIE traces live here).
  The trace metadata carries the full event so nothing is lost.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPIK_BASE_URL = os.environ.get(
    "OPIK_BASE_URL",
    "http://localhost:5173/api",
)
OPIK_API_KEY = os.environ.get("OPIK_API_KEY", "local-dev")
OPIK_PROJECT = os.environ.get("OPIK_PROJECT", "aie-events")
OPIK_TIMEOUT = float(os.environ.get("OPIK_TIMEOUT", "10.0"))


class OpikUnavailableError(Exception):
    """Raised when Opik cannot be reached."""


# ---------------------------------------------------------------------------
# Core client
# ---------------------------------------------------------------------------

class OpikClient:
    """
    Thin REST client for the Opik local API.

    Handles:
    - Trace creation (POST /v1/private/traces)
    - Project / dataset listing (GET /v1/private/*)
    - Health check

    Does NOT use the Opik Python SDK — the SDK's async streamer POSTs to
    a GET-only redirect endpoint and silently drops all traces.
    """

    def __init__(
        self,
        base_url: str = OPIK_BASE_URL,
        api_key: str = OPIK_API_KEY,
        project_name: str = OPIK_PROJECT,
        timeout: float = OPIK_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_name = project_name
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OpikClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        """Return True if the Opik backend is reachable."""
        try:
            client = await self._get_client()
            r = await client.get("/v1/private/projects/")
            return r.status_code == 200
        except Exception as exc:
            logger.warning("Opik health check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    async def create_trace(
        self,
        name: str,
        input_data: Any,
        output_data: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        trace_id: str | None = None,
    ) -> str | None:
        """
        Create a trace in Opik.

        Returns the Opik trace ID (UUID) on success, None on failure.
        Failures are logged but never raised — Opik is best-effort.
        """
        try:
            client = await self._get_client()
            payload: dict[str, Any] = {
                "project_name": self.project_name,
                "name": name,
                "input": input_data,
                "start_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            if output_data is not None:
                payload["output"] = output_data
            if metadata:
                payload["metadata"] = metadata
            if tags:
                payload["tags"] = tags
            # NOTE: Opik requires version-7 UUIDs for trace IDs. We let Opik generate
            # the ID (omitting the "id" field) and extract it from the Location header.

            r = await client.post("/v1/private/traces", json=payload)
            if r.status_code in (200, 201):
                # Trace created. ID is in the Location header (Opik redirects on create).
                loc = r.headers.get("location", "")
                trace_id = loc.split("/")[-1] if loc else None
                return trace_id
            logger.warning(
                "Opik trace creation failed (%d): %s — %s",
                r.status_code, name, r.text[:200],
            )
        except Exception as exc:
            logger.warning("Opik trace creation error: %s — %s", name, exc)
        return None

    async def create_span_trace(
        self,
        event: dict,
    ) -> str | None:
        """
        Emit a single AIE event as an Opik trace.

        Maps:
          event_type         → trace name
          full event dict    → input.metadata
          agent_id          → metadata.agent_id
          session_id        → metadata.session_id
          channel           → metadata.channel
        """
        event_type = event.get("event_type", "unknown")
        event_id = event.get("event_id", "")

        # Build structured input so it renders nicely in the Opik UI
        input_data: dict[str, Any] = {
            "event_type": event_type,
            "event_id": event_id,
            "agent_id": event.get("agent_id"),
            "session_id": event.get("session_id"),
            "timestamp": event.get("timestamp"),
            "channel": event.get("interaction_context", {}).get("channel"),
        }

        # Embed type-specific payload in input for full fidelity
        type_specific_keys = {
            "delegation": ("delegator", "delegate", "task"),
            "tool_call": ("tool", "tool_result"),
            "assumption": ("assumption",),
            "correction": ("prior_event_id", "current_assumption", "contradicted_by"),
            "drift_detected": ("drift_score", "contradicted_event_id"),
            "circuit_breaker": ("trigger", "reason"),
            "human_input": ("human_message",),
        }

        for key in type_specific_keys.get(event_type, ()):
            if key in event:
                input_data[key] = event[key]

        metadata: dict[str, Any] = {
            "schema_version": event.get("schema_version"),
            "workspace_path": event.get("interaction_context", {}).get("workspace_path"),
            "parent_event_id": event.get("interaction_context", {}).get("parent_event_id"),
        }

        tags = [event_type]
        if event.get("drift_detected"):
            tags.append("drift")
        if event.get("circuit_breaker"):
            tags.append("circuit-breaker")

        return await self.create_trace(
            name=event_type,
            input_data=input_data,
            output_data=None,
            metadata=metadata,
            tags=tags,
        )

    # ------------------------------------------------------------------
    # Projects & Datasets
    # ------------------------------------------------------------------

    async def ensure_project_exists(self) -> bool:
        """Create the AIE project in Opik if it doesn't exist. Idempotent."""
        try:
            client = await self._get_client()
            r = await client.get("/v1/private/projects/")
            if r.status_code != 200:
                return False
            data = r.json()
            for proj in data.get("content", []):
                if proj.get("name") == self.project_name:
                    return True

            # Create it
            r2 = await client.post(
                "/v1/private/projects/",
                json={"name": self.project_name, "visibility": "private"},
            )
            return r2.status_code in (200, 201, 422)
        except Exception as exc:
            logger.warning("Opik ensure_project_exists failed: %s", exc)
            return False

    async def get_project_stats(self) -> dict[str, Any]:
        """Return trace counts per project."""
        try:
            client = await self._get_client()
            r = await client.get("/v1/private/projects/")
            if r.status_code != 200:
                return {}
            data = r.json()
            return {p["name"]: p.get("last_updated_trace_at", "never")
                    for p in data.get("content", [])}
        except Exception as exc:
            logger.warning("Opik get_project_stats failed: %s", exc)
            return {}


# ---------------------------------------------------------------------------
# Sync wrapper for the logger's synchronous emit path
# ---------------------------------------------------------------------------

_sync_opik_client: OpikClient | None = None


def get_sync_opik_client() -> OpikClient:
    global _sync_opik_client
    if _sync_opik_client is None:
        _sync_opik_client = OpikClient()
    return _sync_opik_client


async def emit_to_opik(event: dict) -> None:
    """Fire-and-forget emit to Opik. Does not block the logger emit path."""
    client = get_sync_opik_client()
    if not await client.is_healthy():
        return
    await client.ensure_project_exists()
    await client.create_span_trace(event)
