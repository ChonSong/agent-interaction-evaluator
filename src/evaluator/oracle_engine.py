"""Oracle Engine — SPEC.md §6, REQUIREMENTS.md P3.1–P3.3."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import db as db_mod
from . import txtai_client as tx

logger = logging.getLogger(__name__)

@dataclass
class Condition:
    type: str
    field: str = ""
    value: Any = None
    reference: str = ""
    threshold: float = 0.0
    threshold_value: float = 0.0
    op: str = "gt"
    condition: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        known = {"type","field","value","reference","threshold","threshold_value","op","condition","events"}
        return cls(**{k:v for k,v in data.items() if k in known})

@dataclass
class Action:
    type: str
    output: str = ""
    channel: str = "evaluator-alerts"
    condition: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        return cls(
            type=data.get("type","log"),
            output=data.get("output",""),
            channel=data.get("channel","evaluator-alerts"),
            condition=data.get("condition",""),
        )

@dataclass
class Oracle:
    oracle_id: str
    name: str
    description: str
    event_type: str
    trigger: str
    severity: str
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Oracle":
        return cls(
            oracle_id=data["oracle_id"],
            name=data.get("name", data["oracle_id"]),
            description=data.get("description",""),
            event_type=data["event_type"],
            trigger=data.get("trigger","on_event"),
            severity=data.get("severity","info"),
            conditions=[Condition.from_dict(c) if isinstance(c,dict) else c for c in data.get("conditions",[])],
            actions=[Action.from_dict(a) if isinstance(a,dict) else a for a in data.get("actions",[])],
            metadata=data.get("metadata",{}),
        )

@dataclass
class ConditionResult:
    oracle_id: str
    passed: bool
    failed_conditions: list[str] = field(default_factory=list)
    deviation: str | None = None
    evaluated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "passed": self.passed,
            "failed_conditions": self.failed_conditions,
            "deviation": self.deviation,
            "evaluated_at": self.evaluated_at,
        }

# Condition evaluators
class FieldRequiredEvaluator:
    TYPE = "field_required"
    @staticmethod
    def evaluate(event: dict, field: str, **kwargs) -> bool:
        return _get_nested(event, field) is not None

class FieldNotEmptyEvaluator:
    TYPE = "field_not_empty"
    @staticmethod
    def evaluate(event: dict, field: str, **kwargs) -> bool:
        val = _get_nested(event, field)
        if val is None: return False
        if isinstance(val, str): return val.strip() != ""
        if isinstance(val, list): return len(val) > 0
        return True

class FieldMinLengthEvaluator:
    TYPE = "field_min_length"
    @staticmethod
    def evaluate(event: dict, field: str, value: int = 0, **kwargs) -> bool:
        val = _get_nested(event, field)
        if not isinstance(val, str): return False
        return len(val) >= value

class FieldMaxLengthEvaluator:
    TYPE = "field_max_length"
    @staticmethod
    def evaluate(event: dict, field: str, value: int = 0, **kwargs) -> bool:
        val = _get_nested(event, field)
        if not isinstance(val, str): return True
        return len(val) <= value

class FieldRegexEvaluator:
    TYPE = "field_regex"
    @staticmethod
    def evaluate(event: dict, field: str, value: str = "", **kwargs) -> bool:
        val = _get_nested(event, field)
        if not isinstance(val, str): return False
        try:
            return bool(re.search(value, val))
        except re.error:
            return False

class FieldEqEvaluator:
    TYPE = "field_eq"
    @staticmethod
    def evaluate(event: dict, field: str, value: Any = None, **kwargs) -> bool:
        return _get_nested(event, field) == value

class FieldGtEvaluator:
    TYPE = "field_gt"
    @staticmethod
    def evaluate(event: dict, field: str, value: float = 0.0, **kwargs) -> bool:
        try:
            return float(_get_nested(event, field)) > float(value)
        except (TypeError, ValueError):
            return False

class FieldLtEvaluator:
    TYPE = "field_lt"
    @staticmethod
    def evaluate(event: dict, field: str, value: float = 0.0, **kwargs) -> bool:
        try:
            return float(_get_nested(event, field)) < float(value)
        except (TypeError, ValueError):
            return False

class FieldInEvaluator:
    TYPE = "field_in"
    @staticmethod
    def evaluate(event: dict, field: str, values: list = None, **kwargs) -> bool:
        if values is None: return False
        return _get_nested(event, field) in values

class SimilarityThresholdEvaluator:
    TYPE = "similarity_threshold"
    def __init__(self, txtai_client=None):
        self._client = txtai_client
    @property
    def client(self):
        if self._client is None: self._client = tx.get_client()
        return self._client
    def evaluate(self, event: dict, field: str, reference: str = "", threshold: float = 0.0, **kwargs) -> bool:
        if not reference or threshold <= 0: return True
        text = _get_nested(event, field)
        if not isinstance(text, str) or not text: return False
        if not self.client.available: return True
        try:
            scores = self.client.similarity([text, reference], query=text)
            return float(scores[0]) >= threshold if scores else False
        except: return True

class DriftScoreThresholdEvaluator:
    TYPE = "drift_score_threshold"
    @staticmethod
    def evaluate(event: dict, threshold_value: float = 0.0, op: str = "gt", **kwargs) -> bool:
        ds = event.get("drift_score")
        if ds is None: return False
        try:
            ds = float(ds); tv = float(threshold_value)
            return (ds>tv if op=="gt" else ds<tv if op=="lt" else ds>=tv if op=="gte" else ds<=tv if op=="lte" else ds==tv if op=="eq" else False)
        except: return False

class RatioThresholdEvaluator:
    TYPE = "ratio_threshold"
    @staticmethod
    def evaluate(events: list[dict], condition: dict = None, threshold_value: float = 0.0, op: str = "gt", **kwargs) -> bool:
        if not events: return False
        condition = condition or {}
        ct = condition.get("type")
        if not ct: return False
        ec = _EVALUATOR_CLASSES.get(ct)
        if ec is None or ec == RatioThresholdEvaluator: return False
        passing = sum(1 for ev in events if _try_eval(ec, ev, condition))
        ratio = passing / len(events)
        tv = float(threshold_value)
        return (ratio>tv if op=="gt" else ratio<tv if op=="lt" else ratio>=tv if op=="gte" else ratio<=tv if op=="lte" else ratio==tv if op=="eq" else False)

def _try_eval(ec, ev, cond):
    try:
        return ec.evaluate(ev, field=cond.get("field",""), value=cond.get("value"), reference=cond.get("reference",""), threshold=cond.get("threshold",0.0), threshold_value=cond.get("threshold_value",0.0), op=cond.get("op","gt"), values=cond.get("values"))
    except: return False

_EVALUATOR_CLASSES = {c.TYPE: c for c in [
    FieldRequiredEvaluator, FieldNotEmptyEvaluator, FieldMinLengthEvaluator,
    FieldMaxLengthEvaluator, FieldRegexEvaluator, FieldEqEvaluator,
    FieldGtEvaluator, FieldLtEvaluator, FieldInEvaluator,
    type("S",(),{"TYPE":"similarity_threshold"})(),
    type("D",(),{"TYPE":"drift_score_threshold"})(),
    type("R",(),{"TYPE":"ratio_threshold"})(),
]}

def _get_nested(event: dict, field: str) -> Any:
    if not field: return None
    parts = field.split(".")
    val = event
    for part in parts:
        if isinstance(val, dict): val = val.get(part)
        elif isinstance(val, list) and part.isdigit(): idx=int(part); val=val[idx] if idx<len(val) else None
        else: return None
        if val is None: return None
    return val

def _eval_cond(cond: Condition, event: dict) -> bool:
    ec = _EVALUATOR_CLASSES.get(cond.type)
    if ec is None: return True
    kwargs = {"field":cond.field,"value":cond.value,"reference":cond.reference,"threshold":cond.threshold,"threshold_value":cond.threshold_value,"op":cond.op,"values":getattr(cond,"values",None),"condition":cond.condition,"events":cond.events}
    if ec == SimilarityThresholdEvaluator or (isinstance(ec, type) and issubclass(ec, SimilarityThresholdEvaluator)):
        return ec().evaluate(event,**kwargs)
    try: return ec.evaluate(event,**kwargs)
    except: return False

def evaluate_conditions(oracle: Oracle, event: dict) -> ConditionResult:
    from .schema import get_current_timestamp
    failed, deviations = [], []
    for cond in oracle.conditions:
        if not _eval_cond(cond, event):
            failed.append(cond.type)
            deviations.append(f"condition '{cond.type}' on field '{cond.field}' failed")
    return ConditionResult(oracle_id=oracle.oracle_id, passed=len(failed)==0, failed_conditions=failed, deviation="; ".join(deviations) if deviations else None, evaluated_at=get_current_timestamp())

async def apply_actions(oracle: Oracle, event: dict, result: ConditionResult) -> None:
    if result.passed:
        logger.info("Oracle %s PASSED for event %s", oracle.oracle_id, event.get("event_id"))
        return
    severity = oracle.severity
    if severity in ("critical","warning"):
        try: await _insert_result(result, event)
        except: pass
    if severity in ("critical","warning"):
        msg = f"ORACLE FAILURE [{severity.upper()}]: {oracle.oracle_id} failed on {oracle.event_type} event {event.get("event_id")} (session: {event.get("session_id")}). Deviation: {result.deviation}"
        try: await _send_alert(msg, severity)
        except: pass
    if severity == "critical":
        try: await _emit_cb(oracle, event, result)
        except: pass
    if severity == "info":
        logger.warning("Oracle %s [%s] failed for event %s: %s", oracle.oracle_id, severity, event.get("event_id"), result.deviation)

async def _insert_result(result, event):
    import aiosqlite
    db_path = os.environ.get("AIE_DB_PATH")
    conn = await db_mod.init_db(db_path)
    rid = f"{result.oracle_id}_{event.get("event_id","?")}"
    ea = result.evaluated_at or datetime.now(timezone.utc).isoformat()
    await conn.execute("INSERT OR REPLACE INTO oracle_results (result_id,event_id,oracle_id,passed,deviation,evaluated_at) VALUES (?,?,?,?,?,?)",(rid,event.get("event_id"),result.oracle_id,result.passed,result.deviation,ea))
    await conn.commit(); await conn.close()

async def _send_alert(message: str, severity: str):
    import subprocess
    try:
        subprocess.run(["openclaw","message","--channel","evaluator-alerts",message],capture_output=True,timeout=10,check=False)
    except: pass

async def _emit_cb(oracle, event, result):
    import aiosqlite
    from .schema import generate_event_id, get_current_timestamp
    db_path = os.environ.get("AIE_DB_PATH")
    conn = await db_mod.init_db(db_path)
    eid = generate_event_id()
    cb = {"event_id":eid,"event_type":"circuit_breaker","timestamp":get_current_timestamp(),"agent_id":event.get("agent_id","aie-system"),"session_id":event.get("session_id","unknown"),"schema_version":"1.0","interaction_context":event.get("interaction_context",{}),"gate":{"name":oracle.oracle_id,"threshold":result.deviation or "oracle_failed","assumptions_violated":[event.get("event_id")]},"action_blocked":None,"halt_session":True,"alert_sent":True,"audit_ref":f"oracle_result:{oracle.oracle_id}"}
    await conn.execute("INSERT OR REPLACE INTO circuit_breaker_events (event_id,event_type,session_id,oracle_id,halt_session,alert_sent) VALUES (?,?,?,?,?,?)",(eid,"circuit_breaker",cb["session_id"],oracle.oracle_id,True,True))
    await conn.commit(); await conn.close()
    from . import logger as lm
    ld = lm.LOG_DIR
    if ld.exists():
        with open(ld / datetime.now(timezone.utc).strftime("%Y-%m-%d")+'.jsonl',"a") as f:
            f.write(json.dumps(cb)+chr(10))

class OracleRegistry:
    def __init__(self): self._oracles: dict[str,Oracle] = {}
    def load(self, path: str = "oracles/") -> int:
        base = Path(path).resolve(); count = 0
        for yf in base.rglob("*.yaml"):
            if yf.name.startswith("_"): continue
            try:
                with open(yf) as f: data = yaml.safe_load(f)
                if not isinstance(data,dict): continue
                if "oracle_id" in data:
                    o = Oracle.from_dict(data); self._oracles[o.oracle_id]=o; count+=1
                elif isinstance(data.get("oracles"),list):
                    for item in data["oracles"]:
                        o=Oracle.from_dict(item); self._oracles[o.oracle_id]=o; count+=1
            except: pass
        return count
    def get_for_event_type(self, et: str) -> list[Oracle]:
        return [o for o in self._oracles.values() if o.event_type == et]
    def get(self, oid: str) -> Oracle|None: return self._oracles.get(oid)
    def list(self) -> list[Oracle]: return list(self._oracles.values())
    def validate(self, oid: str) -> tuple[bool,str|None]:
        o = self._oracles.get(oid)
        if o is None: return False,f"Oracle '{oid}' not found"
        errs=[]
        if not o.conditions: errs.append("Must have at least one condition")
        for c in o.conditions:
            if c.type not in _EVALUATOR_CLASSES: errs.append(f"Unknown condition type: '{c.type}'")
        if o.severity not in ("critical","warning","info"): errs.append(f"Invalid severity: '{o.severity}'")
        if o.trigger not in ("on_event","on_demand","on_cron"): errs.append(f"Invalid trigger: '{o.trigger}'")
        return (False,"; ".join(errs)) if errs else (True,None)
    def validate_all(self) -> dict: return {oid:self.validate(oid) for oid in self._oracles}

async def evaluate_event(event: dict, registry: OracleRegistry|None = None) -> list[ConditionResult]:
    if registry is None:
        registry = OracleRegistry()
        registry.load(str(Path(__file__).resolve().parents[2]/"oracles"))
    et = event.get("event_type","")
    applicable = [o for o in registry.get_for_event_type(et) if o.trigger in ("on_event","on_demand")]
    results=[]
    for o in applicable:
        r = evaluate_conditions(o, event)
        await apply_actions(o, event, r)
        results.append(r)
    return results

_registry: OracleRegistry|None = None
def get_registry(path: str="oracles/") -> OracleRegistry:
    global _registry
    if _registry is None:
        _registry = OracleRegistry()
        _registry.load(str(Path(__file__).resolve().parents[2]/path))
    return _registry

def reset_registry() -> None:
    global _registry; _registry = None
