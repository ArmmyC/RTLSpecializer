"""Local-only preparation of teacher-visible RTL generation tasks.

This module treats every source artifact as untrusted text.  It discovers local
VerilogEval-style rows, separates private verification bytes from public
normalization metadata, validates manually returned task JSON, and performs a
deterministic task/asset join.  It never executes RTL, testbenches, or commands
found in dataset contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from scripts.dataset.adapters import ImportOptions, RawPublicExample, get_adapter
from scripts.dataset.rtl_extract import module_names, rtl_port_directions, summarize_rtl
from scripts.dataset.verilog_eval_normalization_batches import _target_module_from_prompt


GENERATION_BATCH_SCHEMA_VERSION = "rtl_generation_normalization_batch_v0.1"
GENERATION_TASK_SCHEMA_VERSION = "rtl_generation_task_v0.1"
VERIFICATION_ASSET_SCHEMA_VERSION = "rtl_verification_asset_v0.1"
TEACHER_CANDIDATE_SCHEMA_VERSION = "rtl_teacher_candidate_v0.1"
PROMPT_TEMPLATE_PATH = "docs/dataset/llm_rtl_generation_task_normalization_prompt.md"
AUDIT_SCHEMA_VERSION = "rtl_generation_source_audit_v0.1"

READINESS_CATEGORIES = (
    "executable_ready",
    "needs_testbench",
    "needs_interface_review",
    "ambiguous_specification",
    "structural_only",
    "license_blocked",
    "invalid",
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
PORT_BULLET_RE = re.compile(
    r"^\s*[-*]\s*(input|output|inout)\s+([^\n]+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
PORT_DECL_RE = re.compile(
    r"\b(input|output|inout)\b\s+([^;,)\n]+)", re.IGNORECASE
)
PARENTHETICAL_WIDTH_RE = re.compile(
    r"\(\s*(\d+)\s*[- ]?\s*bits?\s*\)", re.IGNORECASE
)
MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)
PRIVATE_FIELD_NAMES = {
    "raw_reference_rtl",
    "reference_rtl",
    "rtl_code",
    "candidate_rtl",
    "raw_testbench",
    "testbench",
    "testbench_text",
    "support_files",
    "expected_vectors",
    "expected_outputs",
    "benchmark_answers",
    "tool_results",
    "tool_checks",
    "simulation_log",
    "simulator_log",
    "synthesis_report",
    "toggle_report",
    "candidate_answer",
    "rtl_answer",
}
FORBIDDEN_CLAIM_RE = re.compile(
    r"\b(?:simulation|synthesis|equivalence|rtlbench|iverilog|verilator|yosys)\b"
    r"[^\n]{0,80}\b(?:pass(?:ed)?|fail(?:ed)?|verified|successful|result)\b"
    r"|\b(?:pass(?:ed)?|verified|successful)\b[^\n]{0,80}\b"
    r"(?:simulation|synthesis|equivalence|rtlbench|iverilog|verilator|yosys)\b",
    re.IGNORECASE,
)
LICENSE_PLACEHOLDER_RE = re.compile(
    r"(?:^|[_ .-])(unknown|unconfirmed|uncertain|placeholder|todo|verify|"
    r"see[_ -]?upstream|not[_ -]?provided|missing)(?:$|[_ .-])",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
ASSET_FIELDS = {
    "schema_version", "task_id", "source_id", "top_module",
    "verification_readiness", "readiness_reasons", "reference_rtl_path",
    "testbench_path", "support_files", "input_hashes",
}
ASSET_HASH_FIELDS = {
    "source_prompt_sha256", "reference_rtl_sha256", "testbench_sha256", "support_files",
}
PORT_FIELDS = {"name", "direction", "declaration", "packed_range", "width_bits", "signed", "description"}
PROVENANCE_FIELDS = {"public_dataset_name", "public_dataset_url", "source_commit", "license", "original_source_id"}


@dataclass
class SourceRow:
    source_id: str
    source_dataset: str
    design_family: str
    specification: str | None
    reference_rtl: str | None
    testbench: str | None
    support_files: dict[str, bytes] = field(default_factory=dict)
    license: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    source_commit: str | None = None
    top_module_hint: str | None = None
    interface_hints: list[dict[str, Any]] = field(default_factory=list)
    clock_hints: list[dict[str, Any]] = field(default_factory=list)
    reset_hints: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_SV_IDENTIFIER_RE = re.compile(r"^(?:\\[^\s]+|[A-Za-z_][A-Za-z0-9_$]*)$")
_SV_TOKEN_RE = re.compile(
    r"(?:\\[^\s]+|\$?[A-Za-z_][A-Za-z0-9_$]*|::|==|!=|<=|>=|&&|\|\||\+\+|--|[{}()\[\];,#.:@=+*/%!?&|<>~'\-])"
)
_SV_KEYWORDS = {
    "always", "always_comb", "always_ff", "always_latch", "and", "assign",
    "automatic", "begin", "bit", "buf", "byte", "case", "checker", "class",
    "clocking", "const", "constraint", "continue", "cover", "covergroup",
    "default", "disable", "do", "else", "end", "endchecker", "endclass",
    "endclocking", "endfunction", "endgenerate", "endgroup", "endmodule",
    "endpackage", "endprimitive", "endprogram", "endproperty", "endsequence",
    "endtask", "endinterface", "for", "foreach", "fork", "function", "generate",
    "genvar", "if", "iff", "ifnone", "import", "inout", "input", "inside",
    "integer", "interface", "intersect", "join", "local", "localparam", "logic",
    "longint", "macromodule", "modport", "module", "negedge", "new", "nocase",
    "nonblocking", "or", "output", "package", "parameter", "posedge", "primitive",
    "program", "property", "pullup", "pulldown", "real", "ref", "reg", "release",
    "repeat", "return", "sequence", "shortint", "signed", "static", "string",
    "struct", "super", "task", "time", "timeprecision", "timeunit", "tri", "typedef",
    "union", "unsigned", "var", "virtual", "void", "wait", "while", "wire", "with",
    "within", "xnor", "xor",
}
_SV_DECLARATION_KEYWORDS = {
    "checker": "checker",
    "interface": "interface",
    "module": "module",
    "package": "package",
    "primitive": "primitive",
    "program": "program",
}
_SV_PRIMITIVES = {
    "and", "buf", "bufif0", "bufif1", "cmos", "nand", "nmos", "nor", "not",
    "notif0", "notif1", "pmos", "rcmos", "rnmos", "rpmos", "rtran", "rtranif0",
    "rtranif1", "tran", "tranif0", "tranif1", "xnor", "xor",
}


@dataclass(frozen=True)
class _VerificationDependencyReport:
    testbench_modules: frozenset[str]
    testbench_interfaces: frozenset[str]
    testbench_packages: frozenset[str]
    support_modules: frozenset[str]
    support_interfaces: frozenset[str]
    support_packages: frozenset[str]
    probable_instantiations: tuple[str, ...]
    package_imports: tuple[str, ...]
    unresolved_modules: frozenset[str]
    unresolved_packages: frozenset[str]
    ambiguous: bool
    reference_only_modules: frozenset[str]


def _strip_sv_comments_and_strings(text: str) -> tuple[str, bool]:
    """Remove comments/strings while preserving token boundaries."""
    output: list[str] = []
    index = 0
    state = "code"
    ambiguous = False
    while index < len(text):
        char = text[index]
        if state == "code":
            if text.startswith("//", index):
                output.extend((" ", " "))
                index += 2
                state = "line"
            elif text.startswith("/*", index):
                output.extend((" ", " "))
                index += 2
                state = "block"
            elif char == '"':
                output.append(" ")
                index += 1
                state = "string"
            else:
                output.append(char)
                index += 1
        elif state == "line":
            if char == "\n":
                output.append("\n")
                state = "code"
            else:
                output.append(" ")
            index += 1
        elif state == "block":
            if text.startswith("*/", index):
                output.extend((" ", " "))
                index += 2
                state = "code"
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
        else:
            if char == "\\" and index + 1 < len(text):
                output.extend((" ", " "))
                index += 2
            elif char == '"':
                output.append(" ")
                index += 1
                state = "code"
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
    if state != "code":
        ambiguous = True
    return "".join(output), ambiguous


def _sv_tokens(text: str) -> tuple[list[str], bool]:
    without_comments, ambiguous = _strip_sv_comments_and_strings(text)
    tokens = _SV_TOKEN_RE.findall(without_comments)
    return tokens, ambiguous or any(token.startswith("\\") for token in tokens)


def _sv_identifier(value: str) -> bool:
    return bool(_SV_IDENTIFIER_RE.fullmatch(value)) and value.casefold() not in _SV_KEYWORDS


def _sv_declarations(tokens: list[str]) -> dict[str, set[str]]:
    declarations = {kind: set() for kind in _SV_DECLARATION_KEYWORDS.values()}
    for index, token in enumerate(tokens):
        kind = _SV_DECLARATION_KEYWORDS.get(token.casefold())
        if kind is None:
            continue
        name_index = index + 1
        if kind == "module" and name_index < len(tokens) and tokens[name_index].casefold() == "automatic":
            name_index += 1
        if name_index < len(tokens) and _sv_identifier(tokens[name_index]):
            declarations[kind].add(tokens[name_index])
    return declarations


def _sv_declaration_count(tokens: list[str], kind: str, name: str) -> int:
    """Count exact declarations without exposing source text in diagnostics."""
    count = 0
    for index, token in enumerate(tokens[:-1]):
        if token.casefold() != kind.casefold():
            continue
        name_index = index + 1
        if kind.casefold() == "module" and tokens[name_index].casefold() == "automatic":
            name_index += 1
        if name_index < len(tokens) and tokens[name_index] == name:
            count += 1
    return count


def _sv_skip_parenthesized(tokens: list[str], index: int) -> int:
    if index >= len(tokens) or tokens[index] != "(":
        return index
    depth = 0
    while index < len(tokens):
        if tokens[index] == "(":
            depth += 1
        elif tokens[index] == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(tokens)


def _sv_scope_before(tokens: list[str]) -> list[tuple[str, ...]]:
    end_for = {
        "endchecker": "checker",
        "endmodule": "module",
        "endinterface": "interface",
        "endpackage": "package",
        "endprimitive": "primitive",
        "endprogram": "program",
    }
    scopes: list[str] = []
    before: list[tuple[str, ...]] = []
    for index, token in enumerate(tokens):
        before.append(tuple(scopes))
        kind = _SV_DECLARATION_KEYWORDS.get(token.casefold())
        if kind is not None:
            scopes.append(kind)
        elif token.casefold() in end_for:
            expected = end_for[token.casefold()]
            if expected in scopes:
                while scopes:
                    current = scopes.pop()
                    if current == expected:
                        break
    return before


def _sv_probable_instantiations(tokens: list[str]) -> tuple[list[str], bool]:
    scopes = _sv_scope_before(tokens)
    instances: list[str] = []
    ambiguous = False
    index = 0
    while index + 2 < len(tokens):
        type_name = tokens[index]
        if not _sv_identifier(type_name) or not scopes[index] or scopes[index][-1] not in {"module", "interface", "program", "checker"}:
            index += 1
            continue
        instance_index = index + 1
        if tokens[instance_index] == "#":
            instance_index += 1
            # Procedural delay controls commonly appear as ``#5`` after an
            # identifier. Only ``Type #(params) instance(...)`` is a
            # parameterized instantiation candidate; a non-parenthesized
            # delay must not poison the whole dependency analysis.
            if instance_index >= len(tokens) or tokens[instance_index] != "(":
                index += 1
                continue
            instance_index = _sv_skip_parenthesized(tokens, instance_index)
        if (
            instance_index + 1 < len(tokens)
            and _sv_identifier(tokens[instance_index])
            and tokens[instance_index + 1] == "("
            and type_name.casefold() not in _SV_PRIMITIVES
        ):
            previous = tokens[index - 1].casefold() if index else ""
            if (
                not type_name.startswith("$")
                and previous not in {
                    "module", "interface", "package", "program", "checker", "primitive",
                    "function", "task", "class", "typedef", "void", "logic", "wire", "reg",
                    "bit", "byte", "int", "integer", "longint", "shortint", "string", "time",
                    "real", "signed", "unsigned", "var", "static", "automatic", "ref", "input",
                    "output", "inout", "begin", "end", "else", "if", "while", "for", "foreach",
                    "case", "return", "assign", "always", "always_comb", "always_ff", "initial",
                    "do", "fork", "join", "wait", "assert", "cover",
                }
            ):
                instances.append(type_name)
                index = instance_index + 1
                continue
        index += 1
    return instances, ambiguous


def _sv_package_imports(tokens: list[str]) -> tuple[list[str], bool]:
    imports: list[str] = []
    ambiguous = False
    for index, token in enumerate(tokens[:-1]):
        if token.casefold() == "import":
            if _sv_identifier(tokens[index + 1]):
                imports.append(tokens[index + 1])
            else:
                ambiguous = True
    return imports, ambiguous


def _support_texts(row: SourceRow) -> Iterable[str]:
    for content in row.support_files.values():
        try:
            yield content.decode("utf-8")
        except UnicodeDecodeError:
            yield ""


def _verification_dependency_report(row: SourceRow) -> _VerificationDependencyReport:
    testbench_tokens, testbench_ambiguous = _sv_tokens(row.testbench or "")
    testbench_decl = _sv_declarations(testbench_tokens)
    testbench_instances, instance_ambiguous = _sv_probable_instantiations(testbench_tokens)
    package_imports, import_ambiguous = _sv_package_imports(testbench_tokens)
    support_decl = {kind: set() for kind in ("module", "interface", "package")}
    support_ambiguous = False
    for support_text in _support_texts(row):
        tokens, ambiguous = _sv_tokens(support_text)
        parsed = _sv_declarations(tokens)
        for kind in support_decl:
            support_decl[kind].update(parsed[kind])
        support_ambiguous = support_ambiguous or ambiguous

    declared_modules = testbench_decl["module"] | support_decl["module"]
    declared_interfaces = testbench_decl["interface"] | support_decl["interface"]
    declared_packages = testbench_decl["package"] | support_decl["package"]
    expected_top = row.top_module_hint
    unresolved_modules = frozenset(
        type_name
        for type_name in testbench_instances
        if type_name != expected_top
        and type_name not in declared_modules
        and type_name not in declared_interfaces
    )
    unresolved_packages = frozenset(
        package_name for package_name in package_imports if package_name not in declared_packages
    )
    reference_decl = _sv_declarations(_sv_tokens(row.reference_rtl or "")[0])
    reference_only = frozenset(unresolved_modules & reference_decl["module"])
    return _VerificationDependencyReport(
        testbench_modules=frozenset(testbench_decl["module"]),
        testbench_interfaces=frozenset(testbench_decl["interface"]),
        testbench_packages=frozenset(testbench_decl["package"]),
        support_modules=frozenset(support_decl["module"]),
        support_interfaces=frozenset(support_decl["interface"]),
        support_packages=frozenset(support_decl["package"]),
        probable_instantiations=tuple(testbench_instances),
        package_imports=tuple(package_imports),
        unresolved_modules=unresolved_modules,
        unresolved_packages=unresolved_packages,
        ambiguous=testbench_ambiguous or instance_ambiguous or import_ambiguous or support_ambiguous,
        reference_only_modules=reference_only,
    )


def _support_reference_safety_errors(row: SourceRow) -> list[str]:
    reference = _text_bytes(row.reference_rtl)
    if not reference:
        return []
    errors: list[str] = []
    for content in row.support_files.values():
        if content == reference or (content and content in reference):
            errors.append("support files must not contain reference RTL bytes")
            break
    return errors


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _text_bytes(value: str | None) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else b""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _relative_display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except (OSError, ValueError):
        return path.name


def _license_is_usable(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return LICENSE_PLACEHOLDER_RE.search(value.strip()) is None


def _license_identity(value: str | None) -> str:
    """Return the stable identity form of a license label, without source paths."""
    if not isinstance(value, str):
        return "unknown"
    return re.sub(r"\s+", " ", value.strip()).casefold() or "unknown"


def _license_from_checkout(root: Path, fallback: str | None) -> str | None:
    """Use a local license identifier only when the staged checkout identifies it."""
    candidates = [root / "LICENSE", root.parent / "LICENSE", root.parent.parent / "LICENSE"]
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            first_line = candidate.read_text(encoding="utf-8").splitlines()[0].strip().lower()
        except (OSError, UnicodeError, IndexError):
            continue
        if first_line.startswith("mit license"):
            return "MIT"
        if "apache license" in first_line and "2.0" in first_line:
            return "Apache-2.0"
        if first_line.startswith("bsd"):
            return "BSD"
    return fallback


def _safe_source_tree(path: Path) -> list[str]:
    problems: list[str] = []
    if path.is_symlink():
        return [f"input must not be a symlink: {path}"]
    if path.is_dir():
        for candidate in path.rglob("*"):
            if candidate.is_symlink():
                problems.append(f"input tree contains a symlink: {candidate}")
    return problems


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked source artifact: {path}")
    if not path.is_file():
        raise ValueError(f"source artifact is not a regular file: {path}")
    return path.read_bytes()


def _port_name(declaration: str) -> str | None:
    text = re.sub(r"\[[^\]]+\]", " ", declaration)
    # Parenthetical descriptions such as "d (8 bits)" are not identifiers.
    text = text.split("(", 1)[0]
    text = re.sub(
        r"\b(?:input|output|inout|wire|reg|logic|signed|unsigned|integer|bit)\b",
        " ", text, flags=re.IGNORECASE,
    )
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", text)
    return names[-1] if names else None


def _width_bits(packed_range: str | None) -> int | None:
    if not packed_range:
        return 1
    match = re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", packed_range)
    if not match:
        return None
    return abs(int(match.group(1)) - int(match.group(2))) + 1


def _interface_hints(text: str | None) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        return []
    matches = list(PORT_BULLET_RE.finditer(text))
    if not matches:
        matches = list(PORT_DECL_RE.finditer(text))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in matches:
        direction = match.group(1).lower()
        declaration_body = re.sub(r"\s+", " ", match.group(2)).strip()
        declaration = f"{direction} {declaration_body}"
        name = _port_name(match.group(2))
        if not name or name in seen:
            continue
        seen.add(name)
        packed_match = re.search(r"\[[^\]]+\]", declaration)
        packed_range = packed_match.group(0) if packed_match else None
        width_bits = _width_bits(packed_range)
        if packed_range is None:
            parenthetical_width = PARENTHETICAL_WIDTH_RE.search(declaration)
            if parenthetical_width:
                width_bits = int(parenthetical_width.group(1))
        result.append({
            "name": name,
            "direction": direction,
            "declaration": declaration,
            "packed_range": packed_range,
            "width_bits": width_bits,
            "signed": bool(re.search(r"\bsigned\b", declaration, re.IGNORECASE)),
            "description": None,
        })
    return result


def _hints_from_interface_file(raw: bytes) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, []
    names = MODULE_RE.findall(text)
    return (names[0] if len(names) == 1 else None), _interface_hints(text)


def _reset_synchrony_hint(
    specification: str,
    reset_names: list[str],
) -> bool | None:
    """Infer synchrony from language attached to the reset clause.

    Specifications can describe synchronous enables alongside an asynchronous
    reset, for example ``asynchronous ... areset, synchronous ... load``.
    Looking for timing words across the whole specification incorrectly makes
    that reset ambiguous.  Keep the hint conservative and only associate a
    timing word with a nearby reset term without crossing clause punctuation.
    """
    reset_terms = [r"reset", r"rst"]
    reset_terms.extend(
        re.escape(name)
        for name in reset_names
        if isinstance(name, str) and name
    )
    reset_pattern = r"(?:" + "|".join(reset_terms) + r")"
    mode_pattern = r"(?:asynchronous|async|synchronous|sync)"
    separator = r"[^,.;!?]{0,80}"
    modes: set[bool] = set()

    for match in re.finditer(
        rf"\b(?P<mode>{mode_pattern})\b{separator}\b(?:{reset_pattern})\b",
        specification,
        re.IGNORECASE,
    ):
        modes.add(match.group("mode").lower() in {"synchronous", "sync"})
    for match in re.finditer(
        rf"\b(?:{reset_pattern})\b{separator}\b(?P<mode>{mode_pattern})\b",
        specification,
        re.IGNORECASE,
    ):
        modes.add(match.group("mode").lower() in {"synchronous", "sync"})

    if len(modes) == 1:
        return modes.pop()
    return None


def _clock_reset_hints(specification: str | None, ports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(specification, str):
        return [], []
    clocks: list[dict[str, Any]] = []
    resets: list[dict[str, Any]] = []
    has_reset_language = bool(re.search(r"\breset\b", specification, re.IGNORECASE))
    active_level_match = re.search(r"\bactive[- ](high|low)\b", specification, re.IGNORECASE)
    explicit_active_level = active_level_match.group(1).lower() if active_level_match else None

    def is_reset_name(name: str) -> bool:
        lowered = name.lower()
        return (
            lowered in {"rst", "reset", "rst_n", "reset_n", "resetn", "areset", "aresetn", "areset_n", "ar"}
            or lowered.startswith("reset_") or lowered.startswith("rst_")
            or (lowered == "r" and has_reset_language)
        )

    reset_names = [
        str(port["name"])
        for port in ports
        if isinstance(port, dict) and "name" in port and is_reset_name(str(port["name"]))
    ]
    explicit_synchronous = _reset_synchrony_hint(specification, reset_names)
    for port in ports:
        name = str(port["name"])
        lowered = name.lower()
        if lowered in {"clk", "clock"} or lowered.endswith("_clk"):
            edge = "negedge" if re.search(r"negative[- ]edge|negedge", specification, re.IGNORECASE) else "posedge"
            clocks.append({"signal": name, "edge": edge})
        reset_name = is_reset_name(name)
        if reset_name:
            active = "low" if lowered.endswith("_n") or lowered.endswith("n") else "high"
            resets.append({
                "signal": name,
                "active_level": explicit_active_level or active,
                "synchronous": explicit_synchronous,
            })
    return clocks, resets


def _family(source_id: str, specification: str | None, rtl: str | None, fallback: str | None = None) -> str:
    if fallback and fallback not in {"unknown", "verilog_eval"}:
        return fallback
    text = f"{source_id} {specification or ''} {rtl or ''}".lower()
    for name, words in (
        ("fsm", ("fsm", "finite state", "state machine")),
        ("counter", ("counter", "count", "timer")),
        ("fifo", ("fifo",)),
        ("handshake", ("handshake", "valid", "ready")),
        ("shift_register", ("shift", "rotate")),
        ("mux", ("mux", "multiplexer")),
        ("register", ("dff", "flip-flop", "register")),
        ("arithmetic", ("adder", "add", "popcount")),
    ):
        if name == "counter" and "popcount" in text:
            continue
        if any(word in text for word in words):
            return name
    return fallback or "rtl"


def _warnings(row: SourceRow) -> list[str]:
    warnings = list(row.warnings)
    if not row.specification:
        warnings.append("missing_specification")
    if not row.top_module_hint:
        warnings.append("top_module_hint_missing")
    if not row.interface_hints:
        warnings.append("interface_hint_missing")
    if not row.testbench:
        warnings.append("testbench_missing")
    if not _license_is_usable(row.license):
        warnings.append("license_placeholder")
    return sorted(set(warnings))


def _canonical_provenance(row: SourceRow) -> dict[str, Any]:
    """Project source provenance into the fixed generation-task provenance shape."""
    return {
        "public_dataset_name": str(row.provenance.get("public_dataset_name") or row.source_dataset),
        "public_dataset_url": row.provenance.get("public_dataset_url"),
        "source_commit": row.provenance.get("source_commit"),
        "license": row.license,
        "original_source_id": row.source_id,
    }


def _readiness(row: SourceRow) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not row.source_id or not row.specification or not row.reference_rtl:
        return "invalid", ["missing source ID, specification, or reference RTL"]
    if not _license_is_usable(row.license):
        reasons.append("license metadata is missing or unresolved")
        return "license_blocked", reasons
    if not row.top_module_hint or not IDENTIFIER_RE.fullmatch(row.top_module_hint):
        return "needs_interface_review", ["top module is not deterministic"]
    if not row.interface_hints or len({p["name"] for p in row.interface_hints}) != len(row.interface_hints):
        return "needs_interface_review", ["interface ports are not deterministic"]
    if not row.testbench:
        return "needs_testbench", ["testbench is missing"]
    if not row.provenance:
        return "invalid", ["provenance is missing"]

    dependency = _verification_dependency_report(row)
    if _support_reference_safety_errors(row):
        return "needs_testbench", [
            "support files must not contain reference RTL bytes",
        ]
    if dependency.ambiguous:
        return "needs_interface_review", [
            "verification dependency analysis is ambiguous",
        ]

    testbench_tokens, _ = _sv_tokens(row.testbench)
    if (
        _sv_declaration_count(testbench_tokens, "module", "tb") != 1
        or dependency.probable_instantiations.count(row.top_module_hint) != 1
    ):
        return "needs_testbench", [
            "testbench must declare tb and instantiate the expected candidate top",
        ]
    if dependency.unresolved_packages:
        return "needs_interface_review", [
            "testbench has unresolved package dependencies",
        ]
    if dependency.unresolved_modules:
        if dependency.reference_only_modules:
            return "needs_testbench", [
                "testbench depends on a non-candidate module that is available only in private reference material",
            ]
        return "needs_testbench", [
            "testbench has an unresolved non-candidate dependency",
        ]
    return "executable_ready", reasons


def _source_from_example(example: RawPublicExample) -> SourceRow:
    prompt = example.metadata.get("raw_prompt") if isinstance(example.metadata, dict) else None
    if not isinstance(prompt, str):
        prompt = example.artifacts.get("lint_log")
    rtl = example.artifacts.get("rtl_code")
    testbench = example.artifacts.get("testbench")
    support: dict[str, bytes] = {}
    source_root = example.root
    if isinstance(source_root, Path):
        support_path = source_root / f"{example.source_id}_ifc.txt"
        if support_path.exists() and not support_path.is_symlink():
            support[f"{example.source_id}_ifc.txt"] = _read_bytes(support_path)
    top = _target_module_from_prompt(prompt)
    interface = _interface_hints(prompt)
    if support:
        support_top, support_interface = _hints_from_interface_file(next(iter(support.values())))
        top = top or support_top
        interface = interface or support_interface
    clocks, resets = _clock_reset_hints(prompt, interface)
    license_value = _license_from_checkout(source_root.parent, example.license)
    return SourceRow(
        source_id=example.source_id,
        source_dataset=str(example.provenance.get("public_dataset_name") or example.source),
        design_family=_family(example.source_id, prompt, rtl, example.design_family),
        specification=prompt,
        reference_rtl=rtl,
        testbench=testbench,
        support_files=support,
        license=license_value,
        provenance=deepcopy(example.provenance),
        source_path=_relative_display(source_root),
        top_module_hint=top,
        interface_hints=interface,
        clock_hints=clocks,
        reset_hints=resets,
    )


def _payload_rows(path: Path) -> tuple[list[Any], list[str]]:
    if path.is_symlink():
        return [], [f"input must not be a symlink: {path}"]
    try:
        if path.suffix.lower() == ".jsonl":
            rows: list[Any] = []
            errors: list[str] = []
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    errors.append(f"line {number}: malformed JSON: {exc.msg}")
            return rows, errors
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [f"could not read JSON input {path}: {exc}"]
    if isinstance(value, list):
        return value, []
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        return value["rows"], []
    return [], ["JSON input must be an array or object with a rows array"]


def _source_from_normalized(row: dict[str, Any], input_path: Path, index: int) -> SourceRow:
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    source_id = row.get("source_id") or row.get("task_id") or row.get("id")
    specification = row.get("specification") if isinstance(row.get("specification"), str) else row.get("prompt")
    rtl = artifacts.get("rtl_code") or row.get("reference_rtl") or row.get("rtl_code")
    testbench = artifacts.get("testbench") or row.get("testbench")
    source_dataset = row.get("source_dataset") or provenance.get("public_dataset_name") or "local_rtl"
    context = row.get("design_context") if isinstance(row.get("design_context"), dict) else {}
    top = context.get("target_module_name") or _target_module_from_prompt(specification)
    interface = context.get("interface_ports_from_prompt")
    if not isinstance(interface, list) or not interface:
        interface = _interface_hints(specification)
    normalized_interface: list[dict[str, Any]] = []
    for item in interface:
        if isinstance(item, dict) and item.get("name"):
            normalized_interface.append({
                "name": str(item["name"]),
                "direction": item.get("direction"),
                "declaration": str(item.get("declaration") or item["name"]),
                "packed_range": item.get("packed_range"),
                "width_bits": item.get("width_bits"),
                "signed": bool(item.get("signed", False)),
                "description": item.get("description"),
            })
        elif isinstance(item, str):
            parsed = _interface_hints(item)
            normalized_interface.extend(parsed)
    support: dict[str, bytes] = {}
    raw_support = artifacts.get("support_files") or row.get("support_files")
    if isinstance(raw_support, dict):
        for name, value in raw_support.items():
            if isinstance(name, str) and isinstance(value, str):
                support[name] = value.encode("utf-8")
    elif isinstance(raw_support, list):
        for item in raw_support:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str):
                support[item["path"]] = item["content"].encode("utf-8")
    clocks, resets = _clock_reset_hints(specification, normalized_interface)
    return SourceRow(
        source_id=str(source_id) if source_id is not None else "",
        source_dataset=str(source_dataset),
        design_family=_family(str(source_id), specification, rtl, row.get("design_family")),
        specification=specification if isinstance(specification, str) else None,
        reference_rtl=rtl if isinstance(rtl, str) else None,
        testbench=testbench if isinstance(testbench, str) else None,
        support_files=support,
        license=row.get("license") if isinstance(row.get("license"), str) else None,
        provenance=deepcopy(provenance),
        source_path=_relative_display(input_path),
        source_commit=provenance.get("source_commit") if isinstance(provenance.get("source_commit"), str) else None,
        top_module_hint=str(top) if isinstance(top, str) else None,
        interface_hints=normalized_interface,
        clock_hints=clocks,
        reset_hints=resets,
        warnings=list(row.get("normalization_warnings") or []) if isinstance(row.get("normalization_warnings"), list) else [],
    )


def discover_source_rows(input_path: Path) -> tuple[list[SourceRow], list[str]]:
    """Discover local source rows without downloading or executing anything."""
    errors = _safe_source_tree(input_path)
    if errors:
        return [], errors
    if input_path.is_dir():
        # The audit reports the concrete dataset directory, while the existing
        # adapter normally receives its checkout parent.  Support both forms so
        # the audited path can be passed directly to the CLI.
        if list(input_path.glob("*_prompt.txt")):
            rows: list[SourceRow] = []
            for prompt_path in sorted(input_path.glob("*_prompt.txt")):
                if prompt_path.is_symlink():
                    errors.append(f"source artifact must not be a symlink: {prompt_path}")
                    continue
                source_id = prompt_path.name[: -len("_prompt.txt")]
                ref_path = input_path / f"{source_id}_ref.sv"
                test_path = input_path / f"{source_id}_test.sv"
                try:
                    prompt = _read_bytes(prompt_path).decode("utf-8")
                    rtl = _read_bytes(ref_path).decode("utf-8")
                    testbench = _read_bytes(test_path).decode("utf-8") if test_path.exists() else None
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append(f"{source_id}: {exc}")
                    continue
                support: dict[str, bytes] = {}
                interface = _interface_hints(prompt)
                top = _target_module_from_prompt(prompt)
                support_path = input_path / f"{source_id}_ifc.txt"
                if support_path.exists():
                    try:
                        support[support_path.name] = _read_bytes(support_path)
                        support_top, support_interface = _hints_from_interface_file(support[support_path.name])
                        top = top or support_top
                        interface = interface or support_interface
                    except (OSError, ValueError) as exc:
                        errors.append(f"{source_id}: {exc}")
                        continue
                license_value = _license_from_checkout(input_path.parent, "see_upstream_verilog_eval")
                clocks, resets = _clock_reset_hints(prompt, interface)
                rows.append(SourceRow(
                    source_id=source_id,
                    source_dataset="VerilogEval",
                    design_family=_family(source_id, prompt, rtl),
                    specification=prompt,
                    reference_rtl=rtl,
                    testbench=testbench,
                    support_files=support,
                    license=license_value,
                    provenance={
                        "public_dataset_name": "VerilogEval",
                        "public_dataset_url": "https://github.com/NVlabs/verilog-eval",
                        "source_commit": None,
                        "license": license_value,
                        "original_source_id": source_id,
                    },
                    source_path=_relative_display(input_path),
                    top_module_hint=top,
                    interface_hints=interface,
                    clock_hints=clocks,
                    reset_hints=resets,
                ))
            return rows, errors
        try:
            result = get_adapter("verilog_eval").discover_examples(input_path, ImportOptions())
        except (OSError, ValueError) as exc:
            return [], [str(exc)]
        rows = [_source_from_example(example) for example in result.examples]
        errors.extend(item.reason + (": " + "; ".join(item.errors) if item.errors else "") for item in result.rejections)
        return rows, errors
    if input_path.is_file() and input_path.suffix.lower() in {".json", ".jsonl"}:
        payload, load_errors = _payload_rows(input_path)
        errors.extend(load_errors)
        rows: list[SourceRow] = []
        for index, value in enumerate(payload, 1):
            if isinstance(value, dict):
                rows.append(_source_from_normalized(value, input_path, index))
            else:
                errors.append(f"row {index} must be a JSON object")
        return rows, errors
    return [], [f"unsupported source input: {input_path}"]


def _task_id(row: SourceRow) -> str:
    dataset_slug = re.sub(r"[^A-Za-z0-9]+", "_", row.source_dataset).strip("_").lower() or "source"
    source_slug = re.sub(r"[^A-Za-z0-9]+", "_", row.source_id).strip("_").lower() or "row"
    source_commit = row.source_commit if isinstance(row.source_commit, str) else row.provenance.get("source_commit")
    license_value = row.license if row.license is not None else row.provenance.get("license")
    identity = _json_bytes({
        "source_dataset": row.source_dataset,
        "source_id": row.source_id,
        "source_commit": source_commit if isinstance(source_commit, str) else None,
        "license_identity": _license_identity(license_value),
    })
    return f"rtlgen_{dataset_slug}_{source_slug}_{_sha256(identity)[:12]}"


def _public_row(row: SourceRow, task_id: str) -> dict[str, Any]:
    clocks, resets = row.clock_hints, row.reset_hints
    if not clocks and not resets:
        clocks, resets = _clock_reset_hints(row.specification, row.interface_hints)
    return {
        "task_id": task_id,
        "source_id": row.source_id,
        "source_dataset": row.source_dataset,
        "design_family": row.design_family,
        "license": row.license,
        "provenance": _canonical_provenance(row),
        "raw_specification": row.specification,
        "deterministic_top_module_hint": row.top_module_hint,
        "deterministic_interface_hints": deepcopy(row.interface_hints),
        "deterministic_clock_hints": deepcopy(clocks),
        "deterministic_reset_hints": deepcopy(resets),
        "normalization_warnings": _warnings(row),
    }


def _asset_record(row: SourceRow, task_id: str) -> dict[str, Any]:
    readiness, reasons = _readiness(row)
    workspace = f"workspace/{task_id}"
    ref_path = f"{workspace}/reference.sv" if row.reference_rtl is not None else None
    tb_path = f"{workspace}/testbench.sv" if row.testbench is not None else None
    support_paths = [f"{workspace}/support/{name}" for name in sorted(row.support_files)]
    return {
        "schema_version": VERIFICATION_ASSET_SCHEMA_VERSION,
        "task_id": task_id,
        "source_id": row.source_id,
        "top_module": row.top_module_hint,
        "verification_readiness": readiness,
        "readiness_reasons": reasons,
        "reference_rtl_path": ref_path,
        "testbench_path": tb_path,
        "support_files": support_paths,
        "input_hashes": {
            "source_prompt_sha256": _sha256(_text_bytes(row.specification)),
            "reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)) if row.reference_rtl is not None else None,
            "testbench_sha256": _sha256(_text_bytes(row.testbench)) if row.testbench is not None else None,
            "support_files": [
                {"path": path, "sha256": _sha256(row.support_files[name])}
                for name, path in sorted((name, f"{workspace}/support/{name}") for name in row.support_files)
            ],
        },
    }


def _walk_strings(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_strings(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{prefix}[{index}]")


def _walk_keys(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield child, str(key)
            yield from _walk_keys(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_keys(item, f"{prefix}[{index}]")


def _path_variants(path: Path) -> set[str]:
    values = {str(path), str(path.resolve())}
    try:
        values.add(str(path.resolve().relative_to(Path.cwd().resolve())))
    except (OSError, ValueError):
        pass
    return {value for value in values if value}


def _public_leak_errors(
    payload: Any,
    rows: list[SourceRow],
    private_root: Path | None,
    input_path: Path | None = None,
) -> list[str]:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    public_strings = [value for _, value in _walk_strings(payload)]
    errors: list[str] = []
    for row in rows:
        for label, value in (("reference RTL", row.reference_rtl), ("testbench", row.testbench)):
            if isinstance(value, str) and value and (value in serialized or any(value in public_value for public_value in public_strings)):
                errors.append(f"full {label} content would be present in public batch")
        for name, value in row.support_files.items():
            if value and (value.decode("utf-8", errors="ignore") in serialized or any(value.decode("utf-8", errors="ignore") in public_value for public_value in public_strings)):
                errors.append(f"support file content would be present in public batch: {name}")
    path_values: set[str] = set()
    public_identity_markers = {
        marker.casefold()
        for row in rows
        for marker in (
            row.source_id,
            row.source_dataset,
            row.provenance.get("public_dataset_name"),
            row.provenance.get("original_source_id"),
        )
        if isinstance(marker, str) and marker
    }
    if private_root is not None:
        path_values.update(_path_variants(private_root))
        path_values.update({private_root.name, "workspace"})
    if input_path is not None:
        path_values.update(_path_variants(input_path))
        # Checkout directory names are useful leak sentinels, but URLs remain
        # legitimate provenance values and are handled separately below.
        path_values.update(part for part in input_path.parts[-2:] if part not in {"/", "", "data", ".local_data"} and len(part) > 2)
    for value in sorted(path_values, key=len, reverse=True):
        if value.casefold() in public_identity_markers:
            continue
        if value and any(value in public_value and "://" not in public_value for public_value in public_strings):
            errors.append("local input or private workspace path would be present in public batch")
            break
    for value in public_strings:
        if ".local_data" in value and "://" not in value:
            errors.append(".local_data path would be present in public batch")
        if WINDOWS_DRIVE_RE.match(value) or value.startswith("/") and "://" not in value:
            errors.append("absolute or Windows path would be present in public batch")
        if ("workspace/" in value or "workspace\\" in value) and "://" not in value:
            errors.append("private workspace path would be present in public batch")
    for path, key in _walk_keys(payload):
        if key.lower() in PRIVATE_FIELD_NAMES:
            errors.append(f"forbidden private field in public payload: {path}")
    return sorted(set(errors))


def _ensure_directory(path: Path, label: str, *, allow_local_data: bool) -> list[str]:
    errors: list[str] = []
    if _path_contains_symlink(path):
        return [f"{label} path contains a symlink: {path}"]
    if path.exists() and not path.is_dir():
        return [f"{label} must be a directory: {path}"]
    if not allow_local_data and ".local_data" in path.parts:
        errors.append(f"{label} must not be inside .local_data")
    return errors


def _path_contains_symlink(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts:
        if part == path.anchor:
            continue
        current = current / part
        if current.is_symlink():
            return True
    return False


def _is_hardlinked(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_nlink > 1
    except OSError:
        return False


def _samefile(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _atomic_write(path: Path, content: bytes) -> None:
    if _path_contains_symlink(path):
        raise ValueError(f"output path contains a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".rtlgen-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _managed_batch(path: Path) -> bool:
    if path.is_symlink() or not path.is_file() or not re.fullmatch(r"batch_\d{3}\.json", path.name):
        return False
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(value,dict) and value.get("batch_schema_version")==GENERATION_BATCH_SCHEMA_VERSION and value.get("created_by")=="export_rtl_generation_normalization_batches"


def _prepare_public_output(output_dir: Path, planned: list[Path], force: bool) -> list[str]:
    errors = _ensure_directory(output_dir, "output directory", allow_local_data=False)
    if errors: return errors
    output_dir.mkdir(parents=True, exist_ok=True)
    managed = [p for p in output_dir.iterdir() if _managed_batch(p)]
    if managed and not force:
        return ["output directory already contains managed generation batches; use --force"]
    for path in sorted(output_dir.iterdir()):
        if not re.fullmatch(r"batch_\d{3}\.json", path.name):
            continue
        if path.is_symlink():
            errors.append(f"batch output must not be a symlink: {path}")
        elif _is_hardlinked(path):
            errors.append(f"batch output must not be a hard-link alias: {path}")
        elif not force and path.exists():
            errors.append(f"batch output already exists: {path}")
        elif force and not _managed_batch(path):
            errors.append(f"refusing to replace unknown batch-like file: {path}")
    for path in planned:
        if path.exists() and path.is_symlink():
            errors.append(f"batch output must not be a symlink: {path}")
        if path.exists() and _is_hardlinked(path):
            errors.append(f"batch output must not be a hard-link alias: {path}")
        if path.exists() and not force:
            errors.append(f"batch output already exists: {path}")
        if path.exists() and force and not _managed_batch(path):
            errors.append(f"refusing to replace unknown batch output: {path}")
    if errors: return sorted(set(errors))
    if force:
        for path in managed:
            if path not in planned:
                path.unlink()
    return []


def _managed_asset_manifest(path: Path) -> set[str] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not records or any(not isinstance(record, dict) or record.get("schema_version") != VERIFICATION_ASSET_SCHEMA_VERSION for record in records):
        return None
    managed = {"verification_assets.jsonl"}
    for record in records:
        for key in ("reference_rtl_path", "testbench_path"):
            if isinstance(record.get(key), str):
                managed.add(record[key])
        managed.update(value for value in record.get("support_files", []) if isinstance(value, str))
    return managed


def _prepare_private_output(private_dir: Path, planned_files: list[Path], force: bool) -> list[str]:
    errors = _ensure_directory(private_dir, "private output directory", allow_local_data=True)
    if errors: return errors
    private_dir.mkdir(parents=True, exist_ok=True)
    old_manifest_paths = _managed_asset_manifest(private_dir / "verification_assets.jsonl")
    for path in planned_files:
        if path.exists() and path.is_symlink():
            errors.append(f"private output must not be a symlink: {path}")
        elif _is_hardlinked(path):
            errors.append(f"private output must not be a hard-link alias: {path}")
        elif path.exists() and not force:
            errors.append(f"private output already exists: {path}; use --force")
        elif path.exists() and not path.is_file():
            errors.append(f"private output must be a file: {path}")
        elif path.exists() and force:
            try:
                relative = str(path.relative_to(private_dir))
            except ValueError:
                relative = path.name
            if old_manifest_paths is None or relative not in old_manifest_paths:
                errors.append(f"refusing to replace unknown private output: {path}")
    return sorted(set(errors))


def _source_duplicate_errors(rows: list[SourceRow]) -> list[str]:
    seen: set[str] = set(); errors=[]
    for row in rows:
        if not row.source_id:
            errors.append("source row has an empty source_id")
        elif row.source_id in seen:
            errors.append(f"duplicate source_id: {row.source_id}")
        seen.add(row.source_id)
    return errors


def export_generation_normalization_batches(
    input_path: Path,
    output_dir: Path,
    private_output_dir: Path,
    *,
    batch_size: int = 5,
    limit: int | None = None,
    start_index: int = 0,
    force: bool = False,
    source_commit: str | None = None,
    source_ids: Iterable[str] | None = None,
    correction_manifest: Path | None = None,
    correction_root: Path | None = None,
    correction_version: str | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    rows, discovery_errors = discover_source_rows(input_path)
    errors.extend(discovery_errors)
    if source_commit is not None:
        if not SOURCE_COMMIT_RE.fullmatch(source_commit):
            errors.append("source_commit must be exactly 40 lowercase hexadecimal characters")
        else:
            for row in rows:
                existing = row.provenance.get("source_commit") if isinstance(row.provenance, dict) else None
                if existing not in (None, "", source_commit):
                    errors.append(f"source commit mismatch for source_id: {row.source_id}")
                row.source_commit = source_commit
                row.provenance["source_commit"] = source_commit
    requested_ids = list(source_ids) if source_ids is not None else None
    if requested_ids is not None:
        if not requested_ids:
            errors.append("source-ID allowlist must not be empty")
        if len(requested_ids) != len(set(requested_ids)):
            errors.append("source-ID allowlist contains duplicates")
        rows_by_id = {row.source_id: row for row in rows}
        missing_ids = sorted(set(requested_ids) - set(rows_by_id))
        if missing_ids:
            errors.append(f"source-ID allowlist contains missing IDs: {missing_ids}")
        rows = [rows_by_id[source_id] for source_id in requested_ids if source_id in rows_by_id]
    if batch_size < 1: errors.append("--batch-size must be at least 1")
    if start_index < 0: errors.append("--start-index must be at least 0")
    if limit is not None and limit < 1: errors.append("--limit must be at least 1")
    if not input_path.exists(): errors.append(f"input does not exist: {input_path}")
    if output_dir.resolve() == private_output_dir.resolve() or _is_within(output_dir, private_output_dir) or _is_within(private_output_dir, output_dir):
        errors.append("public and private output directories must be separate")
    errors.extend(_source_duplicate_errors(rows))
    # An explicit allowlist is an operator-authored packet order. Preserve it
    # byte-for-byte so the returned normalization rows can be checked by
    # position as well as by identity. Unbounded exports retain deterministic
    # source ordering for compatibility with the original workflow.
    if requested_ids is None:
        rows = sorted(rows, key=lambda item: (item.source_id, item.source_dataset, _json_bytes(item.provenance)))
    selected = rows[start_index:]
    if limit is not None: selected = selected[:limit]
    if not selected: errors.append("no source rows remain after applying start-index/limit")
    for row in selected:
        if not row.specification:
            errors.append(f"missing specification for source_id: {row.source_id}")
        if not row.reference_rtl:
            errors.append(f"missing reference RTL for source_id: {row.source_id}")
    private_selected = selected
    correction_count = 0
    if (correction_manifest is None) != (correction_root is None):
        errors.append("correction_manifest and correction_root must be supplied together")
    elif correction_manifest is not None and correction_root is not None:
        from scripts.dataset.rtl_generation_asset_corrections import overlay_source_rows

        private_selected, correction_errors, correction_rows = overlay_source_rows(
            selected,
            correction_manifest,
            correction_root,
            expected_correction_version=correction_version or "assetfix_v002",
        )
        errors.extend(correction_errors)
        correction_count = len(correction_rows)
    tasks = [(row, _task_id(row)) for row in selected]
    private_tasks = [(row, _task_id(row)) for row in private_selected]
    task_ids = [task_id for _, task_id in tasks]
    if len(task_ids) != len(set(task_ids)): errors.append("duplicate deterministic task_id")
    batch_count = (len(tasks) + batch_size - 1) // batch_size if tasks else 0
    public_paths = [output_dir / f"batch_{i:03d}.json" for i in range(1, batch_count + 1)]
    private_files = [private_output_dir / "verification_assets.jsonl"]
    for row, task_id in private_tasks:
        private_files.append(private_output_dir / "workspace" / task_id / "reference.sv")
        if row.testbench is not None: private_files.append(private_output_dir / "workspace" / task_id / "testbench.sv")
        private_files.extend(private_output_dir / "workspace" / task_id / "support" / name for name in row.support_files)
    errors.extend(_prepare_public_output(output_dir, public_paths, force))
    errors.extend(_prepare_private_output(private_output_dir, private_files, force))
    for row, _ in private_tasks:
        for support_name in row.support_files:
            if Path(support_name).is_absolute() or ".." in Path(support_name).parts or Path(support_name).name != support_name:
                errors.append(f"unsafe support file path: {support_name}")
        for source_path in (input_path, output_dir, private_output_dir):
            for candidate in private_files + public_paths:
                if candidate.exists() and _samefile(candidate, source_path):
                    errors.append("input/output hard-link alias detected")
    if errors:
        result = {"ok":False,"input":_relative_display(input_path),"output_dir":_relative_display(output_dir),"private_output_dir":_relative_display(private_output_dir),"exported_rows":0,"batch_files":[],"errors":sorted(set(errors)),"warnings":[]}
        return result, 1
    assets = [_asset_record(row, task_id) for row, task_id in private_tasks]
    payloads: list[tuple[Path, dict[str, Any]]] = []
    for index, offset in enumerate(range(0, len(tasks), batch_size), 1):
        batch_tasks = tasks[offset:offset + batch_size]
        public_rows = [_public_row(row, task_id) for row, task_id in batch_tasks]
        source_labels = sorted({row.source_dataset for row, _ in batch_tasks})
        source_label = source_labels[0] if len(source_labels) == 1 else ",".join(source_labels)
        payload = {
            "batch_schema_version": GENERATION_BATCH_SCHEMA_VERSION,
            "created_by": "export_rtl_generation_normalization_batches",
            "source_label": source_label,
            "batch_index": index,
            "batch_count": batch_count,
            "row_count": len(public_rows),
            "start_index": start_index + offset,
            "prompt_template": PROMPT_TEMPLATE_PATH,
            "rows": public_rows,
        }
        leakage = _public_leak_errors(payload, [row for row, _ in batch_tasks], private_output_dir, input_path)
        if leakage:
            return {"ok":False,"input":_relative_display(input_path),"output_dir":_relative_display(output_dir),"private_output_dir":_relative_display(private_output_dir),"exported_rows":0,"batch_files":[],"errors":leakage,"warnings":[]}, 1
        payloads.append((output_dir / f"batch_{index:03d}.json", payload))
    asset_bytes = b"".join(json.dumps(asset,ensure_ascii=False,separators=(",", ":"),sort_keys=True).encode("utf-8") + b"\n" for asset in assets)
    try:
        for path, payload in payloads:
            _atomic_write(path, (json.dumps(payload,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
        _atomic_write(private_output_dir / "verification_assets.jsonl", asset_bytes)
        for row, task_id in private_tasks:
            workspace = private_output_dir / "workspace" / task_id
            _atomic_write(workspace / "reference.sv", _text_bytes(row.reference_rtl))
            if row.testbench is not None: _atomic_write(workspace / "testbench.sv", _text_bytes(row.testbench))
            for name, content in sorted(row.support_files.items()): _atomic_write(workspace / "support" / name, content)
    except (OSError, ValueError) as exc:
        return {"ok":False,"input":_relative_display(input_path),"output_dir":_relative_display(output_dir),"private_output_dir":_relative_display(private_output_dir),"exported_rows":0,"batch_files":[],"errors":[f"could not write outputs: {exc}"],"warnings":[]}, 1
    return {"ok":True,"input":_relative_display(input_path),"output_dir":_relative_display(output_dir),"private_output_dir":_relative_display(private_output_dir),"batch_size":batch_size,"start_index":start_index,"limit":limit,"exported_rows":len(tasks),"batch_files":[_relative_display(path) for path,_ in payloads],"private_assets":_relative_display(private_output_dir/"verification_assets.jsonl"),"readiness_categories":dict(sorted(Counter(_readiness(row)[0] for row,_ in private_tasks).items())),"corrections_applied":correction_count,"public_correction_bytes_excluded":correction_count > 0,"errors":[],"warnings":[]}, 0


def _load_json(path: Path) -> tuple[Any | None, list[str]]:
    if path.is_symlink(): return None, [f"input must not be a symlink: {path}"]
    try: return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: return None, [f"could not read JSON {path}: {exc}"]


def _normalized_rows(
    value: Any,
    *,
    require_response_object: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    if require_response_object:
        if not isinstance(value, dict):
            return [], ["normalized response must be an object with only a top-level rows array"]
        if set(value) != {"rows"}:
            return [], ["normalized response must contain only the top-level rows field"]
        rows = value.get("rows")
    else:
        rows = value if isinstance(value, list) else value.get("rows") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return [], ["normalized input must be an array or object with rows"]
    errors=[]; result=[]
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict): errors.append(f"normalized row {index} must be an object")
        else: result.append(row)
    return result,errors


def _raw_rows(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value,dict) or value.get("batch_schema_version") != GENERATION_BATCH_SCHEMA_VERSION:
        return [], [f"raw batch must use {GENERATION_BATCH_SCHEMA_VERSION}"]
    rows=value.get("rows")
    if not isinstance(rows,list): return [], ["raw batch rows must be an array"]
    return [r for r in rows if isinstance(r,dict)], [f"raw batch row {i} must be an object" for i,r in enumerate(rows,1) if not isinstance(r,dict)]


def _effective_reset_hints(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Use explicit reset language to complete an older incomplete hint.

    Some already-exported public packets recorded ``synchronous: null`` when
    the specification also mentioned synchronous enables alongside an
    explicitly asynchronous reset.  The packet is immutable, so validation
    may complete only that unresolved field from the preserved public
    specification.  Conflicting concrete hint values remain authoritative and
    are still checked normally.
    """
    reset_hints = raw.get("deterministic_reset_hints") or []
    if not isinstance(reset_hints, list):
        return []
    specification = raw.get("raw_specification")
    interface_hints = raw.get("deterministic_interface_hints") or []
    if not isinstance(specification, str) or not isinstance(interface_hints, list):
        return reset_hints
    _, derived_hints = _clock_reset_hints(specification, interface_hints)
    if len(reset_hints) != 1 or len(derived_hints) != 1:
        return reset_hints
    existing = reset_hints[0]
    derived = derived_hints[0]
    if (
        existing.get("signal") == derived.get("signal")
        and existing.get("active_level") == derived.get("active_level")
        and existing.get("synchronous") is None
        and derived.get("synchronous") is not None
    ):
        return derived_hints
    return reset_hints


