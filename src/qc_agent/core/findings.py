"""Deterministic finding normalization and infrastructure blockers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase

LEVELS = ("low", "medium", "critical")
_POSITION = re.compile(r"\s+@\s+\S+:\d+(?::\d+)?(?:\s|$)")
_LINE = re.compile(r"(?i)\b(?:line|dòng)\s*\d+\b")


@dataclass(frozen=True)
class NormFinding:
    fingerprint: str
    severity: str
    task_id: str
    suite: str | None
    worker: str
    rule_id: str | None
    title: str
    path: str | None
    line: int | None
    end_line: int | None
    lane: str
    verdict_source: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


def _default(policy: dict, suite: str | None) -> str:
    defaults = (policy.get("default_severity") or {})
    return defaults.get(suite, defaults.get("*", "medium"))


def _rule_id(detected_by: str) -> str | None:
    return detected_by.partition(":")[2] or None


def _fingerprint(worker: str, rule_id: str | None, path: str | None, title: str) -> str:
    clean = _LINE.sub("", _POSITION.sub("", title)).strip()
    return hashlib.sha256(f"{worker}|{rule_id or ''}|{path or ''}|{clean}".encode()).hexdigest()[:16]


def normalize(results: dict, specs: dict, *, task_suite: dict[str, str] | None = None,
              policy: dict | None = None) -> tuple[list[NormFinding], list[tuple[str, str]]]:
    """Normalize selected tasks only. Neither LLM nor discovery can supply blocking severity."""
    task_suite, policy = task_suite or {}, policy or {}
    findings: list[NormFinding] = []
    blockers: list[tuple[str, str]] = []
    gating_seen = False
    for task_id, spec in specs.items():
        lane = spec.get("lane", "discovery")
        suite = task_suite.get(task_id)
        result = results.get(task_id)
        if result is None:
            if lane == "gate":
                blockers.append((task_id, "không có result"))
            continue
        status = result.get("status")
        verdict = result.get("verdict") or {}
        if lane == "gate" and verdict.get("gating") and status in ("pass", "fail"):
            gating_seen = True
        if status == "error" and lane == "gate":
            blockers.append((task_id, "error (hạ tầng)"))
        if status == "skipped" and lane == "gate":
            if policy.get("on_skipped_gate_task", "fail") == "fail":
                blockers.append((task_id, "skipped ở gate lane"))
            else:
                findings.append(_make(task_id, suite, result, lane, "task_default", "skipped gate task", None,
                                      "low", {}))
        raw = (result.get("findings") or []) if status in ("pass", "fail") else []
        for item in raw:
            location = item.get("location") or {}
            detected_by = item.get("detected_by") or ""
            severity = item.get("severity_hint") or _default(policy, suite)
            findings.append(_make(task_id, suite, result, lane, "finding", item.get("title") or "finding",
                                  _rule_id(detected_by), severity, policy, location,
                                  item.get("verdict_source") or verdict.get("verdict_source") or "heuristic"))
        if status == "fail" and not raw:
            findings.append(_make(task_id, suite, result, lane, "task_default", "task failed", None,
                                  _default(policy, suite), policy))
    if any(spec.get("lane") == "gate" for spec in specs.values()) and not gating_seen:
        blockers.append(("*", "không có result gating nào — không có gate"))
    return findings, blockers


def _make(task_id: str, suite: str | None, result: dict, lane: str, source: str, title: str,
          rule_id: str | None, severity: str, policy: dict, location: dict | None = None,
          verdict_source: str | None = None) -> NormFinding:
    worker = (result.get("worker") or {}).get("name") or "unknown"
    for override in policy.get("overrides") or []:
        if (fnmatchcase(suite or "", override.get("suite", "*")) and
                fnmatchcase(rule_id or "", override.get("rule_id", "*")) and
                severity == override.get("from")):
            severity = override["to"]
    verdict_source = verdict_source or (result.get("verdict") or {}).get("verdict_source") or "heuristic"
    if lane == "discovery" or verdict_source == "llm_judgment":
        severity = "low"
    if severity not in LEVELS:
        severity = "medium"
    location = location or {}
    path = location.get("path")
    return NormFinding(_fingerprint(worker, rule_id, path, title), severity, task_id, suite, worker,
                       rule_id, title, path, location.get("line"), location.get("end_line"),
                       lane, verdict_source, source)
