"""Tests for AILogger IPC server — REQUIREMENTS.md P1.5."""

import sys
import os
import asyncio
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from evaluator.logger import (
    AILogger,
    JsonRpcError,
    dispatch_method,
    parse_request,
    rpc_error,
    rpc_response,
)
from evaluator.schema import generate_event_id, get_current_timestamp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir():
    """Create a temporary data directory for log files."""
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    shutil.rmtree(tmpdir)


@pytest.fixture
def valid_delegation_event():
    return {
        "schema_version": "1.0",
        "event_id": generate_event_id(),
        "event_type": "delegation",
        "timestamp": get_current_timestamp(),
        "agent_id": "agent-1",
        "session_id": "session-test-1",
        "interaction_context": {
            "channel": "test",
            "workspace_path": None,
            "parent_event_id": None,
        },
        "delegator": {"agent_id": "agent-1", "role": "orchestrator"},
        "delegate": {"agent_id": "agent-2", "role": "worker"},
        "task": {
            "task_id": "task-1",
            "description": "Test task",
            "intent": "Testing",
            "constraints": [],
            "context_summary": "This is a test delegation event for unit testing",
            "context_fidelity": 0.9,
            "max_turns": 5,
            "deadline": None,
        },
        "oracle_ref": None,
    }


@pytest.fixture
def valid_tool_call_event():
    return {
        "schema_version": "1.0",
        "event_id": generate_event_id(),
        "event_type": "tool_call",
        "timestamp": get_current_timestamp(),
        "agent_id": "agent-1",
        "session_id": "session-test-1",
        "interaction_context": {
            "channel": "test",
            "workspace_path": None,
            "parent_event_id": None,
        },
        "tool": {
            "name": "read_file",
            "namespace": "filesystem",
            "arguments": {"path": "/tmp/test.txt"},
            "argument_schema": None,
        },
        "trigger": {"type": "user_request", "triggered_by_event_id": None},
        "outcome": {
            "status": "success",
            "duration_ms": 42,
            "error_message": None,
            "output_summary": "Read 128 bytes",
        },
    }


# ---------------------------------------------------------------------------
# Test JSON-RPC helpers
# ---------------------------------------------------------------------------

def test_parse_request_valid():
    data = b'{"jsonrpc": "2.0", "method": "status", "params": {}, "id": 1}'
    obj, rid = parse_request(data)
    assert obj["method"] == "status"
    assert obj["params"] == {}
    assert rid == 1


def test_parse_request_invalid_json():
    with pytest.raises(JsonRpcError) as exc_info:
        parse_request(b"not valid json")
    assert exc_info.value.code == -32600


def test_parse_request_missing_method():
    with pytest.raises(JsonRpcError) as exc_info:
        parse_request(b'{"jsonrpc": "2.0", "params": {}, "id": 1}')
    assert exc_info.value.code == -32600


def test_parse_request_wrong_version():
    with pytest.raises(JsonRpcError) as exc_info:
        parse_request(b'{"jsonrpc": "1.0", "method": "status", "params": {}, "id": 1}')
    assert exc_info.value.code == -32600


def test_rpc_response():
    result = {"events_received": 5, "buffered": 0, "logger_uptime_seconds": 10.5}
    response_bytes = rpc_response(id=1, result=result)
    response = json.loads(response_bytes.decode("utf-8"))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"] == result


def test_rpc_error():
    err_bytes = rpc_error(id=1, code=-32600, message="Invalid request")
    err = json.loads(err_bytes.decode("utf-8"))
    assert err["jsonrpc"] == "2.0"
    assert err["error"]["code"] == -32600
    assert err["error"]["message"] == "Invalid request"
    assert err["id"] == 1