def _task_shape_errors(task: dict[str, Any], raw: dict[str, Any] | None = None, private_assets: dict[str, dict[str, Any]] | None = None) -> list[str]:
    errors: list[str] = []
    expected = {"schema_version", "task_id", "source_id", "source_dataset", "design_family", "language", "specification", "top_module", "interface", "clocking", "reset", "latency_contract", "behavioral_constraints", "assumptions", "ambiguities", "provenance"}
    errors.extend(f"unknown task field: {key}" for key in sorted(set(task) - expected))
    errors.extend(f"missing task field: {key}" for key in sorted(expected - set(task)))
    if task.get("schema_version") != GENERATION_TASK_SCHEMA_VERSION:
        errors.append("wrong schema version")
    for key in ("task_id", "source_id", "source_dataset", "design_family", "language"):
        if key in task and (not isinstance(task[key], str) or not task[key].strip()):
            errors.append(f"{key} must be a non-empty string")
    if task.get("language") != "systemverilog":
        errors.append("language must be systemverilog")
    if "top_module" in task and task["top_module"] is not None and (not isinstance(task["top_module"], str) or not IDENTIFIER_RE.fullmatch(task["top_module"])):
        errors.append("invalid module name")
    if not isinstance(task.get("specification"), str):
        errors.append("specification must be a string")

    interface = task.get("interface")
    interface_ports: list[dict[str, Any]] = []
    if not isinstance(interface, dict) or set(interface) != {"ports"} or not isinstance(interface.get("ports"), list):
        errors.append("interface must have exactly a ports array")
    else:
        names: set[str] = set()
        for index, port in enumerate(interface["ports"]):
            if not isinstance(port, dict):
                errors.append(f"interface port {index} must be an object")
                continue
            missing = PORT_FIELDS - set(port)
            errors.extend(f"missing interface port field: {key}" for key in sorted(missing))
            errors.extend(f"unknown interface port field: {key}" for key in sorted(set(port) - PORT_FIELDS))
            if not isinstance(port.get("name"), str) or not IDENTIFIER_RE.fullmatch(port.get("name", "")):
                errors.append(f"invalid port name at index {index}")
            elif port["name"] in names:
                errors.append(f"duplicate port name: {port['name']}")
            else:
                names.add(port["name"])
            if port.get("direction") not in {"input", "output", "inout"}:
                errors.append(f"invalid port direction at index {index}")
            if not isinstance(port.get("declaration"), str):
                errors.append(f"port declaration must be a string at index {index}")
            if port.get("packed_range") is not None and not isinstance(port.get("packed_range"), str):
                errors.append(f"packed_range must be string or null at index {index}")
            if port.get("width_bits") is not None and (type(port.get("width_bits")) is not int or port["width_bits"] < 1):
                errors.append(f"invalid width_bits at index {index}")
            if not isinstance(port.get("signed"), bool):
                errors.append(f"signed must be boolean at index {index}")
            if port.get("description") is not None and not isinstance(port.get("description"), str):
                errors.append(f"description must be string or null at index {index}")
            interface_ports.append(port)

    clocking = task.get("clocking")
    if not isinstance(clocking, dict) or set(clocking) != {"clock_signal", "edge"}:
        errors.append("clocking must have exactly clock_signal and edge")
    else:
        if clocking["clock_signal"] is not None and (not isinstance(clocking["clock_signal"], str) or not IDENTIFIER_RE.fullmatch(clocking["clock_signal"])):
            errors.append("invalid clock signal")
        if clocking["edge"] not in {None, "posedge", "negedge"}:
            errors.append("invalid clock edge")

    reset = task.get("reset")
    if not isinstance(reset, dict) or set(reset) != {"signal", "active_level", "synchronous"}:
        errors.append("reset must have exactly signal, active_level, and synchronous")
    else:
        if reset["signal"] is not None and (not isinstance(reset["signal"], str) or not IDENTIFIER_RE.fullmatch(reset["signal"])):
            errors.append("invalid reset signal")
        if reset["active_level"] not in {None, "high", "low"}:
            errors.append("invalid reset active level")
        if reset["synchronous"] is not None and not isinstance(reset["synchronous"], bool):
            errors.append("reset synchronous must be boolean or null")

    port_directions = {port.get("name"): port.get("direction") for port in interface_ports if isinstance(port.get("name"), str)}
    if isinstance(clocking, dict) and clocking.get("clock_signal") is not None and port_directions.get(clocking.get("clock_signal")) not in {"input", "inout"}:
        errors.append("clock signal must name an input or inout port")
    if isinstance(reset, dict) and reset.get("signal") is not None and port_directions.get(reset.get("signal")) not in {"input", "inout"}:
        errors.append("reset signal must name an input or inout port")

    for key in ("behavioral_constraints", "assumptions"):
        if not isinstance(task.get(key), list):
            errors.append(f"{key} must be an array")
        elif any(not isinstance(item, str) for item in task[key]):
            errors.append(f"{key} items must be strings")
    latency = task.get("latency_contract")
    if latency is not None:
        allowed_latency = {"cycles", "min_cycles", "max_cycles", "throughput_cycles", "description"}
        if not isinstance(latency, dict):
            errors.append("latency_contract must be an object or null")
        else:
            errors.extend(f"missing latency field: {key}" for key in sorted(allowed_latency - set(latency)))
            errors.extend(f"unknown latency field: {key}" for key in sorted(set(latency) - allowed_latency))
            for key in allowed_latency - {"description"}:
                if key in latency and latency[key] is not None and (type(latency[key]) is not int or latency[key] < (1 if key == "throughput_cycles" else 0)):
                    errors.append(f"invalid latency field: {key}")
            if "description" in latency and latency["description"] is not None and not isinstance(latency["description"], str):
                errors.append("latency description must be string or null")
            minimum, maximum, cycles = latency.get("min_cycles"), latency.get("max_cycles"), latency.get("cycles")
            if type(minimum) is int and type(maximum) is int and minimum > maximum:
                errors.append("latency min_cycles must not exceed max_cycles")
            if type(cycles) is int and type(minimum) is int and cycles < minimum:
                errors.append("latency cycles conflicts with min_cycles")
            if type(cycles) is int and type(maximum) is int and cycles > maximum:
                errors.append("latency cycles conflicts with max_cycles")

    ambiguities = task.get("ambiguities")
    if not isinstance(ambiguities, list):
        errors.append("ambiguities must be an array")
    else:
        for index, item in enumerate(ambiguities):
            if not isinstance(item, (str, dict)):
                errors.append(f"ambiguity {index} must be a string or object")
            elif isinstance(item, dict):
                allowed = {"topic", "statement", "evidence"}
                errors.extend(f"unknown ambiguity field: {key}" for key in sorted(set(item) - allowed))
                if not isinstance(item.get("statement"), str) or not item["statement"].strip():
                    errors.append(f"ambiguity {index} needs a statement")
                for key in ("topic", "evidence"):
                    if key in item and item[key] is not None and not isinstance(item[key], str):
                        errors.append(f"ambiguity {index}.{key} must be string or null")

    provenance = task.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        errors.extend(f"unknown provenance field: {key}" for key in sorted(set(provenance) - PROVENANCE_FIELDS))
        errors.extend(f"missing provenance field: {key}" for key in sorted(PROVENANCE_FIELDS - set(provenance)))
        for key in ("public_dataset_name", "license", "original_source_id"):
            if key in provenance and (not isinstance(provenance[key], str) or not provenance[key]):
                errors.append(f"provenance.{key} must be non-empty")
        for key in ("public_dataset_url", "source_commit"):
            if key in provenance and provenance[key] is not None and not isinstance(provenance[key], str):
                errors.append(f"provenance.{key} must be string or null")
    if raw is not None:
        for field, expected_value in (("task_id",raw.get("task_id")),("source_id",raw.get("source_id")),("source_dataset",raw.get("source_dataset"))):
            if task.get(field) != expected_value: errors.append(f"changed {field}")
        if isinstance(task.get("provenance"), dict) and task["provenance"].get("license") != raw.get("license"):
            errors.append("changed license")
        if task.get("specification") != raw.get("raw_specification"): errors.append("changed exact specification text")
        if task.get("provenance") != raw.get("provenance"): errors.append("changed provenance")
        hint=raw.get("deterministic_top_module_hint")
        if hint is not None and task.get("top_module") != hint: errors.append("top-module conflict with deterministic source hint")
        hints=raw.get("deterministic_interface_hints")
        ports=interface.get("ports",[]) if isinstance(interface,dict) else []
        if isinstance(hints,list) and hints:
            expected_pairs=[(x.get("name"),x.get("direction")) for x in hints if isinstance(x,dict)]
            actual_pairs=[(x.get("name"),x.get("direction")) for x in ports if isinstance(x,dict)]
            if expected_pairs != actual_pairs: errors.append("ports conflict with deterministic source hints")
            expected_declarations=[x.get("declaration") for x in hints if isinstance(x,dict)]
            actual_declarations=[x.get("declaration") for x in ports if isinstance(x,dict)]
            if expected_declarations != actual_declarations: errors.append("port declarations do not preserve source-facing wording")
            expected_ranges=[x.get("packed_range") for x in hints if isinstance(x,dict)]
            actual_ranges=[x.get("packed_range") for x in ports if isinstance(x,dict)]
            if expected_ranges != actual_ranges: errors.append("packed ranges do not preserve deterministic source hints")
            expected_widths=[x.get("width_bits") for x in hints if isinstance(x,dict)]
            actual_widths=[x.get("width_bits") for x in ports if isinstance(x,dict)]
            if expected_widths != actual_widths: errors.append("port widths do not preserve deterministic source hints")
        clock_hint=raw.get("deterministic_clock_hints") or []
        reset_hint=_effective_reset_hints(raw)
        clock=task.get("clocking") if isinstance(task.get("clocking"),dict) else {}
        reset=task.get("reset") if isinstance(task.get("reset"),dict) else {}
        if not clock_hint and (clock.get("clock_signal") is not None or clock.get("edge") is not None): errors.append("invented clock evidence")
        if not reset_hint and any(reset.get(key) is not None for key in ("signal","active_level","synchronous")):
            errors.append("invented reset evidence")
        if len(reset_hint) == 1:
            expected_reset = reset_hint[0]
            actual_reset = {
                "signal": reset.get("signal"),
                "active_level": reset.get("active_level"),
                "synchronous": reset.get("synchronous"),
            }
            expected_reset = {
                "signal": expected_reset.get("signal"),
                "active_level": expected_reset.get("active_level"),
                "synchronous": expected_reset.get("synchronous"),
            }
            if actual_reset != expected_reset:
                errors.append("reset contract does not preserve deterministic source hints")
        material=[w for w in raw.get("normalization_warnings",[]) if isinstance(w,str) and any(k in w for k in ("missing","ambiguous","conflict"))]
        if material and not ambiguities: errors.append("missing ambiguity record for exporter warning")
    for path, value in _walk_strings(task):
        if path.endswith(".specification") or path.startswith("provenance.") or path.startswith("ambiguities"):
            continue
        if re.search(r"(?:^|[/\\])workspace[/\\]|rtl_generation_verification_assets", value):
            errors.append(f"private workspace path at {path}")
        if FORBIDDEN_CLAIM_RE.search(value): errors.append(f"invented verification claim at {path}")
    for path,key in _walk_keys(task):
        if key.lower() in PRIVATE_FIELD_NAMES: errors.append(f"embedded private field: {path}")
        if key.lower() in {"rtl_teacher_candidate_v0.1","rtl_answer_v0.1","expected_vector","expected_output"}: errors.append(f"embedded answer content: {path}")
    if private_assets:
        record=private_assets.get(str(task.get("task_id")))
        if record is None: errors.append("missing private asset record")
    return sorted(set(errors))


