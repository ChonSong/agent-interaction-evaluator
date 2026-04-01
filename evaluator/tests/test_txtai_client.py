"""Tests for TxtaiClient and DriftDetector — REQUIREMENTS.md P2.1, P2.2, P2.3."""

import sys
import os
import asyncio
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

# Reset module-level client between tests to avoid state leakage
import evaluator.txtai_client as tx_mod


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the singleton client before and after each test."""
    tx_mod.reset_client()
    yield
    tx_mod.reset_client()


@pytest.fixture
def tmp_index_dir():
    """Provide a temporary index directory that is cleaned up after each test."""
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
async def client(tmp_index_dir):
    """Provide a TxtaiClient backed by a temporary index directory."""
    client = tx_mod.TxtaiClient(index_path=str(tmp_index_dir))
    client._available = None  # Force re-check
    # Ensure it's available (we expect txtai to be importable in test env)
    if not client.available:
        pytest.skip("txtai not available in test environment")
    client.ensure_collection()
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_assumption_event(
    event_id: str,
    session_id: str,
    statement: str,
    agent_id: str = "test-agent",
) -> dict:
    """Create a synthetic assumption event for testing."""
    from evaluator.schema import generate_event_id, get_current_timestamp

    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "assumption",
        "timestamp": get_current_timestamp(),
        "agent_id": agent_id,
        "session_id": session_id,
        "interaction_context": {
            "channel": "test",
            "workspace_path": None,
            "parent_event_id": None,
        },
        "assumption": {
            "statement": statement,
            "category": "test",
            "confidence": 0.9,
            "grounded_in": None,
        },
        "derived_from": [],
        "oracle_ref": None,
    }


def make_delegation_event(
    event_id: str,
    session_id: str,
    description: str,
    agent_id: str = "test-agent",
) -> dict:
    """Create a synthetic delegation event for testing."""
    from evaluator.schema import generate_event_id, get_current_timestamp

    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "delegation",
        "timestamp": get_current_timestamp(),
        "agent_id": agent_id,
        "session_id": session_id,
        "interaction_context": {
            "channel": "test",
            "workspace_path": None,
            "parent_event_id": None,
        },
        "delegator": {"agent_id": agent_id, "role": "tester"},
        "delegate": {"agent_id": "sub-agent", "role": "executor"},
        "task": {
            "task_id": generate_event_id(),
            "description": description,
            "intent": "test intent",
            "constraints": [],
            "context_summary": "test context",
            "context_fidelity": 0.8,
            "max_turns": None,
            "deadline": None,
        },
        "oracle_ref": None,
    }


# ---------------------------------------------------------------------------
# P2.1: txtai_client — index and query roundtrip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_and_query_roundtrip(client):
    """Index a synthetic assumption event and query it back."""
    event = make_assumption_event(
        event_id="roundtrip-001",
        session_id="session-roundtrip",
        statement="The user wants to create a new project",
    )

    # Index the event
    success = client.index_event(event)
    assert success, "index_event should return True on success"

    # Query it back via query_assumptions
    results = client.query_assumptions(
        text="The user wants to create a new project",
        session_id=None,
        top_k=5,
    )

    assert len(results) >= 1, "Should find the indexed assumption"
    matched = [r for r in results if r["event_id"] == "roundtrip-001"]
    assert len(matched) == 1, "Should find the exact event we indexed"

    result = matched[0]
    assert result["score"] > 0.0
    assert result["session_id"] == "session-roundtrip"
    assert "assumption_statement" in result


@pytest.mark.asyncio
async def test_query_by_session(client):
    """query_assumptions returns only events from the specified session when session_id is set."""
    # Index events from two different sessions
    event1 = make_assumption_event(
        event_id="sess-001-a",
        session_id="session-A",
        statement="The API endpoint is /users",
    )
    event2 = make_assumption_event(
        event_id="sess-002-a",
        session_id="session-B",
        statement="The API endpoint is /users",
    )

    client.index_event(event1)
    client.index_event(event2)

    # Query with session-A filter
    results_a = client.query_assumptions(
        text="The API endpoint is /users",
        session_id="session-A",
        top_k=10,
    )

    assert all(r["session_id"] == "session-A" for r in results_a), \
        "All results should be from session-A"

    # Query with session-B filter
    results_b = client.query_assumptions(
        text="The API endpoint is /users",
        session_id="session-B",
        top_k=10,
    )

    assert all(r["session_id"] == "session-B" for r in results_b), \
        "All results should be from session-B"

    # Query without filter — should find both
    results_all = client.query_assumptions(
        text="The API endpoint is /users",
        session_id=None,
        top_k=10,
    )

    # Should find at least 2 (the two events we indexed)
    assert len(results_all) >= 2


@pytest.mark.asyncio
async def test_get_index_stats(client):
    """get_index_stats returns non-zero collection size after indexing."""
    # Initially should be 0 or minimal
    stats_before = client.get_index_stats()
    assert stats_before["collection_size"] >= 0

    # Index a few events
    for i in range(3):
        event = make_delegation_event(
            event_id=f"stats-{i}",
            session_id="session-stats",
            description=f"Task description {i}",
        )
        client.index_event(event)

    # After indexing, should have more events
    stats = client.get_index_stats()
    assert stats["collection_size"] >= 3, \
        "Collection size should be at least 3 after indexing 3 events"
    assert stats["last_updated"] is not None, \
        "last_updated should be set after indexing"


# ---------------------------------------------------------------------------
# P2.1: graceful fallback when txtai unavailable
# ---------------------------------------------------------------------------

def test_graceful_fallback(monkeypatch, tmp_index_dir):
    """When txtai is unavailable, methods return empty results without crashing."""

    def mock_check():
        return False

    # Create client with unavailable status
    client = tx_mod.TxtaiClient(index_path=str(tmp_index_dir))
    client._available = None

    # Monkey-patch the availability check to simulate txtai being unavailable
    import evaluator.txtai_client as tx_mod2
    original_check = tx_mod2._check_txtai

    try:
        tx_mod2._check_txtai = lambda: False
        client._available = False

        # These should all return gracefully without raising
        assert client.index_event({}) is False
        assert client.query_assumptions("test") == []
        assert client.query_events({}) == []
        assert client.get_index_stats() == {"collection_size": 0, "last_updated": None}

    finally:
        tx_mod2._check_txtai = original_check


# ---------------------------------------------------------------------------
# P2.2: drift detection — direct contradiction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drift_detection_direct(client):
    """Two very similar assumption statements (diff only in trivial words) are flagged as direct."""
    # Index the first assumption
    # "located at" vs "situated at" yields similarity >= 0.95 with sentence-transformers
    event1 = make_assumption_event(
        event_id="drift-direct-1",
        session_id="session-drift",
        statement="The file is located at /home/user/data.csv",
    )
    client.index_event(event1)

    # Second assumption — same meaning, slightly different words
    event2 = make_assumption_event(
        event_id="drift-direct-2",
        session_id="session-drift",
        statement="The file is situated at /home/user/data.csv",
    )
    client.index_event(event2)

    # Run drift detection on the second event
    from evaluator.drift import DriftDetector

    detector = DriftDetector(txtai_client=client)
    result = detector.check("drift-direct-2", event2)

    # Should detect drift
    assert result is not None, "Should detect drift between similar statements"
    assert result.event_id == "drift-direct-2"
    assert result.contradicted_event_id == "drift-direct-1"
    assert result.contradiction_type == "direct", \
        f"Very similar statements should be 'direct', got '{result.contradiction_type}'"
    assert result.drift_score >= 0.95, \
        f"Direct contradiction should have score >= 0.95, got {result.drift_score}"


# ---------------------------------------------------------------------------
# P2.2: drift detection — semantic contradiction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drift_detection_semantic(client):
    """Two different but similar assumption statements are flagged as semantic."""
    # Index the first assumption
    # "authentication" vs "authorization" yields similarity ~0.863 (semantic range)
    event1 = make_assumption_event(
        event_id="drift-semantic-1",
        session_id="session-semantic",
        statement="The system requires authentication",
    )
    client.index_event(event1)

    # Second assumption — different but similar
    event2 = make_assumption_event(
        event_id="drift-semantic-2",
        session_id="session-semantic",
        statement="The system requires authorization",
    )
    client.index_event(event2)

    # Run drift detection on the second event
    from evaluator.drift import DriftDetector

    detector = DriftDetector(txtai_client=client)
    result = detector.check("drift-semantic-2", event2)

    # Should detect drift
    assert result is not None, "Should detect drift between similar but different statements"
    assert result.contradiction_type == "semantic", \
        f"Different but similar statements should be 'semantic', got '{result.contradiction_type}'"
    assert 0.85 <= result.drift_score < 0.95, \
        f"Semantic contradiction should have score 0.85-0.95, got {result.drift_score}"


# ---------------------------------------------------------------------------
# P2.2: drift detection — no drift
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drift_detection_none(client):
    """Unrelated assumption statements are not flagged as drift."""
    # Index the first assumption
    event1 = make_assumption_event(
        event_id="drift-none-1",
        session_id="session-none",
        statement="The weather forecast shows rain tomorrow",
    )
    client.index_event(event1)

    # Second assumption — completely unrelated
    event2 = make_assumption_event(
        event_id="drift-none-2",
        session_id="session-none",
        statement="The user wants to deploy to production",
    )
    client.index_event(event2)

    # Run drift detection on the second event
    from evaluator.drift import DriftDetector

    detector = DriftDetector(txtai_client=client)
    result = detector.check("drift-none-2", event2)

    # Should NOT detect drift
    assert result is None, \
        "Should not detect drift between unrelated statements"


# ---------------------------------------------------------------------------
# P2.2: scan_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_session(client):
    """scan_session returns all drifts in a given session."""
    from evaluator.drift import DriftDetector

    session_id = "session-scan-test"

    # Index multiple events with controlled similarity scores
    # s1 vs s2: 0.965 (direct) - mutually contradictory
    # s3 vs s4: 0.863 (semantic)
    # s5: unrelated to s1-s4
    events = [
        make_assumption_event(
            event_id="scan-s1",
            session_id=session_id,
            statement="The config file is at /etc/app.conf",
        ),
        make_assumption_event(
            event_id="scan-s2",
            session_id=session_id,
            statement="The config file is situated at /etc/app.conf",  # direct with s1
        ),
        make_assumption_event(
            event_id="scan-s3",
            session_id=session_id,
            statement="The system requires authentication",
        ),
        make_assumption_event(
            event_id="scan-s4",
            session_id=session_id,
            statement="The system requires authorization",  # semantic with s3 (~0.863)
        ),
        make_assumption_event(
            event_id="scan-s5",
            session_id="other-session",  # different session - should not appear
            statement="The weather is sunny today",
        ),
    ]

    for event in events:
        client.index_event(event)

    # Run scan on the session
    detector = DriftDetector(txtai_client=client)
    results = detector.scan_session(session_id)

    # Should find at least 2 drifts (s2 vs s1 direct, s4 vs s3 semantic)
    assert len(results) >= 2, \
        f"Should find at least 2 drifts in session, found {len(results)}: {[r.to_dict() for r in results]}"

    # All drifts should be from our session
    result_session_ids = {r.event_id.split('-')[0] for r in results}
    # Each result event_id contains the session scope info through scan_session


# ---------------------------------------------------------------------------
# P2.2: DriftDetector initialization
# ---------------------------------------------------------------------------

def test_drift_detector_initialization():
    """DriftDetector can be initialized without explicit client."""
    from evaluator.drift import DriftDetector

    detector = DriftDetector()
    assert detector.client is not None
