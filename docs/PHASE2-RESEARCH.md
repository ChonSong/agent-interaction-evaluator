# Phase 2 Research — Drift Detection + txtai Indexing

> Research sprint completed 2026-04-01. Sources: RepoTransmute source code, txtai docs, HuggingFace cross-encoder models, AgentTrace (Feb 2026), promptfoo docs.

---

## 1. What RepoTransmute Already Has

RepoTransmute has a **fully implemented evaluator module** at `repo_transmute/evaluator/`:

| File | What it does |
|---|---|
| `events.py` | `InteractionEvent` dataclass — 9 event types, structured fields |
| `drift_detector.py` | `DriftDetector` — keyword-based assumption tracking + heuristic contradiction detection |
| `assumption_extractor.py` | Extracts assumptions from text |
| `audit.py` | Generates audit trails |
| `interface.py` | `AgentEvaluator` — coordinates all of the above |
| `search.py` | `EvaluatorSearchIndex` — txtai wrapper for events |

**Critical finding:** RepoTransmute's `DriftDetector` uses **keyword-based contradiction detection** (CONTEXT_SHIFT_KEYWORDS: "but", "however", "actually", "wait", "correction"...). It does NOT use semantic similarity. This is the right v1 approach but misses semantic drift.

---

## 2. txtai Architecture (from RepoTransmute source)

**Model:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim embeddings, CPU-friendly)

**Client pattern (TxtaiClient):**
```python
# Index documents
client.index([{"id": "uid", "text": "embedded text", ...metadata})
client.save()

# Search
results = client.search("query", limit=10)
# Returns: [{"id": str, "score": float, ...stored_metadata}]

# Similarity
scores = client.similarity(["text1", "text2"], query="query")
# Returns: [cosine_similarity scores]
```

**Key insight:** The `text` field is what gets embedded. All other fields go into a SQLite sidecar (`metadata.db`) and are joined back after search. So for assumption drift, we need to construct a rich `text` field from the assumption statement.

**Embedding short texts:** For assumption statements (typically 1-2 sentences), the embedded text should be the assumption statement itself, possibly prefixed with context like `"assumption: <statement>"` to distinguish from tool_call content.

---

## 3. Cross-Encoder NLI Models

**For implicit contradiction detection:**

| Model | Size | Notes |
|---|---|---|
| `cross-encoder/nli-deberta-v3-xsmall` | ~22MB | Smallest, fastest |
| `cross-encoder/nli-deberta-v3-small` | ~45MB | Small |
| `cross-encoder/nli-deberta-v3-base` | ~370MB | Full accuracy |

**All output:** `contradiction`, `entailment`, or `neutral` scores.

**v1 decision:** Use only if `similarity > 0.85` (semantic drift flagged) — then run NLI as a second pass to distinguish `contradiction` from `neutral`. This avoids running NLI on every assumption pair.

**Practical note:** Even `nli-deberta-v3-small` runs on CPU in <100ms per pair. No GPU required.

---

## 4. Similarity Thresholds — Analysis

| Threshold | Effect |
|---|---|
| ≥ 0.95 | Only near-identical statements. Catches direct repetition with different framing. Very few false positives. |
| 0.90–0.95 | Similar statements. Catches rephrasing of same assumption. Manageable false positive rate. |
| 0.85–0.90 | Captures semantic drift (different words, same meaning). Higher false positive rate — distinct but related concepts may fire. |
| < 0.85 | Too noisy for short assumption texts. |