def _load_assets(path: Path) -> tuple[dict[str,dict[str,Any]], list[str]]:
    if path.is_symlink(): return {}, [f"private assets must not be a symlink: {path}"]
    records: dict[str,dict[str,Any]]={}; errors=[]
    try: lines=path.read_text(encoding="utf-8").splitlines()
    except (OSError,UnicodeError) as exc: return {}, [f"could not read private assets: {exc}"]
    for index,line in enumerate(lines,1):
        if not line.strip(): continue
        try: record=json.loads(line)
        except json.JSONDecodeError as exc: errors.append(f"private asset line {index}: malformed JSON: {exc.msg}"); continue
        if not isinstance(record,dict): errors.append(f"private asset line {index}: row must be object"); continue
        if record.get("schema_version") != VERIFICATION_ASSET_SCHEMA_VERSION: errors.append(f"private asset line {index}: wrong schema version")
        task_id=record.get("task_id")
        if not isinstance(task_id,str) or not task_id: errors.append(f"private asset line {index}: missing task_id")
        elif task_id in records: errors.append(f"duplicate private asset task_id: {task_id}")
        else: records[task_id]=record
        errors.extend(f"private asset line {index}: {item}" for item in _asset_shape_errors(record))
        forbidden=PRIVATE_FIELD_NAMES & {str(k).lower() for k in record}
        forbidden.discard("support_files")
        if forbidden: errors.append(f"private asset embeds forbidden content fields: {sorted(forbidden)}")
    return records, errors


