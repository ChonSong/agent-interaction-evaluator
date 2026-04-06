"""AILogger — Agent Interaction Logger IPC Server — SPEC.md §5.1.

JSON-RPC 2.0 over Unix socket with backpressure handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import alerts
from . import db as db_mod
from . import drift
from . import schema as schema_mod
from . import sanitiser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOCKET_PATH = os.environ.get(
    "AILOGGER_SOCKET",
    "/tmp/ailogger.sock",
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "evaluator" / "data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "ailogger.pid"


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

class JsonRpcError(Exception):
    """JSON-RPC 2.0 error."""
    def __init__(self, code: int, message: str, data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


INVALID_REQUEST   = -32600
METHOD_NOT_FOUND  = -32601
INVALID_PARAMS    = -32602
INTERNAL_ERROR    = -32603


def rpc_response(id: Any, result: Any) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "result": result, "id": id},
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"


def rpc_error(id: Any, code: int, message: str, data=None) -> bytes:
    err = {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": id}
    if data is not None:
        err["error"]["data"] = data
    return json.dumps(err, ensure_ascii=False).encode("utf-8") + b"\n"


def parse_request(data: bytes) -> tuple[dict, Any]:
    """Parse a JSON-RPC request. Returns (parsed_dict, id)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JsonRpcError(INVALID_REQUEST, f"Invalid JSON: {exc}")

    if not isinstance(obj, dict):
        raise JsonRpcError(INVALID_REQUEST, "Request must be a JSON object")
    if obj.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, "Only JSON-RPC 2.0 supported")
    if "method" not in obj:
        raise JsonRpcError(INVALID_REQUEST, "Missing 'method' field")

    req_id = obj.get("id")
    return obj, req_id


# ---------------------------------------------------------------------------
# AILogger core
# ---------------------------------------------------------------------------

