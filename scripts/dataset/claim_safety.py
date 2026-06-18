"""Conservative checks for unsupported engineering claims."""

from __future__ import annotations

import json
import re
from typing import Any


def _answer_text(answer: dict[str, Any]) -> str:
    return json.dumps(answer, ensure_ascii=False).lower()


def _has_warning(answer: dict[str, Any], section: str, words: tuple[str, ...]) -> bool:
    text = json.dumps(answer.get(section, ""), ensure_ascii=False).lower()
    return any(word in text for word in words)


def _has_passing_check(checks: dict[str, Any], tool: str) -> bool:
    check = checks.get(tool)
    return isinstance(check, dict) and check.get("status") == "pass"


def find_unsupported_claims(row: dict[str, Any], answer: dict[str, Any]) -> list[tuple[str, str]]:
    text = _answer_text(answer)
    strong_text = re.sub(
        r"\b(?:may|might|could)\s+(?:reduce|lower|improve)\s+"
        r"(?:switching\s+activity|toggle\s+activity|activity|area|power)\b",
        "qualified suggestion",
        text,
    )
    checks = row.get("tool_checks") if isinstance(row.get("tool_checks"), dict) else {}
    task = row.get("messages", [{}, {}, {}])[1].get("content", {})
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    issues: list[tuple[str, str]] = []

    strong_power = re.search(r"\b(reduces?|reduced|lower(?:s|ed)?|improves?|improved)\s+(?:the\s+)?power\b|\bpower\s+(?:(?:is|was|has been)\s+)?(?:reduced|lower|improved)", strong_text)
    measured_power = re.search(r"\b(measured|measurement|report(?:ed)?)\b[^.]{0,60}\bpower\b|\bpower\b[^.]{0,60}\b(measured|measurement)\b", text)
    if strong_power and not _has_passing_check(checks, "power"):
        issues.append(("tool_checks.power", "unsupported power improvement claim without power evidence"))
    if measured_power and not artifacts.get("power_report"):
        issues.append(("messages[1].content.artifacts.power_report", "measured power claim requires a power report artifact"))

    if re.search(r"\b(area (?:(?:is|was|has been) )?improved|reduces? area|area (?:reduced|decreased)|smaller area)\b", strong_text) and not _has_passing_check(checks, "synthesis"):
        issues.append(("tool_checks.synthesis", "unsupported area improvement claim without synthesis evidence"))
    if re.search(r"\b(guaranteed lower activity|switching (?:is|was) (?:reduced|improved)|reduces? (?:switching|toggle) activity|toggle (?:is|was) improved)\b", strong_text) and not _has_passing_check(checks, "toggle"):
        issues.append(("tool_checks.toggle", "unsupported switching/toggle improvement claim without toggle evidence"))
    if re.search(r"\b(correctness (?:is|was|has been) verified|verified correct|proven correct)\b", text) and not (_has_passing_check(checks, "simulation") or _has_passing_check(checks, "equivalence")):
        issues.append(("tool_checks.simulation", "unsupported verified correctness claim without simulation or equivalence evidence"))

    if re.search(r"\b(this |the )?patch is safe\b", text) and not answer.get("functional_risk"):
        issues.append(("messages[2].content.functional_risk", "a safe-patch claim requires a functional risk warning"))
    if re.search(r"\b(remove|removing|change|changing|omit|omitting)\b[^.]{0,50}\breset\b", text) and not _has_warning(answer, "time_reasoning", ("reset", "risk", "behavior")):
        issues.append(("messages[2].content.time_reasoning.reset_behavior_risk", "reset change requires a reset behavior risk warning"))
    risky = re.search(r"\b(remove|removing)\b[^.]{0,40}\bregister|\b(share|sharing)\b[^.]{0,40}\bhardware|\b(change|changing)\b[^.]{0,50}\b(valid|ready)\b", text)
    if risky and not _has_warning(answer, "time_reasoning", ("latency", "state", "interface", "cycle")):
        issues.append(("messages[2].content.time_reasoning.latency_or_state_risk", "register, sharing, or handshake change requires a latency/state/interface risk warning"))
    return issues