def validate_generation_normalized_batch(
    raw_batch_path: Path,
    normalized_path: Path,
    private_assets_path: Path | None = None,
    *,
    require_response_object: bool = False,
) -> tuple[dict[str,Any], int]:
    raw_value, errors=_load_json(raw_batch_path)
    raw_rows, raw_errors=_raw_rows(raw_value)
    errors.extend(raw_errors)
    normalized_value, normalized_errors=_load_json(normalized_path)
    errors.extend(normalized_errors)
    normalized_rows, row_errors=_normalized_rows(
        normalized_value,
        require_response_object=require_response_object,
    )
    errors.extend(row_errors)
    assets=None
    if private_assets_path is not None:
        assets, asset_errors=_load_assets(private_assets_path); errors.extend(asset_errors)
    if len(raw_rows)!=len(normalized_rows): errors.append("missing or extra normalized rows")
    if len({r.get("task_id") for r in normalized_rows}) != len(normalized_rows): errors.append("duplicate normalized task IDs")
    row_errors_all=[]
    for index,(raw,task) in enumerate(zip(raw_rows,normalized_rows),1):
        row_errors_all.extend(f"row {index}: {item}" for item in _task_shape_errors(task,raw,assets))
    errors.extend(row_errors_all)
    report={"ok":not errors,"raw_batch":_relative_display(raw_batch_path),"normalized":_relative_display(normalized_path),"rows":len(normalized_rows),"errors":sorted(set(errors)),"row_errors":row_errors_all,"response_object_required":require_response_object}
    return report, 0 if report["ok"] else 1


