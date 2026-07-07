"""Conservative regex helpers for public RTL draft metadata.

These helpers inspect text only. They do not execute, elaborate, or compile RTL.
"""

from __future__ import annotations

import re
from typing import Any


_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_MODULE_RE = re.compile(r"\bmodule\s+(" + _IDENT + r")\b", re.IGNORECASE)
_PORT_RE = re.compile(r"\b(?:input|output|inout)\b[^;)]*", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"\b(" + _IDENT + r")(?:\s*\[[^\]]+\])?\s*<=", re.IGNORECASE)
_ALWAYS_COMB_RE = re.compile(r"\balways_comb\b|\balways\s*@\s*\*", re.IGNORECASE)
_RTL_PORT_DECL_RE = re.compile(
    r"\b(input|output|inout)\b\s+(.+?)(?=\b(?:input|output|inout)\b|[;\n)])",
    re.IGNORECASE | re.DOTALL,
)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _port_names(text: str) -> list[str]:
    names: list[str] = []
    for match in _PORT_RE.finditer(text):
        segment = re.sub(r"\[[^\]]+\]", " ", match.group(0))
        segment = re.sub(r"\b(?:input|output|inout|wire|reg|logic|signed)\b", " ", segment, flags=re.IGNORECASE)
        names.extend(re.findall(_IDENT, segment))
    return _dedupe(names)


def rtl_port_directions(text: str) -> dict[str, str]:
    """Best-effort port direction extraction without parsing or elaborating RTL."""
    directions: dict[str, str] = {}
    if not isinstance(text, str):
        return directions
    without_comments = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", text, flags=re.DOTALL)
    for match in _RTL_PORT_DECL_RE.finditer(without_comments):
        direction = match.group(1).lower()
        segment = match.group(2)
        segment = re.split(r"\b(?:input|output|inout)\b", segment, maxsplit=1, flags=re.IGNORECASE)[0]
        segment = re.sub(r"\[[^\]]+\]", " ", segment)
        segment = re.sub(r"\b(?:wire|reg|logic|signed|unsigned|integer|bit)\b", " ", segment, flags=re.IGNORECASE)
        for name in re.findall(_IDENT, segment):
            directions.setdefault(name, direction)
    return directions


def _is_reset_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in {
            "rst",
            "reset",
            "rst_n",
            "reset_n",
            "resetn",
            "areset",
            "aresetn",
            "areset_n",
            "ar",
        }
        or lowered.endswith("_rst")
        or lowered.endswith("_reset")
        or lowered.endswith("_rst_n")
        or lowered.endswith("_reset_n")
        or lowered.startswith("rst_")
        or lowered.startswith("reset_")
    )


def _reset_condition_names(text: str) -> set[str]:
    """Find short reset-like controls used in simple nonblocking reset branches."""
    without_comments = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", text, flags=re.DOTALL)
    reset_names: set[str] = set()
    zero_rhs = r"(?:'0|0+|[0-9]+'[sS]?[dDhHbBoO]0+)"
    condition_re = re.compile(
        r"\bif\s*\(\s*(?:!|~)?\s*(" + _IDENT + r")\s*\)\s*"
        r"(?:begin\s*)?\b" + _IDENT + r"(?:\s*\[[^\]]+\])?\s*<=\s*"
        + zero_rhs + r"\s*;",
        re.IGNORECASE,
    )
    for match in condition_re.finditer(without_comments):
        name = match.group(1)
        if name.lower() == "r":
            reset_names.add(name)
    return reset_names


def summarize_rtl(artifacts: dict[str, str]) -> dict[str, Any]:
    rtl_text = "\n".join(
        value for key, value in artifacts.items()
        if key in {"rtl_code", "before_rtl_code", "after_rtl_code"} and isinstance(value, str)
    )
    modules = _MODULE_RE.findall(rtl_text)
    ports = _port_names(rtl_text)
    registered = _dedupe(_ASSIGN_RE.findall(rtl_text))
    candidates = _dedupe(ports + registered)
    clocks = [name for name in candidates if name in {"clk", "clock"} or name.endswith("_clk")]
    reset_conditions = _reset_condition_names(rtl_text)
    resets = [name for name in candidates if _is_reset_name(name) or name in reset_conditions]
    counters = [
        name for name in candidates
        if re.search(r"count|counter|cnt|timer", name, re.IGNORECASE)
    ]
    fsms = [
        name for name in candidates
        if re.search(r"state|next_state", name, re.IGNORECASE)
    ]
    return {
        "top_module": modules[0] if modules else None,
        "clock_signals": clocks,
        "reset_signals": resets,
        "registered_signals": registered,
        "combinational_blocks": ["always_comb"] if _ALWAYS_COMB_RE.search(rtl_text) else [],
        "suspected_fsm_signals": fsms,
        "suspected_counters": counters,
        "unused_enable_signals": [],
        "activity_hotspots": counters + fsms,
    }
