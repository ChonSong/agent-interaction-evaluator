"""AILoggerClient — thin client for emitting events to the AILogger IPC server.

SPEC.md §8.1 Option A.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any


class AILoggerClient:
    """
    Async client for the AILogger Unix socket server.

    Usage:
        async with AILoggerClient() as client:
            await client.emit(event)
            await client.emit_batch([event1, event2])
            status = await client.status()

    Or:
        client = AILoggerClient()
        await client.connect()
        # ... use client
        await client.close()
    """

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or os.environ.get(
            "AILOGGER_SOCKET", "/tmp/ailogger.sock"
        )
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "AILoggerClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Establish connection to the logger socket."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            self.socket_path
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def _send_request(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and return the result."""
        if self._writer is None:
            raise RuntimeError("Not connected. Call connect() first or use async with.")

        request_id = id(self) & 0xFFFFFF
        request = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }, ensure_ascii=False).encode("utf-8") + b"\n"

        async with self._lock:
            self._writer.write(request)
            await self._writer.drain()

            # Read response
            if self._reader is None:
                raise RuntimeError("Reader not available")
            response_bytes = await self._reader.readline()
            if not response_bytes:
                raise RuntimeError("Empty response from server")

        response = json.loads(response_bytes.decode("utf-8"))

        # Check for JSON-RPC error
        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"JSON-RPC error {err.get('code')}: {err.get('message')}"
            )

        return response.get("result")

    async def emit(self, event: dict) -> bool:
        """
        Emit a single event.

        Returns:
            True on success, raises RuntimeError on failure.
        """
        try:
            result = await self._send_request("emit", {"event": event})
            return result.get("status") == "ok"
        except Exception:
            raise

    async def emit_batch(self, events: list[dict]) -> list[dict]:
        """
        Emit a batch of events.

        Returns:
            List of result dicts (one per event).
        """
        return await self._send_request("emit_batch", {"events": events})

    async def status(self) -> dict:
        """
        Get logger status.

        Returns:
            dict with events_received, buffered, logger_uptime_seconds.
        """
        return await self._send_request("status", {})