def _private_relative_path_errors(value: Any, task_id: str, kind: str) -> list[str]:
    """Validate an asset path as a normalized, task-scoped POSIX path."""
    if not isinstance(value, str) or not value:
        return [f"unsafe asset path: {kind}"]
    errors: list[str] = []
    if "\x00" in value:
        errors.append(f"asset path contains NUL: {kind}")
    if "\\" in value:
        errors.append(f"asset path must use POSIX separators: {kind}")
    if WINDOWS_DRIVE_RE.match(value) or value.startswith("/"):
        errors.append(f"asset path must be relative: {kind}")
    parts = value.split("/")
    if any(not part for part in parts):
        errors.append(f"asset path has an empty component: {kind}")
    if any(part in {".", ".."} for part in parts):
        errors.append(f"asset path has traversal component: {kind}")
    if errors:
        return errors
    if kind in {"reference_rtl_path", "testbench_path"}:
        if len(parts) != 3 or parts[:2] != ["workspace", task_id]:
            errors.append(f"{kind} must be beneath workspace/{task_id}/")
    elif kind == "support_file":
        if len(parts) < 4 or parts[:3] != ["workspace", task_id, "support"]:
            errors.append(f"support path must be beneath workspace/{task_id}/support/")
    elif kind == "support_hash_path":
        if len(parts) < 4 or parts[:3] != ["workspace", task_id, "support"]:
            errors.append(f"support hash path must be beneath workspace/{task_id}/support/")
    return errors