# ---------------------------------------------------------------------------
# Test AILogger.handle_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_status_returns_counts():
    ailogger = AILogger()
    status = await ailogger.handle_status()

    assert "events_received" in status
    assert "buffered" in status
    assert "logger_uptime_seconds" in status
    assert status["events_received"] == 0
    assert status["buffered"] == 0
    assert status["logger_uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# Test AILogger.handle_emit — validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_emit_valid_event(valid_delegation_event, tmp_data_dir):
    ailogger = AILogger()

    # Patch _write_jsonl and _update_session to avoid file system dependencies
    with patch.object(ailogger, "_write_jsonl", new_callable=AsyncMock) as mock_write, \
         patch.object(ailogger, "_update_session", new_callable=AsyncMock) as mock_session, \
         patch.object(ailogger, "_index_event", new_callable=AsyncMock):

        result = await ailogger.handle_emit(valid_delegation_event)

        assert result["status"] == "ok"
        assert result["event_id"] == valid_delegation_event["event_id"]
        mock_write.assert_called_once()
        mock_session.assert_called_once()


@pytest.mark.asyncio
async def test_handle_emit_invalid_event():
    ailogger = AILogger()

    with pytest.raises(JsonRpcError) as exc_info:
        await ailogger.handle_emit({"event_type": "delegation"})  # missing required fields
    assert exc_info.value.code == -32602


@pytest.mark.asyncio
async def test_handle_emit_unknown_event_type():
    ailogger = AILogger()
    event = {
        "schema_version": "1.0",
        "event_id": generate_event_id(),
        "event_type": "unknown_type",
        "timestamp": get_current_timestamp(),
        "agent_id": "agent-1",
        "session_id": "session-1",
        "interaction_context": {"channel": "test"},
    }
    with pytest.raises(JsonRpcError) as exc_info:
        await ailogger.handle_emit(event)
    assert "Unknown event_type" in exc_info.value.message


# ---------------------------------------------------------------------------
# Test AILogger.handle_emit_batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_emit_batch(valid_delegation_event, valid_tool_call_event):
    ailogger = AILogger()

    events = [valid_delegation_event, valid_tool_call_event]

    with patch.object(ailogger, "_write_jsonl", new_callable=AsyncMock), \
         patch.object(ailogger, "_update_session", new_callable=AsyncMock), \
         patch.object(ailogger, "_index_event", new_callable=AsyncMock):

        results = await ailogger.handle_emit_batch(events)

    assert len(results) == 2
    assert results[0]["success"] is True
    assert results[1]["success"] is True


@pytest.mark.asyncio
async def test_handle_emit_batch_partial_failure(valid_delegation_event):
    ailogger = AILogger()

    events = [
        valid_delegation_event,
        {"event_type": "unknown"},  # will fail
    ]

    with patch.object(ailogger, "_write_jsonl", new_callable=AsyncMock), \
         patch.object(ailogger, "_update_session", new_callable=AsyncMock), \
         patch.object(ailogger, "_index_event", new_callable=AsyncMock):

        results = await ailogger.handle_emit_batch(events)

    assert results[0]["success"] is True
    assert results[1]["success"] is False
    assert "error" in results[1]


# ---------------------------------------------------------------------------
# Test backpressure buffering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_buffer_event_on_txtai_failure(valid_delegation_event):
    ailogger = AILogger()
    ailogger._txtai_available = False

    with patch.object(ailogger, "_write_jsonl", new_callable=AsyncMock), \
         patch.object(ailogger, "_update_session", new_callable=AsyncMock), \
         patch.object(ailogger, "_index_event", new_callable=AsyncMock) as mock_index:

        # Simulate index_event raising an exception
        mock_index.side_effect = Exception("txtai unavailable")

        await ailogger.handle_emit(valid_delegation_event)

    assert len(ailogger._buffer) == 1
    assert ailogger._buffer[0]["event_id"] == valid_delegation_event["event_id"]


@pytest.mark.asyncio
async def test_status_returns_buffered_count(valid_delegation_event):
    ailogger = AILogger()
    ailogger._events_received = 10
    ailogger._buffer.extend([valid_delegation_event, valid_delegation_event])

    status = await ailogger.handle_status()

    assert status["events_received"] == 10
    assert status["buffered"] == 2


# ---------------------------------------------------------------------------
# Test dispatch_method
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_emit(valid_delegation_event):
    ailogger = AILogger()

    with patch.object(ailogger, "handle_emit", new_callable=AsyncMock) as mock_emit:
        mock_emit.return_value = {"status": "ok", "event_id": "test-id"}

        result = await dispatch_method(ailogger, "emit", {"event": valid_delegation_event})

        mock_emit.assert_called_once_with(valid_delegation_event)
        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_emit_batch(valid_delegation_event):
    ailogger = AILogger()

    with patch.object(ailogger, "handle_emit_batch", new_callable=AsyncMock) as mock:
        mock.return_value = [{"success": True}]

        result = await dispatch_method(
            ailogger, "emit_batch", {"events": [valid_delegation_event]}
        )

        assert result == [{"success": True}]


@pytest.mark.asyncio
async def test_dispatch_status():
    ailogger = AILogger()
    status = await dispatch_method(ailogger, "status", {})

    assert "events_received" in status
    assert "buffered" in status


@pytest.mark.asyncio
async def test_dispatch_unknown_method():
    ailogger = AILogger()

    with pytest.raises(JsonRpcError) as exc_info:
        await dispatch_method(ailogger, "unknown_method", {})
    assert exc_info.value.code == -32601


@pytest.mark.asyncio
async def test_dispatch_emit_missing_event():
    ailogger = AILogger()

    with pytest.raises(JsonRpcError) as exc_info:
        await dispatch_method(ailogger, "emit", {"event": "not-a-dict"})
    assert exc_info.value.code == -32602


# ---------------------------------------------------------------------------
# Test _write_jsonl persistence (real file system)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_jsonl_creates_file(valid_delegation_event, tmp_data_dir):
    ailogger = AILogger()

    # Patch LOG_DIR to use our temp directory
    import evaluator.logger as logger_mod
    original_log_dir = logger_mod.LOG_DIR
    logger_mod.LOG_DIR = tmp_data_dir / "logs"
    ailogger._jsonl_path = None

    try:
        await ailogger._write_jsonl(valid_delegation_event)

        # Find the written JSONL file
        jsonl_files = list(logger_mod.LOG_DIR.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        # Read and verify content
        with open(jsonl_files[0]) as fh:
            line = fh.readline()
        parsed = json.loads(line)
        assert parsed["event_id"] == valid_delegation_event["event_id"]
    finally:
        logger_mod.LOG_DIR = original_log_dir


# ---------------------------------------------------------------------------
# Test sanitisation integration in emit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_sanitises_secrets(valid_tool_call_event):
    ailogger = AILogger()

    # Add a secret to the event
    valid_tool_call_event["tool"]["arguments"]["api_key"] = "sk-secret-123"

    with patch.object(ailogger, "_write_jsonl", new_callable=AsyncMock) as mock_write, \
         patch.object(ailogger, "_update_session", new_callable=AsyncMock), \
         patch.object(ailogger, "_index_event", new_callable=AsyncMock):

        await ailogger.handle_emit(valid_tool_call_event)

        # Check that the event written to JSONL was sanitised
        written_event = mock_write.call_args[0][0]
        assert written_event["tool"]["arguments"]["api_key"] == "[REDACTED]"
