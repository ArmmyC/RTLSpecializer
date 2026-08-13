"""Trusted, versioned correction overlays for RTL verification assets.

The correction layer owns only replacement testbench metadata and bytes.  It
never changes the staged upstream checkout, the public normalization payload,
or the frozen split.  Correction validation is structural and metadata-only;
RTL and testbench execution remains an operator-controlled later step.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from scripts.dataset.rtl_generation_preparation import (
    SourceRow,
    _is_hardlinked,
    _is_within,
    _path_contains_symlink,
    _readiness,
    _relative_display,
    _sha256,
    _sv_declaration_count,
    _sv_declarations,
    _sv_probable_instantiations,
    _sv_tokens,
    _text_bytes,
    _verification_dependency_report,
    discover_source_rows,
)


CORRECTION_VERSION = "assetfix_v002"
CORRECTION_ROW_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.1"
CORRECTION_MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_v0.1"
CORRECTION_SELECTION_SCHEMA_VERSION = "rtl_verification_asset_correction_selection_v0.1"
CORRECTION_VALIDATION_SCHEMA_VERSION = "rtl_verification_asset_correction_validation_v0.1"
V003_MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_v0.2"
V003_ROW_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.2"
EXTENDED_ROW_CORRECTION_VERSIONS = frozenset({
    "assetfix_v003",
    "assetfix_v005",
    "assetfix_v006",
    "assetfix_v006_retry_02",
    "assetfix_v007",
    "assetfix_v008",
    "assetfix_v008_retry_01",
    "assetfix_v009",
})

EXTENDED_ROW_FIELDS = {
    "schema_version", "source_dataset", "source_id", "task_id", "split",
    "design_family", "top_module", "upstream_commit", "original_prompt_sha256",
    "original_reference_rtl_sha256", "original_testbench_sha256",
    "corrected_testbench_sha256", "correction_version", "correction_reason",
    "authoring_method", "reference_modified", "reference_copied_to_support",
    "testbench_path", "support_files", "dependency_closure",
    "verification_readiness", "qualification_status", "mutation_contracts",
    "static_audit", "frozen_split_sha256", "source_tree_sha256",
    "public_specification_sha256", "selection_ids_sha256", "selection_report_sha256",
    "qualification_evidence_sha256", "qualification_freeze_sha256",
    "qualification_report_sha256", "qualification_result",
    "qualification_runner_sidecar_sha256",
}

EXPECTED_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)

SELECTION_ROLES = {
    "Prob001_zero": "constant_or_direct_combinational",
    "Prob020_mt2015_eq2": "small_relational_combinational",
    "Prob071_always_casez": "decoder_like_priority_logic",
    "Prob048_m2014_q4c": "simple_sequential_register",
    "Prob079_fsm3onehot": "small_fsm_combinational_logic",
}

CORRECTION_ROW_FIELDS = {
    "schema_version",
    "source_dataset",
    "source_id",
    "task_id",
    "split",
    "design_family",
    "top_module",
    "upstream_commit",
    "original_prompt_sha256",
    "original_reference_rtl_sha256",
    "original_testbench_sha256",
    "corrected_testbench_sha256",
    "correction_version",
    "correction_reason",
    "authoring_method",
    "reference_modified",
    "reference_copied_to_support",
    "testbench_path",
    "support_files",
    "dependency_closure",
    "verification_readiness",
    "negative_mutation_validation",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_CANONICAL_MISMATCH_FORMAT_RE = re.compile(
    r"(?i)Mismatches[ \t]*:[ \t]*%[0-9]*d\b"
)
_PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "reference_rtl",
)
_NONDETERMINISTIC_MARKERS = (
    "$random",
    "$urandom",
    "$time",
    "$realtime",
    "$fopen",
    "$readmem",
    "$writemem",
    "$system",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {index} must be an object")
        rows.append(value)
    return rows


def _write_new(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing correction output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _safe_relative_path(root: Path, relative: Any, *, require_suffix: str | None = None) -> tuple[Path | None, str | None]:
    if not isinstance(relative, str) or not relative:
        return None, "correction path must be a non-empty relative string"
    candidate = Path(relative)
    if candidate.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in candidate.parts):
        return None, f"unsafe correction path: {relative!r}"
    if require_suffix is not None and candidate.suffix.casefold() != require_suffix.casefold():
        return None, f"correction path must end with {require_suffix}: {relative!r}"
    full = root / candidate
    if not _is_within(full, root) or _path_contains_symlink(full) or full.is_symlink():
        return None, f"correction path escapes root or contains a symlink: {relative!r}"
    if not full.is_file():
        return None, f"correction file is missing: {relative!r}"
    if _is_hardlinked(full):
        return None, f"correction file must not be a hard-link alias: {relative!r}"
    return full, None


def _selection_rows(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    try:
        value = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, [], [str(exc)]
    if not isinstance(value, dict):
        return None, [], ["correction selection must be an object"]
    errors: list[str] = []
    if value.get("schema_version") != CORRECTION_SELECTION_SCHEMA_VERSION:
        errors.append("wrong correction selection schema version")
    rows = value.get("rows")
    if not isinstance(rows, list):
        errors.append("correction selection rows must be an array")
        rows = []
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if len(clean_rows) != len(rows):
        errors.append("correction selection rows must contain objects")
    return value, clean_rows, errors


def _selection_roles(source_id: str) -> str:
    return SELECTION_ROLES.get(source_id, "bounded_smoke_correction")


def select_correction_rows(
    inventory_path: Path,
    split_path: Path,
    output_path: Path,
    *,
    source_ids: Iterable[str] = EXPECTED_SOURCE_IDS,
) -> tuple[dict[str, Any], int]:
    from scripts.dataset.rtl_generation_inventory import (
        _load_inventory,
        validate_generation_split,
    )

    errors: list[str] = []
    requested = list(source_ids)
    if len(requested) != 5:
        errors.append("correction selection requires exactly five source IDs")
    if len(requested) != len(set(requested)):
        errors.append("correction selection contains duplicate source IDs")
    try:
        inventory = _load_inventory(inventory_path)
        split_report, split_code = validate_generation_split(inventory_path, split_path, expected_seed=7)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    if split_code:
        errors.extend(split_report.get("errors", []))
    by_id = {row["source_id"]: row for row in inventory}
    split_value = _load_json(split_path)
    train_ids = set(split_value.get("splits", {}).get("train", []))
    missing = sorted(set(requested) - set(by_id))
    outside = sorted(set(requested) - train_ids)
    if missing:
        errors.append(f"correction IDs missing from inventory: {missing}")
    if outside:
        errors.append(f"correction IDs outside train split: {outside}")
    rows: list[dict[str, Any]] = []
    for source_id in requested:
        row = by_id.get(source_id)
        if row is None:
            continue
        if row.get("verification_readiness") != "needs_testbench":
            errors.append(f"{source_id} is not currently needs_testbench")
        if not any("reference material" in str(reason) for reason in row.get("readiness_reasons", [])):
            errors.append(f"{source_id} is not blocked by a reference-only dependency")
        rows.append({
            "source_id": source_id,
            "task_id": row.get("task_id"),
            "split": "train",
            "current_readiness": row.get("verification_readiness"),
            "blocking_reason": "reference_only_dependency",
            "selection_reason": "bounded smoke correction",
            "selection_role": _selection_roles(source_id),
            "design_family": row.get("design_family"),
            "behavior_categories": row.get("behavior_categories", []),
        })
    report = {
        "schema_version": CORRECTION_SELECTION_SCHEMA_VERSION,
        "source_dataset": "VerilogEval",
        "source_commit": inventory[0].get("source_commit") if inventory else None,
        "base_inventory_sha256": sha256_file(inventory_path),
        "base_split_sha256": sha256_file(split_path),
        "correction_version": CORRECTION_VERSION,
        "requested_count": 5,
        "selected_count": len(rows),
        "selection_algorithm": "explicit_public_spec_smoke_allowlist_v1",
        "rows": rows,
        "errors": sorted(set(errors)),
    }
    if errors or len(rows) != 5:
        return report, 1
    _write_new(output_path, (json.dumps({**report, "ok": True}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**report, "ok": True, "output": _relative_display(output_path)}, 0


def load_correction_manifest(
    path: Path,
    correction_root: Path,
    *,
    expected_correction_version: str = CORRECTION_VERSION,
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        rows = _load_jsonl(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [], [str(exc)]
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        is_extended = (
            expected_correction_version in EXTENDED_ROW_CORRECTION_VERSIONS
            and row.get("schema_version") == V003_ROW_SCHEMA_VERSION
        )
        allowed_fields = (
            EXTENDED_ROW_FIELDS if is_extended else CORRECTION_ROW_FIELDS
        )
        required_fields = (
            {
                "schema_version", "source_dataset", "source_id", "task_id", "split",
                "top_module", "upstream_commit", "original_prompt_sha256",
                "original_reference_rtl_sha256", "original_testbench_sha256",
                "corrected_testbench_sha256", "correction_version", "reference_modified",
                "reference_copied_to_support", "testbench_path", "support_files",
                "dependency_closure", "verification_readiness",
            }
            if is_extended else CORRECTION_ROW_FIELDS
        )
        unknown = sorted(set(row) - allowed_fields)
        missing = sorted(required_fields - set(row))
        errors.extend(f"manifest row {index}: unknown field {key}" for key in unknown)
        errors.extend(f"manifest row {index}: missing field {key}" for key in missing)
        expected_schema = V003_ROW_SCHEMA_VERSION if is_extended else CORRECTION_ROW_SCHEMA_VERSION
        if row.get("schema_version") != expected_schema:
            errors.append(f"manifest row {index}: wrong schema version")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"manifest row {index}: invalid source_id")
        elif source_id in seen:
            errors.append(f"manifest row {index}: duplicate source_id {source_id}")
        else:
            seen.add(source_id)
        if row.get("source_dataset") != "VerilogEval":
            errors.append(f"manifest row {index}: source_dataset must remain VerilogEval")
        if row.get("split") != "train":
            errors.append(f"manifest row {index}: split must be train")
        if row.get("correction_version") != expected_correction_version:
            errors.append(f"manifest row {index}: wrong correction version")
        if not _COMMIT_RE.fullmatch(str(row.get("upstream_commit") or "")):
            errors.append(f"manifest row {index}: invalid upstream commit")
        for field in (
            "original_prompt_sha256",
            "original_reference_rtl_sha256",
            "original_testbench_sha256",
            "corrected_testbench_sha256",
        ):
            if not _SHA256_RE.fullmatch(str(row.get(field) or "")):
                errors.append(f"manifest row {index}: invalid {field}")
        if row.get("reference_modified") is not False:
            errors.append(f"manifest row {index}: reference_modified must be false")
        if row.get("reference_copied_to_support") is not False:
            errors.append(f"manifest row {index}: reference_copied_to_support must be false")
        if row.get("support_files") != []:
            errors.append(f"manifest row {index}: support_files must be empty")
        if row.get("dependency_closure") != "passed":
            errors.append(f"manifest row {index}: dependency closure did not pass")
        if row.get("verification_readiness") != "executable_ready":
            errors.append(f"manifest row {index}: verification readiness is not executable_ready")
        if expected_correction_version == "assetfix_v005":
            if row.get("qualification_status") != "qualified":
                errors.append(f"manifest row {index}: v005 overlay row is not qualified")
            if row.get("qualification_result") not in {None, "passed"}:
                errors.append(f"manifest row {index}: v005 qualification result is not passed")
        path, path_error = _safe_relative_path(correction_root, row.get("testbench_path"), require_suffix=".sv")
        if path_error:
            errors.append(f"manifest row {index}: {path_error}")
    return rows, sorted(set(errors))


def _read_correction_text(row: dict[str, Any], correction_root: Path) -> tuple[str | None, bytes | None, list[str]]:
    path, path_error = _safe_relative_path(correction_root, row.get("testbench_path"), require_suffix=".sv")
    if path_error:
        return None, None, [path_error]
    assert path is not None
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return None, None, [f"could not read corrected testbench: {exc}"]
    return text, content, []


def _top_instantiation_port_names(text: str, top_module: str) -> tuple[set[str], int]:
    pattern = re.compile(
        rf"\b{re.escape(top_module)}\b\s+(?:[A-Za-z_]\w*)\s*\(",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        return set(), len(matches)
    start = matches[0].end()
    depth = 1
    index = start
    while index < len(text) and depth:
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
        index += 1
    if depth:
        return set(), len(matches)
    body = text[start:index - 1]
    return set(re.findall(r"\.\s*([A-Za-z_]\w*)\s*\(", body)), len(matches)


def _mutation_contract(source_id: str) -> dict[str, Any]:
    cases: dict[str, list[dict[str, Any]]] = {
        "Prob001_zero": [
            {"name": "constant_one", "witness": "zero=0, mutant=1", "distinguishing_case_count": 1},
        ],
        "Prob020_mt2015_eq2": [
            {"name": "constant_zero", "witness": "A=0,B=0, expected=1", "distinguishing_case_count": 4},
            {"name": "inverted_equality", "witness": "A=0,B=1, expected=0", "distinguishing_case_count": 16},
        ],
        "Prob071_always_casez": [
            {"name": "constant_zero", "witness": "in=8'b00000010, expected=1", "distinguishing_case_count": 7},
            {"name": "most_significant_first", "witness": "in=8'b10000010, expected=1", "distinguishing_case_count": 1},
        ],
        "Prob048_m2014_q4c": [
            {"name": "reset_ignored", "witness": "r=1,d=1 at posedge, expected q=0", "distinguishing_case_count": 1},
            {"name": "one_cycle_latency", "witness": "d changes before a later posedge", "distinguishing_case_count": 1},
            {"name": "stuck_state", "witness": "d=1 after reset, expected q=1", "distinguishing_case_count": 1},
        ],
        "Prob079_fsm3onehot": [
            {"name": "constant_zero_next_state", "witness": "state=A,in=1, expected next_state=B", "distinguishing_case_count": 1},
            {"name": "stuck_output", "witness": "state=D, expected out=1", "distinguishing_case_count": 1},
        ],
    }
    values = cases.get(source_id, [])
    return {
        "schema_version": "rtl_correction_negative_mutation_contract_v0.1",
        "validated_offline": all(int(item["distinguishing_case_count"]) > 0 for item in values),
        "execution_deferred": True,
        "oracle_basis": "public_specification_only",
        "cases": values,
    }


def _structural_errors(row: SourceRow, text: str) -> list[str]:
    errors: list[str] = []
    lowered = text.casefold()
    for marker in _PRIVATE_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"private marker in corrected testbench: {marker}")
    for marker in _NONDETERMINISTIC_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"nondeterministic or host-dependent construct: {marker}")
    if "`include" in lowered:
        errors.append("corrected testbench must not include files")
    if re.search(r"\brefmodule\b", text, re.IGNORECASE):
        errors.append("corrected testbench must not mention RefModule")
    tokens, token_ambiguous = _sv_tokens(text)
    declarations = _sv_declarations(tokens)
    dependency = _verification_dependency_report(replace(row, testbench=text, support_files={}))
    if token_ambiguous or dependency.ambiguous:
        errors.append("corrected testbench dependency analysis is ambiguous")
    if _sv_declaration_count(tokens, "module", "tb") != 1:
        errors.append("corrected testbench must declare exactly one tb module")
    if declarations["module"] != {"tb"}:
        errors.append("corrected testbench may declare only tb")
    if declarations["package"] or declarations["interface"]:
        errors.append("corrected testbench must not declare packages or interfaces")
    if dependency.unresolved_modules:
        errors.append(f"unresolved corrected testbench modules: {sorted(dependency.unresolved_modules)}")
    if dependency.unresolved_packages:
        errors.append(f"unresolved corrected testbench packages: {sorted(dependency.unresolved_packages)}")
    if dependency.probable_instantiations.count(row.top_module_hint or "") != 1:
        errors.append("corrected testbench must instantiate TopModule exactly once")
    if dependency.probable_instantiations != (row.top_module_hint,):
        errors.append("corrected testbench has an unexpected module instantiation")
    port_names, instance_count = _top_instantiation_port_names(text, row.top_module_hint or "TopModule")
    expected_ports = {str(port.get("name")) for port in row.interface_hints}
    if instance_count != 1 or port_names != expected_ports:
        errors.append("TopModule named port connections do not match the public interface")
    canonical_results = _CANONICAL_MISMATCH_FORMAT_RE.findall(text)
    if len(canonical_results) != 1:
        errors.append(
            "corrected testbench must emit exactly one canonical "
            "Mismatches: <integer> result"
        )
    if "mismatch_count_v1" in lowered:
        errors.append(
            "corrected testbench must not use the legacy "
            "mismatch_count_v1 output label"
        )
    return sorted(set(errors))


def overlay_source_rows(
    rows: list[SourceRow],
    correction_manifest_path: Path,
    correction_root: Path,
    *,
    expected_correction_version: str = CORRECTION_VERSION,
) -> tuple[list[SourceRow], list[str], dict[str, dict[str, Any]]]:
    manifest_rows, errors = load_correction_manifest(
        correction_manifest_path,
        correction_root,
        expected_correction_version=expected_correction_version,
    )
    by_id = {row["source_id"]: row for row in manifest_rows}
    result: list[SourceRow] = []
    for row in rows:
        correction = by_id.get(row.source_id)
        if correction is None:
            result.append(row)
            continue
        text, content, read_errors = _read_correction_text(correction, correction_root)
        errors.extend(f"{row.source_id}: {error}" for error in read_errors)
        if text is None or content is None:
            result.append(row)
            continue
        if _sha256(content) != correction.get("corrected_testbench_sha256"):
            errors.append(f"{row.source_id}: corrected testbench hash mismatch")
        # The correction manifest is authoritative for the private asset
        # closure.  A corrected row declaring no support files must also clear
        # any source-adapter metadata file (for example a public *_ifc.txt
        # companion) that discovery attached to the raw row.  Leaving it on
        # the overlaid SourceRow would copy it into the private asset view and
        # can make the public-leak detector mistake public interface text for
        # leaked support content.
        result.append(replace(row, testbench=text, support_files={}))
    unknown = sorted(set(by_id) - {row.source_id for row in rows})
    errors.extend(f"correction manifest source ID is absent from source rows: {source_id}" for source_id in unknown)
    return result, sorted(set(errors)), by_id


def create_correction_manifest(
    inventory_path: Path,
    selection_path: Path,
    correction_root: Path,
    output_path: Path,
    *,
    source_commit: str,
) -> tuple[dict[str, Any], int]:
    from scripts.dataset.rtl_generation_inventory import _load_inventory

    errors: list[str] = []
    try:
        inventory = _load_inventory(inventory_path)
        selection, selection_rows, selection_errors = _selection_rows(selection_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    errors.extend(selection_errors)
    if selection is None:
        return {"ok": False, "errors": sorted(set(errors))}, 1
    if selection.get("base_inventory_sha256") != sha256_file(inventory_path):
        errors.append("selection report base inventory hash mismatch")
    selected_ids = [row.get("source_id") for row in selection_rows]
    if len(selected_ids) != 5 or len(set(selected_ids)) != 5:
        errors.append("selection report must contain five unique source IDs")
    by_id = {row["source_id"]: row for row in inventory}
    manifest_rows: list[dict[str, Any]] = []
    for selected in selection_rows:
        source_id = selected.get("source_id")
        source = by_id.get(source_id)
        if source is None:
            errors.append(f"selected source ID is absent from inventory: {source_id}")
            continue
        path, path_error = _safe_relative_path(correction_root, f"tasks/{source_id}/testbench.sv", require_suffix=".sv")
        if path_error:
            errors.append(f"{source_id}: {path_error}")
            continue
        assert path is not None
        try:
            content = path.read_bytes()
        except OSError as exc:
            errors.append(f"{source_id}: could not read corrected testbench: {exc}")
            continue
        try:
            content.decode("utf-8")
        except UnicodeError as exc:
            errors.append(f"{source_id}: corrected testbench is not UTF-8: {exc}")
            continue
        manifest_rows.append({
            "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
            "source_dataset": source.get("source_dataset") or "VerilogEval",
            "source_id": source_id,
            "task_id": source.get("task_id"),
            "split": "train",
            "design_family": source.get("design_family"),
            "top_module": source.get("top_module"),
            "upstream_commit": source_commit,
            "original_prompt_sha256": source.get("source_prompt_sha256"),
            "original_reference_rtl_sha256": source.get("reference_rtl_sha256"),
            "original_testbench_sha256": source.get("testbench_sha256"),
            "corrected_testbench_sha256": _sha256(content),
            "correction_version": CORRECTION_VERSION,
            "correction_reason": "replace reference-only checker with public-spec-derived standalone testbench",
            "authoring_method": "trusted_manual_public_spec",
            "reference_modified": False,
            "reference_copied_to_support": False,
            "testbench_path": f"tasks/{source_id}/testbench.sv",
            "support_files": [],
            # These fields are claims in the manifest and are independently
            # re-established by validate_rtl_generation_asset_corrections.py.
            "dependency_closure": "passed",
            "verification_readiness": "executable_ready",
            "negative_mutation_validation": _mutation_contract(source_id),
        })
    if errors:
        return {"ok": False, "rows": manifest_rows, "errors": sorted(set(errors))}, 1
    _write_new(
        output_path,
        b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in manifest_rows),
    )
    return {
        "ok": True,
        "manifest": _relative_display(output_path),
        "row_count": len(manifest_rows),
        "correction_manifest_sha256": sha256_file(output_path),
        "errors": [],
    }, 0


def validate_correction_rows(
    source_rows: list[SourceRow],
    correction_rows: list[dict[str, Any]],
    correction_root: Path,
    *,
    source_commit: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_source = {row.source_id: row for row in source_rows}
    errors: list[str] = []
    validated: list[dict[str, Any]] = []
    for index, correction in enumerate(correction_rows, 1):
        source_id = correction.get("source_id")
        source = by_source.get(source_id)
        if source is None:
            errors.append(f"manifest row {index}: source ID absent from source tree: {source_id}")
            continue
        if correction.get("upstream_commit") != source_commit:
            errors.append(f"{source_id}: upstream commit mismatch")
        if correction.get("task_id") != _task_id_from_source(source, source_commit):
            errors.append(f"{source_id}: task ID changed")
        if correction.get("source_dataset") != source.source_dataset:
            errors.append(f"{source_id}: source dataset changed")
        expected_original = {
            "original_prompt_sha256": _sha256(_text_bytes(source.specification)),
            "original_reference_rtl_sha256": _sha256(_text_bytes(source.reference_rtl)),
            "original_testbench_sha256": _sha256(_text_bytes(source.testbench)),
        }
        for field, value in expected_original.items():
            if correction.get(field) != value:
                errors.append(f"{source_id}: {field} mismatch")
        text, content, read_errors = _read_correction_text(correction, correction_root)
        errors.extend(f"{source_id}: {error}" for error in read_errors)
        if text is None or content is None:
            continue
        if correction.get("corrected_testbench_sha256") != _sha256(content):
            errors.append(f"{source_id}: corrected testbench hash mismatch")
        if source.reference_rtl and _text_bytes(source.reference_rtl) in content:
            errors.append(f"{source_id}: corrected testbench contains reference RTL bytes")
        structural = _structural_errors(source, text)
        errors.extend(f"{source_id}: {error}" for error in structural)
        mutation = _mutation_contract(source_id)
        if not mutation["validated_offline"] or not mutation["cases"]:
            errors.append(f"{source_id}: offline negative mutation contract is incomplete")
        readiness, readiness_reasons = _readiness(replace(source, testbench=text))
        if readiness != "executable_ready" or readiness_reasons:
            errors.append(f"{source_id}: corrected readiness is {readiness}: {readiness_reasons}")
        validated.append({
            **correction,
            "dependency_closure": "passed" if not structural else "failed",
            "verification_readiness": readiness,
            "negative_mutation_validation": mutation,
        })
    return validated, sorted(set(errors))


def _task_id_from_source(row: SourceRow, source_commit: str) -> str:
    # Importing the canonical helper keeps task identity exactly aligned with
    # the inventory and avoids deriving a second task-ID algorithm here.
    original_commit = row.source_commit
    row.source_commit = source_commit
    try:
        from scripts.dataset.rtl_generation_preparation import _task_id
        return _task_id(row)
    finally:
        row.source_commit = original_commit


def correction_manifest_report(
    selection: dict[str, Any],
    correction_rows: list[dict[str, Any]],
    *,
    source_commit: str,
    base_inventory_sha256: str,
    base_split_sha256: str,
    correction_manifest_sha256: str,
    corrected_inventory_sha256: str,
    source_tree_sha256: str | None,
    inventory_counts: dict[str, int],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": CORRECTION_MANIFEST_SCHEMA_VERSION,
        "source_dataset": "VerilogEval",
        "source_commit": source_commit,
        "source_tree_sha256": source_tree_sha256,
        "base_inventory_sha256": base_inventory_sha256,
        "base_split_sha256": base_split_sha256,
        "correction_version": CORRECTION_VERSION,
        "selection_report_sha256": selection.get("selection_report_sha256"),
        "correction_manifest_sha256": correction_manifest_sha256,
        "corrected_inventory_sha256": corrected_inventory_sha256,
        "row_count": len(correction_rows),
        "rows": correction_rows,
        "readiness_counts": inventory_counts,
        "reference_modified": False,
        "reference_copied_to_support": False,
        "dependency_closure_passed": not errors and all(row.get("dependency_closure") == "passed" for row in correction_rows),
        "verification_readiness": "executable_ready" if not errors and len(correction_rows) == 5 else "invalid",
        "negative_mutation_execution": "deferred_to_isolated_verification",
        "errors": sorted(set(errors)),
    }


__all__ = [
    "CORRECTION_MANIFEST_SCHEMA_VERSION",
    "CORRECTION_ROW_SCHEMA_VERSION",
    "CORRECTION_SELECTION_SCHEMA_VERSION",
    "CORRECTION_VALIDATION_SCHEMA_VERSION",
    "CORRECTION_VERSION",
    "EXPECTED_SOURCE_IDS",
    "create_correction_manifest",
    "correction_manifest_report",
    "load_correction_manifest",
    "overlay_source_rows",
    "select_correction_rows",
    "sha256_file",
    "validate_correction_rows",
]