def _hash_value_errors(value: Any, field: str, *, nullable: bool) -> list[str]:
    if value is None and nullable:
        return []
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        return [f"{field} must be 64 lowercase hexadecimal characters" if value is not None else f"missing required hash: {field}"]
    return []


def _asset_shape_errors(asset: dict[str, Any]) -> list[str]:
    errors = [f"unknown asset field: {key}" for key in sorted(set(asset) - ASSET_FIELDS)]
    errors.extend(f"missing asset field: {key}" for key in sorted(ASSET_FIELDS - set(asset)))
    if asset.get("schema_version") != VERIFICATION_ASSET_SCHEMA_VERSION:
        errors.append("wrong asset schema version")
    if asset.get("verification_readiness") not in READINESS_CATEGORIES:
        errors.append("invalid verification readiness")
    for key in ("task_id", "source_id"):
        if not isinstance(asset.get(key), str) or not asset[key]:
            errors.append(f"asset {key} must be non-empty")
    task_id = asset.get("task_id") if isinstance(asset.get("task_id"), str) else ""
    if asset.get("top_module") is not None and (not isinstance(asset["top_module"], str) or not IDENTIFIER_RE.fullmatch(asset["top_module"])):
        errors.append("invalid asset top module")
    reasons = asset.get("readiness_reasons")
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        errors.append("readiness_reasons must be an array of strings")

    for key in ("reference_rtl_path", "testbench_path"):
        value = asset.get(key)
        if value is not None:
            errors.extend(_private_relative_path_errors(value, task_id, key))
    support_files = asset.get("support_files")
    if not isinstance(support_files, list) or any(not isinstance(value, str) for value in support_files):
        errors.append("support_files must be an array of relative strings")
        support_files = []
    if len(support_files) != len(set(support_files)):
        errors.append("support_files must be unique")
    for value in support_files:
        errors.extend(_private_relative_path_errors(value, task_id, "support_file"))

    hashes = asset.get("input_hashes")
    if not isinstance(hashes, dict):
        errors.append("input_hashes must be an object")
        hashes = {}
    else:
        errors.extend(f"unknown input hash field: {key}" for key in sorted(set(hashes) - ASSET_HASH_FIELDS))
        errors.extend(f"missing input hash field: {key}" for key in sorted(ASSET_HASH_FIELDS - set(hashes)))
    errors.extend(_hash_value_errors(hashes.get("source_prompt_sha256"), "source_prompt_sha256", nullable=False))
    for path_key, hash_key in (("reference_rtl_path", "reference_rtl_sha256"), ("testbench_path", "testbench_sha256")):
        path_value, hash_value = asset.get(path_key), hashes.get(hash_key)
        errors.extend(_hash_value_errors(hash_value, hash_key, nullable=path_value is None))
        if path_value is None and hash_value is not None:
            errors.append(f"{hash_key} requires {path_key}")
        if path_value is not None and hash_value is None:
            errors.append(f"{path_key} requires {hash_key}")
    support_hashes = hashes.get("support_files")
    if not isinstance(support_hashes, list):
        errors.append("input_hashes.support_files must be an array")
        support_hashes = []
    seen_hash_paths: set[str] = set()
    hash_path_list: list[str] = []
    for index, item in enumerate(support_hashes):
        if not isinstance(item, dict):
            errors.append(f"support hash {index} must be an object")
            continue
        if set(item) != {"path", "sha256"}:
            errors.append(f"support hash {index} must have exactly path and sha256")
        path_value = item.get("path")
        errors.extend(_private_relative_path_errors(path_value, task_id, "support_hash_path"))
        if isinstance(path_value, str):
            if path_value in seen_hash_paths:
                errors.append(f"duplicate support hash path: {path_value}")
            seen_hash_paths.add(path_value)
            hash_path_list.append(path_value)
        errors.extend(_hash_value_errors(item.get("sha256"), f"support hash {index}.sha256", nullable=False))
    if hash_path_list != support_files:
        errors.append("support hash paths must exactly match support_files")

    if asset.get("verification_readiness") == "executable_ready":
        for path_key, hash_key in (("reference_rtl_path", "reference_rtl_sha256"), ("testbench_path", "testbench_sha256")):
            if asset.get(path_key) is None or hashes.get(hash_key) is None:
                errors.append(f"executable_ready requires {path_key} and {hash_key}")
    return sorted(set(errors))