class AILogger:
    """
    Main logger class. Handles:
    - JSON-RPC 2.0 over Unix socket
    - emit / emit_batch / status methods
    - Event validation + sanitisation
    - JSONL persistence
    - SQLite session tracking
    - txtai indexing (Phase 2 — graceful fallback if unavailable)
    - In-memory backpressure buffer
    """

    def __init__(self):
        self._start_time = datetime.now(timezone.utc)
        self._events_received = 0
        self._server_task: asyncio.Task | None = None
        self._shutdown = False

        # Backpressure buffer — events queued when txtai is unavailable
        self._buffer: list[dict] = []
        self._txtai_available = True  # assume available until proven otherwise

        # JSONL writer — one file per day
        self._jsonl_path: Path | None = None
        self._jsonl_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public JSON-RPC methods
    # ------------------------------------------------------------------

    async def handle_emit(self, event: dict) -> dict:
        """Validate, sanitise, and persist a single event."""
        # 1. Validate
        valid, err = schema_mod.validate_event(event)
        if not valid:
            raise JsonRpcError(INVALID_PARAMS, f"Event validation failed: {err}")

        # 2. Sanitise
        sanitised = sanitiser.sanitise_event(event)

        # 3. Persist to JSONL
        await self._write_jsonl(sanitised)

        # 4. Update SQLite session
        await self._update_session(sanitised)

        # 5. Attempt txtai indexing (non-blocking, graceful fallback)
        # Exceptions from _index_event are caught here to ensure events are
        # never dropped due to indexing failures.
        try:
            await self._index_event(sanitised)
        except Exception as exc:
            logger.warning("Indexer failed, event will be buffered: %s", exc)
            self._buffer_event(sanitised)

        self._events_received += 1
        return {"status": "ok", "event_id": sanitised.get("event_id")}

    async def handle_emit_batch(self, events: list[dict]) -> list[dict]:
        """Process a batch of events."""
        results = []
        for event in events:
            try:
                result = await self.handle_emit(event)
                results.append({"success": True, **result})
            except JsonRpcError as e:
                results.append({
                    "success": False,
                    "error": {"code": e.code, "message": e.message},
                })
        return results

    async def handle_status(self) -> dict:
        """Return current logger status."""
        uptime = datetime.now(timezone.utc) - self._start_time
        return {
            "events_received": self._events_received,
            "buffered": len(self._buffer),
            "logger_uptime_seconds": uptime.total_seconds(),
            "txtai_available": self._txtai_available,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_jsonl_path(self) -> Path:
        """Return today's JSONL file path, creating directories if needed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = LOG_DIR / f"{today}.jsonl"
        if self._jsonl_path != path:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = path
        return self._jsonl_path

    async def _write_jsonl(self, event: dict) -> None:
        """Append event as a JSON line to today's log file."""
        path = await self._get_jsonl_path()
        line = json.dumps(event, ensure_ascii=False) + "\n"
        async with self._jsonl_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)

    async def _update_session(self, event: dict) -> None:
        """Update session counters in SQLite."""
        session_id = event.get("session_id")
        if not session_id:
            return

        try:
            from . import db

            # Upsert session — increment event_count
            session_data = {
                "session_id": session_id,
                "agent_id": event.get("agent_id"),
                "channel": event.get("interaction_context", {}).get("channel"),
                "started_at": event.get("timestamp"),
            }
            # Simple approach: try insert, update on conflict
            # For Phase 1 we do a basic upsert via db.insert_session
            await db.insert_session(session_data)
        except Exception as exc:
            logger.warning("Failed to update session in SQLite: %s", exc)

    # ------------------------------------------------------------------
    # Drift detection — Phase 2.2
    # ------------------------------------------------------------------

    def _check_and_alert_drift(self, event: dict) -> None:
        """
        Check an assumption event for drift against prior indexed assumptions.

        If drift is found: logs to DB and sends Discord alert for critical drift.
        This is synchronous to avoid blocking the emit path — drift detection
        is best-effort and should never prevent event persistence.
        """
        try:
            detector = drift.DriftDetector()
            result = detector.check(event.get("event_id", ""), event)
            if result is None:
                return

            # Log to SQLite drift_log
            drift_entry = {
                "drift_id": f"drift-{result.event_id}",
                "current_event_id": result.event_id,
                "contradicted_event_id": result.contradicted_event_id,
                "contradiction_type": result.contradiction_type,
                "drift_score": result.drift_score,
                "action_taken": None,
                "resolved_at": None,
            }
            # Run the async insert in a new event loop (synchronous context)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # We're in async context — create a task
                loop.create_task(db_mod.insert_drift_log(drift_entry))
            except RuntimeError:
                # No running loop — create a new one
                asyncio.run(db_mod.insert_drift_log(drift_entry))

            logger.warning(
                "DRIFT DETECTED: event=%s type=%s score=%.3f",
                result.event_id,
                result.contradiction_type,
                result.drift_score,
            )

            # Alert on critical drift (score >= 0.9)
            if result.drift_score >= 0.9:
                msg = (
                    f"🚨 CRITICAL DRIFT detected\n"
                    f"Event: `{result.event_id}`\n"
                    f"Type: {result.contradiction_type}\n"
                    f"Score: {result.drift_score:.3f}\n"
                    f"Current: {result.current_statement[:100]}\n"
                    f"Prior: {result.prior_statement[:100]}"
                )
                alerts.send_alert(msg, severity="critical")

        except Exception as exc:
            logger.warning("Drift check failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Oracle evaluation — Phase 3
    # ------------------------------------------------------------------

    def _evaluate_oracles(self, event: dict) -> None:
        """
        Evaluate an event against loaded oracles (Phase 3).

        This is synchronous to avoid blocking the emit path.
        """
        try:
            from . import oracle_engine as oe
            oe.evaluate_event(event)
        except Exception as exc:
            logger.warning("Oracle evaluation failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # txtai indexing — Phase 2.2
    # ------------------------------------------------------------------

    async def _index_event(self, event: dict) -> None:
        """
        Index event in txtai and check for drift (Phase 2.2).

        Events are NEVER dropped due to indexing or drift check failures.
        """
        try:
            from . import txtai_client as tx

            client = tx.get_client()
            client.index_event(event)

            # Phase 2.2: check for drift (non-blocking, best-effort)
            self._check_and_alert_drift(event)

            # Phase 3: evaluate against oracles (non-blocking, best-effort)
            self._evaluate_oracles(event)

            if not self._txtai_available:
                self._txtai_available = True
                logger.info("txtai connection restored, %d buffered events will be replayed", len(self._buffer))
                await self._replay_buffer()
        except ImportError:
            # txtai_client not yet implemented (Phase 2) — buffer
            self._buffer_event(event)
        except Exception as exc:
            # txtai unavailable — buffer event, do NOT drop
            logger.warning("txtai indexing failed, buffering event: %s", exc)
            self._txtai_available = False
            self._buffer_event(event)

        # Phase 7: ship to Opik for UI observability — always fire, best-effort
        # This runs regardless of txtai/drift/oracle outcome
        try:
            from . import opik_client as ok
            asyncio.create_task(ok.emit_to_opik(event))
        except Exception as opik_exc:
            logger.debug("Opik emit skipped: %s", opik_exc)

    def _buffer_event(self, event: dict) -> None:
        """Add event to backpressure buffer."""
        self._buffer.append(event)

    async def _replay_buffer(self) -> None:
        """Replay buffered events once txtai is available again."""
        while self._buffer:
            event = self._buffer.pop(0)
            try:
                from . import txtai_client as tx
                client = tx.get_client()
                client.index_event(event)
                # Also check drift on replayed events
                self._check_and_alert_drift(event)
                # Also evaluate against oracles on replayed events
                self._evaluate_oracles(event)
                # Also emit to Opik on replayed events
                try:
                    from . import opik_client as ok
                    asyncio.create_task(ok.emit_to_opik(event))
                except Exception:
                    pass
            except Exception as exc:
                # Put it back at front of buffer and stop
                logger.warning("txtai re-index failed, stopping replay: %s", exc)
                self._buffer.insert(0, event)
                break

            # Emit to Opik even during replay (best-effort)
            try:
                from . import opik_client as ok
                asyncio.create_task(ok.emit_to_opik(event))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# IPC Server
# ---------------------------------------------------------------------------

async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ailogger: AILogger,
) -> None:
    """Handle a single client connection."""
    try:
        addr = writer.get_extra_info("sockname")
    except Exception:
        addr = None
    logger.debug("Client connected: %s", addr)

    try:
        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break

            line = line_bytes.strip()
            if not line:
                continue

            try:
                request, req_id = parse_request(line)
            except JsonRpcError as e:
                writer.write(rpc_error(None, e.code, e.message))
                await writer.drain()
                continue

            method = request.get("method")
            params = request.get("params", {})

            try:
                result = await dispatch_method(ailogger, method, params)
                writer.write(rpc_response(req_id, result))
            except JsonRpcError as e:
                writer.write(rpc_error(req_id, e.code, e.message, e.data))
            except Exception as exc:
                logger.exception("Unexpected error handling %s", method)
                writer.write(
                    rpc_error(req_id, INTERNAL_ERROR, f"Internal error: {exc}")
                )

            await writer.drain()

    except asyncio.CancelledError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()
        logger.debug("Client disconnected: %s", addr)


async def dispatch_method(
    ailogger: AILogger, method: str, params: dict
) -> Any:
    """Dispatch a JSON-RPC method to the appropriate handler."""
    if method == "emit":
        event = params.get("event")
        if not isinstance(event, dict):
            raise JsonRpcError(INVALID_PARAMS, "params.event is required and must be a dict")
        return await ailogger.handle_emit(event)

    elif method == "emit_batch":
        events = params.get("events")
        if not isinstance(events, list):
            raise JsonRpcError(INVALID_PARAMS, "params.events is required and must be a list")
        return await ailogger.handle_emit_batch(events)

    elif method == "status":
        return await ailogger.handle_status()

    else:
        raise JsonRpcError(METHOD_NOT_FOUND, f"Unknown method: {method!r}")


async def run_server(ailogger: AILogger, socket_path: str) -> None:
    """Start the Unix socket server."""
    # Remove stale socket file
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    server = await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, ailogger),
        socket_path,
    )

    # Make socket accessible to all users after binding
    os.chmod(socket_path, 0o777)

    logger.info("AILogger server listening on %s", socket_path)

    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    async with server:
        await server.serve_forever()

    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    if PID_FILE.exists():
        PID_FILE.unlink()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point: ailogger [serve|status|emit --stdin]."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        cmd = "serve"
    else:
        cmd = sys.argv[1]

    if cmd == "serve":
        _cmd_serve()
    elif cmd == "status":
        _cmd_status()
    elif cmd == "emit":
        _cmd_emit()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: ailogger [serve|status|emit --stdin]")
        sys.exit(1)