**Recommendation for assumption texts:**
- `≥ 0.95` → `direct` contradiction
- `0.85–0.95` → `semantic` contradiction (second-pass NLI to confirm — if NLI says `neutral`, don't flag)
- `implicit` → NLI `contradiction` on semantically similar pairs (0.85–0.95 range), or pairs that share key entities but differ in predicate

**Key risk:** Short assumption texts (< 10 words) have less embedding signal. Threshold of 0.85 may fire incorrectly on generic statements. Mitigation: require `grounded_in` field (oracle: `groundedness_required`).

---

## 5. AgentTrace (Feb 2026) — Relevance

**Paper:** `AgentTrace: A Structured Logging Framework for Agent System Observability` (arxiv 2602.10133)

**Key insight from abstract:**
> "Existing security methods... fail to provide sufficient transparency or traceability into agent reasoning, state changes, or environmental interactions. AgentTrace is a dynamic observability and telemetry framework."

**What it confirms:**
- Structured event logging for agents is an active research area (not solved)
- The problem we're tackling (AIE) is at the frontier
- Dynamic telemetry + static evaluation is the right combination

**What it adds:** AgentTrace provides a conceptual framework for event taxonomy (reasoning traces, state changes, environmental interactions). Our 7 event types cover all three.

---

## 6. promptfoo — What It Doesn't Do

promptfoo tests **outputs and judgments**, not interaction quality:

- ✅ Tests if agent output is correct (assertion-based)
- ✅ Tests if agent passes/fails defined test cases
- ✅ Security red-teaming
- ❌ Does NOT track assumption drift over time
- ❌ Does NOT detect when agent contradicts itself across a session
- ❌ Does NOT produce decision provenance

**Conclusion:** AIE and promptfoo are complementary, not competing. promptfoo tests competence; AIE tests consistency and reliability over time.

---

## 7. OpenHands Observability

OpenHands supports **OpenTelemetry tracing** — but it's for execution monitoring (latency, tool calls, span hierarchies), not assumption drift detection.

**Relevance:** OpenHands tracing could be an input source to AIE. If we instrument OpenHands agents to emit AIE-compatible events, we get OpenTelemetry tracing + AIE drift detection together.

---

## 8. Key Design Decisions for Phase 2

### 8.1 Reuse vs Rewrite

**Decision:** Reuse RepoTransmute's `TxtaiClient` pattern, but maintain a **separate** `agent_events` collection.

```python
# AIE's txtai_client.py
import sys
sys.path.insert(0, str(Path("~/workspace/zoul/repo-transmute/src")))
from repo_transmute.txtai.client import TxtaiClient

class AIETxtaiClient(TxtaiClient):
    """Extends RepoTransmute's client with AIE-specific collection."""
    COLLECTION = "agent_events"
```

Why not just import it? Because AIE needs its own metadata schema (agent events have different fields than code blueprints). We extend, not fork.

### 8.2 Drift Detection Strategy

**v1 (Phase 2):**
1. Index each `assumption` event's statement in txtai
2. On new assumption: query txtai for prior assumptions in same session
3. If `similarity > 0.85`: flag as `semantic` drift
4. If `similarity ≥ 0.95`: flag as `direct` drift
5. Optional second-pass NLI (v2) to filter false positives

**NOT doing:** Replacing RepoTransmute's keyword-based DriftDetector. That detector fires on self-correction keywords — that's a valid signal. We extend it with semantic similarity.

### 8.3 Event Text Construction

For embedding assumption statements:
```
"assumption: {statement} | category: {category} | agent: {agent_id} | session: {session_id}"
```

For tool_call events:
```
"tool_call: {tool.name} | args_summary: {sanitised args} | outcome: {status}"
```

### 8.4 Threshold Calibration

- Use `groundedness_required` oracle to filter ungrounded assumptions (high false positive risk)
- Set `semantic` threshold at 0.85, but require NLI confirmation for production alerts
- `direct` threshold at 0.95 (near-identical) — fire immediately, no NLI needed

### 8.5 Collection Isolation

- `blueprints` — RepoTransmute code indexes (untouched by AIE)
- `agent_events` — AIE interaction events
- Both share the same `metadata.db` SQLite sidecar (different tables)
- `metadata.db` already has `repo_meta` table — AIE adds `event_meta` table

---

## 9. What to Keep from SPEC.md

- 7 event types ✅ (compatible with RepoTransmute's 9, subset is fine)
- JSON-RPC IPC logger ✅ (already built in Phase 1)
- Oracle YAML format ✅ (already defined)
- Drift result schema ✅ (minor additions for NLI)

## What to Change in SPEC.md

- Drift algorithm section (§5.2): Note keyword-based detection is supplemented by semantic similarity (not replaced)
- Add NLI as v2 enhancement (Phase 7)
- txtai client section: note we extend RepoTransmute's `TxtaiClient`, not rewrite

---

## 10. Research Gaps (Unresolved)

| Question | Status |
|---|---|
| Optimal similarity threshold for short assumption texts | Assumption-based — 0.85 is plausible, needs calibration against real data |
| False positive rate of semantic drift on agent assumptions | Unknown — need golden fixtures to calibrate |
| NLI model size vs accuracy tradeoffs | `nli-deberta-v3-small` likely sufficient for our use case |

---

## 11. Summary Recommendation

**Proceed with Phase 2 implementation using:**

1. **txtai client:** Extend RepoTransmute's `TxtaiClient` pattern, share the same FAISS index, create separate `agent_events` collection
2. **Embedding model:** Same `all-MiniLM-L6-v2` (no need for another model)
3. **Drift detection:** Semantic similarity on assumption statements, keyword-based detection from RepoTransmute's `DriftDetector` as complementary signal
4. **Thresholds:** `≥ 0.95` = direct, `0.85–0.95` = semantic (second-pass NLI in v2)
5. **NLI:** Defer to Phase 7 unless threshold calibration proves necessary in v1
6. **Integration:** The logger (Phase 1) should call txtai index on every event — integrate `AIETxtaiClient.index_event()` into `AILogger.emit()`