def _read_private_asset_bytes(private_root: Path, relative: str, label: str) -> tuple[bytes | None, str | None]:
    path = private_root / relative
    if not _is_within(path, private_root) or _path_contains_symlink(path) or path.is_symlink():
        return None, f"{label} escapes the private workspace or is a symlink: {relative}"
    if not path.is_file():
        return None, f"missing or unreadable {label}: {relative}"
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"could not read {label} {relative}: {exc}"


def _verify_asset_integrity(task: dict[str, Any], asset: dict[str, Any], private_root: Path) -> list[str]:
    shape_errors = _asset_shape_errors(asset)
    if shape_errors:
        return shape_errors
    errors: list[str] = []
    if not isinstance(task.get("specification"), str):
        return ["specification must be a string before integrity verification"]
    hashes = asset["input_hashes"]
    expected_prompt_hash = hashes["source_prompt_sha256"]
    actual_prompt_hash = _sha256(_text_bytes(task["specification"]))
    if actual_prompt_hash != expected_prompt_hash:
        errors.append(f"specification hash mismatch for task {task.get('task_id')}")
    for path_key, hash_key, label in (
        ("reference_rtl_path", "reference_rtl_sha256", "reference RTL"),
        ("testbench_path", "testbench_sha256", "testbench"),
    ):
        relative = asset[path_key]
        if relative is None:
            continue
        content, read_error = _read_private_asset_bytes(private_root, relative, label)
        if read_error:
            errors.append(read_error)
        elif _sha256(content or b"") != hashes[hash_key]:
            errors.append(f"{label} hash mismatch for task {task.get('task_id')}")
    for item in hashes["support_files"]:
        content, read_error = _read_private_asset_bytes(private_root, item["path"], "support file")
        if read_error:
            errors.append(read_error)
        elif _sha256(content or b"") != item["sha256"]:
            errors.append(f"support file hash mismatch for task {task.get('task_id')}: {item['path']}")
    return sorted(set(errors))


def _assembled_private_leak_errors(tasks: list[dict[str, Any]], assets: dict[str, dict[str, Any]], private_root: Path) -> list[str]:
    serialized_strings = [value for task in tasks for _, value in _walk_strings(task)]
    errors: list[str] = []
    for asset in assets.values():
        for key in ("reference_rtl_path", "testbench_path"):
            relative = asset.get(key)
            if not isinstance(relative, str):
                continue
            path = private_root / relative
            if not _is_within(path, private_root) or path.is_symlink():
                continue
            try:
                content = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeError):
                continue
            if content and any(content in value for value in serialized_strings):
                errors.append(f"teacher-visible task contains private {key} content")
        for relative in asset.get("support_files", []):
            if not isinstance(relative, str):
                continue
            path = private_root / relative
            if not _is_within(path, private_root) or path.is_symlink():
                continue
            try:
                content = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeError):
                continue
            if content and any(content in value for value in serialized_strings):
                errors.append("teacher-visible task contains private support-file content")
    return sorted(set(errors))


def _managed_jsonl(path: Path, schema: str) -> bool:
    if path.is_symlink() or not path.is_file(): return False
    try: lines=[x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError,UnicodeError): return False
    if not lines:
        return False
    try:
        values=[json.loads(line) for line in lines]
    except json.JSONDecodeError:
        return False
    return all(isinstance(value,dict) and value.get("schema_version")==schema for value in values)