def _cmd_serve() -> None:
    """Start the AILogger server."""
    ailogger = AILogger()

    # Handle shutdown signals
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received")
        ailogger._shutdown = True

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    socket_path = os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    try:
        asyncio.run(run_server(ailogger, socket_path))
    except KeyboardInterrupt:
        logger.info("Server interrupted")


def _cmd_status() -> None:
    """Connect to logger socket and print status."""
    socket_path = os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": "status",
        "params": {},
        "id": 1,
    }).encode("utf-8") + b"\n"

    try:
        response = asyncio.run(_sock_communicate(socket_path, request))
        print(json.dumps(response, indent=2))
    except Exception as exc:
        print(f"Failed to connect to logger: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_emit() -> None:
    """Read a JSON event from stdin and emit it via the logger socket."""
    stdin_data = sys.stdin.read()
    if not stdin_data.strip():
        print("No event data on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        event = json.loads(stdin_data)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON on stdin: {exc}", file=sys.stderr)
        sys.exit(1)

    socket_path = os.environ.get("AILOGGER_SOCKET", "/tmp/ailogger.sock")

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": "emit",
        "params": {"event": event},
        "id": 1,
    }).encode("utf-8") + b"\n"

    try:
        response = asyncio.run(_sock_communicate(socket_path, request))
        print(json.dumps(response, indent=2))
    except Exception as exc:
        print(f"Failed to emit event: {exc}", file=sys.stderr)
        sys.exit(1)


async def _sock_communicate(
    socket_path: str, request: bytes
) -> dict:
    """Connect to Unix socket, send request, read and return the JSON response."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.write(request)
    await writer.drain()
    response_bytes = await reader.readline()
    writer.close()
    await writer.wait_closed()
    if not response_bytes:
        raise RuntimeError("Empty response from logger server")
    return json.loads(response_bytes.decode("utf-8"))


if __name__ == "__main__":
    main()