def assemble_generation_inputs(normalized_path: Path, private_assets_path: Path, tasks_output: Path, assets_output: Path, *, force: bool=False) -> tuple[dict[str,Any],int]:
    errors=[]
    normalized_value, load_errors=_load_json(normalized_path); errors.extend(load_errors)
    tasks, task_errors=_normalized_rows(normalized_value); errors.extend(task_errors)
    asset_records, asset_errors=_load_assets(private_assets_path); errors.extend(asset_errors)
    if len({str(t.get("task_id")) for t in tasks}) != len(tasks):
        errors.append("duplicate task IDs")
    task_ids={str(t.get("task_id")) for t in tasks}
    asset_ids=set(asset_records)
    missing_ids = task_ids - asset_ids
    unused_private_asset_count = len(asset_ids - task_ids)
    if missing_ids:
        errors.append(f"missing private asset records for normalized task IDs: {sorted(missing_ids)}")
    selected_assets = {task_id: asset_records[task_id] for task_id in task_ids if task_id in asset_records}
    private_root=private_assets_path.parent.resolve()
    for task in tasks:
        errors.extend(f"task {task.get('task_id')}: {item}" for item in _task_shape_errors(task, None, asset_records))
        asset=selected_assets.get(str(task.get("task_id")))
        if not asset:
            continue
        errors.extend(f"asset {asset.get('task_id')}: {item}" for item in _asset_shape_errors(asset))
        if asset.get("source_id") != task.get("source_id"):
            errors.append(f"source ID conflict for task {task.get('task_id')}")
        if asset.get("top_module") is not None and task.get("top_module") is not None and asset.get("top_module") != task.get("top_module"):
            errors.append(f"top module conflict for task {task.get('task_id')}")
        errors.extend(f"task {task.get('task_id')}: {item}" for item in _verify_asset_integrity(task, asset, private_root))
    errors.extend(_assembled_private_leak_errors(tasks, selected_assets, private_root))
    for output in (tasks_output,assets_output):
        if output.is_symlink(): errors.append(f"output must not be a symlink: {output}")
        if output.exists() and not force: errors.append(f"output already exists: {output}; use --force")
        if output.exists() and _is_hardlinked(output): errors.append(f"output must not be a hard-link alias: {output}")
    for output in (tasks_output, assets_output):
        for source in (normalized_path, private_assets_path):
            if output.resolve() == source.resolve() or _samefile(output, source): errors.append("input/output alias collision")
    if _samefile(tasks_output,assets_output) or tasks_output.resolve()==assets_output.resolve(): errors.append("output alias collision")
    if tasks_output.exists() and force and not _managed_jsonl(tasks_output,GENERATION_TASK_SCHEMA_VERSION): errors.append("refusing to replace unknown task output")
    if assets_output.exists() and force and not _managed_jsonl(assets_output,VERIFICATION_ASSET_SCHEMA_VERSION): errors.append("refusing to replace unknown asset output")
    if errors:
        return {"ok":False,"tasks_output":_relative_display(tasks_output),"assets_output":_relative_display(assets_output),"rows":len(tasks),"unused_private_asset_count":unused_private_asset_count,"errors":sorted(set(errors))},1
    # Normalized response order is part of the packet contract. Preserve it
    # through assembly so a five-task exchange can be audited positionally.
    ordered_task_ids = [str(task.get("task_id")) for task in tasks]
    task_bytes=b"".join(json.dumps(t,ensure_ascii=False,separators=(",", ":"),sort_keys=True).encode()+b"\n" for t in tasks)
    asset_bytes=b"".join(json.dumps(selected_assets[t],ensure_ascii=False,separators=(",", ":"),sort_keys=True).encode()+b"\n" for t in ordered_task_ids)
    try:
        _atomic_write(tasks_output,task_bytes); _atomic_write(assets_output,asset_bytes)
    except OSError as exc:
        return {"ok":False,"tasks_output":_relative_display(tasks_output),"assets_output":_relative_display(assets_output),"rows":len(tasks),"unused_private_asset_count":unused_private_asset_count,"errors":[f"could not atomically write outputs: {exc}"]},1
    warnings = [f"{unused_private_asset_count} private asset records were not selected for this normalized batch"] if unused_private_asset_count else []
    return {"ok":True,"tasks_output":_relative_display(tasks_output),"assets_output":_relative_display(assets_output),"rows":len(tasks),"selected_private_asset_count":len(selected_assets),"unused_private_asset_count":unused_private_asset_count,"errors":[],"warnings":warnings},0


def _median_stats(values: list[int]) -> dict[str, int | float | None]:
    return {"maximum": max(values) if values else None, "median": median(values) if values else None}


def _repository_path_state(path: Path) -> str:
    """Classify a path from fixed repository metadata, never from its contents."""
    resolved = path.resolve()
    if ".local_data" in resolved.parts:
        return "ignored_local"
    repo_root = Path(__file__).resolve().parents[2]
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError:
        return "untracked_or_unknown"
    relative_text = relative.as_posix()
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", relative_text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if tracked.returncode == 0:
        return "tracked"
    ignored = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-q", "--", relative_text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return "ignored_local" if ignored.returncode == 0 else "untracked_or_unknown"


def audit_source_rows(input_path: Path) -> dict[str, Any]:
    rows, discovery_errors=discover_source_rows(input_path)
    categories=Counter(); families=Counter(); prompt_sizes=[]; rtl_sizes=[]; tb_sizes=[]; samples=[]
    for row in sorted(rows,key=lambda x:x.source_id):
        readiness,_=_readiness(row); categories[readiness]+=1; families[row.design_family]+=1
        if row.specification: prompt_sizes.append(len(row.specification.encode()))
        if row.reference_rtl: rtl_sizes.append(len(row.reference_rtl.encode()))
        if row.testbench: tb_sizes.append(len(row.testbench.encode()))
        if len(samples)<5:
            samples.append({"source_id":row.source_id,"dataset_name":row.source_dataset,"design_family":row.design_family,"top_module":row.top_module_hint,"port_names":[p["name"] for p in row.interface_hints],"presence_flags":{"specification":bool(row.specification),"reference_rtl":bool(row.reference_rtl),"testbench":bool(row.testbench),"support_files":bool(row.support_files)},"content_hashes":{"source_prompt_sha256":_sha256(_text_bytes(row.specification)),"reference_rtl_sha256":_sha256(_text_bytes(row.reference_rtl)),"testbench_sha256":_sha256(_text_bytes(row.testbench))},"content_sizes":{"prompt_bytes":len(_text_bytes(row.specification)),"rtl_bytes":len(_text_bytes(row.reference_rtl)),"testbench_bytes":len(_text_bytes(row.testbench))},"readiness_category":readiness})
    ids=[row.source_id for row in rows]
    duplicate_count=sum(count-1 for count in Counter(ids).values() if count>1)
    ready=[row for row in sorted(rows,key=lambda x:x.source_id) if _readiness(row)[0]=="executable_ready"]
    smoke=[]
    for label, predicate in (("combinational", lambda row: not row.clock_hints and not row.reset_hints), ("sequential", lambda row: bool(row.clock_hints)), ("counter_or_fsm", lambda row: row.design_family in {"counter","fsm","fifo","handshake"})):
        for row in ready:
            if len(smoke) >= 5 or not predicate(row) or any(item["source_id"] == row.source_id for item in smoke):
                continue
            smoke.append({"source_id":row.source_id,"dataset_name":row.source_dataset,"design_family":label,"top_module":row.top_module_hint,"port_names":[p["name"] for p in row.interface_hints],"presence_flags":{"specification":bool(row.specification),"reference_rtl":bool(row.reference_rtl),"testbench":bool(row.testbench),"support_files":bool(row.support_files)},"content_hashes":{"source_prompt_sha256":_sha256(_text_bytes(row.specification)),"reference_rtl_sha256":_sha256(_text_bytes(row.reference_rtl)),"testbench_sha256":_sha256(_text_bytes(row.testbench))},"content_sizes":{"prompt_bytes":len(_text_bytes(row.specification)),"rtl_bytes":len(_text_bytes(row.reference_rtl)),"testbench_bytes":len(_text_bytes(row.testbench))},"readiness_category":"executable_ready"})
            if len([item for item in smoke if item["design_family"] == label]) >= (2 if label in {"combinational","sequential"} else 1):
                break
    canonical = _relative_display(input_path)
    return {"report_schema_version":AUDIT_SCHEMA_VERSION,"input":canonical,"file_format":"local VerilogEval or normalized task input","tracked_or_ignored":_repository_path_state(input_path),"total_rows":len(rows),"unique_source_ids":len(set(ids)),"duplicate_source_id_count":duplicate_count,"rows_with_nonempty_prompt_or_specification":sum(bool(r.specification) for r in rows),"rows_with_reference_rtl":sum(bool(r.reference_rtl) for r in rows),"rows_with_testbench":sum(bool(r.testbench) for r in rows),"rows_with_support_files":sum(bool(r.support_files) for r in rows),"rows_with_one_detected_module":sum(len(module_names(r.reference_rtl or ""))==1 for r in rows),"rows_with_deterministic_top_module":sum(bool(r.top_module_hint) for r in rows),"rows_with_deterministic_interface":sum(bool(r.interface_hints) for r in rows),"rows_with_confirmed_license_metadata":sum(_license_is_usable(r.license) for r in rows),"rows_with_placeholder_or_missing_license":sum(not _license_is_usable(r.license) for r in rows),"rows_appearing_executable_ready":categories["executable_ready"],"rows_needing_testbench":categories["needs_testbench"],"rows_structural_only":categories["structural_only"],"rows_unsuitable":categories["invalid"]+categories["license_blocked"],"readiness_categories":dict(sorted(categories.items())),"design_family_distribution":dict(sorted(families.items())),"size_bytes":{"prompt_or_specification":_median_stats(prompt_sizes),"reference_rtl":_median_stats(rtl_sizes),"testbench":_median_stats(tb_sizes)},"symlinked_inputs":_safe_source_tree(input_path),"unsafe_paths":[],"source_prompt_text_preserved_exactly":True,"reference_rtl_and_testbench_embedded_in_normalized_rows":input_path.is_file(),"samples":samples,"smoke_subset":smoke,"recommended_canonical_v0_1_input":{"path":canonical,"reason":"local input with usable specifications and deterministic private verification assets"},"errors":discovery_errors}


def write_audit_reports(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    _atomic_write(json_path,(json.dumps(report,ensure_ascii=False,indent=2)+"\n").encode())
    lines=["# RTL generation source audit v0.1","","Metadata-only audit. Raw RTL, testbench contents, full prompts, credentials, endpoint data, and absolute paths are not included.","",f"- Input: `{report.get('input')}`",f"- Rows: {report.get('total_rows')}",f"- Readiness: `{json.dumps(report.get('readiness_categories',{}),sort_keys=True)}`","", "## Counts", "", "| Metric | Count |", "|---|---:|"]
    for key in ("unique_source_ids","duplicate_source_id_count","rows_with_nonempty_prompt_or_specification","rows_with_reference_rtl","rows_with_testbench","rows_with_support_files","rows_with_one_detected_module","rows_with_deterministic_top_module","rows_with_deterministic_interface","rows_with_confirmed_license_metadata","rows_with_placeholder_or_missing_license","rows_appearing_executable_ready","rows_needing_testbench","rows_structural_only","rows_unsuitable"):
        lines.append(f"| {key} | {report.get(key)} |")
    lines += ["","## Recommended input","",f"- `{report.get('recommended_canonical_v0_1_input',{}).get('path')}`",f"- {report.get('recommended_canonical_v0_1_input',{}).get('reason')}","","## Design families","",f"`{json.dumps(report.get('design_family_distribution',{}),sort_keys=True)}`","","## Smoke subset",""]
    for sample in report.get("smoke_subset",[]): lines.append(f"- `{sample['source_id']}`: `{sample['design_family']}`, top `{sample['top_module']}`, ports `{', '.join(sample['port_names'])}`")
    lines += ["","## Samples",""]
    for sample in report.get("samples",[]): lines.append(f"- `{sample['source_id']}`: `{sample['readiness_category']}`, top `{sample['top_module']}`, ports `{', '.join(sample['port_names'])}`")
    lines += ["","## Limitations","", "- Presence of reference RTL is not a correctness claim.", "- No source data was executed."]
    _atomic_write(markdown_path,("\n".join(lines)+"\n").encode())


__all__=[
    "AUDIT_SCHEMA_VERSION","GENERATION_BATCH_SCHEMA_VERSION","GENERATION_TASK_SCHEMA_VERSION","VERIFICATION_ASSET_SCHEMA_VERSION","TEACHER_CANDIDATE_SCHEMA_VERSION","SourceRow","assemble_generation_inputs","audit_source_rows","discover_source_rows","export_generation_normalization_batches","validate_generation_normalized_batch","write_audit_reports",
]
