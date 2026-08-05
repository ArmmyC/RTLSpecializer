"""Package one or more sanitized RTL repair trajectories.

This control-plane tool consumes already-published candidate and verification
records.  It never executes RTL, reads private verification assets, or calls a
model.  The output is auxiliary repair data and is deliberately not a clean
specification-to-RTL generation package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_human_review import package_tree_sha256


SCHEMA_VERSION = "rtl_repair_trajectory_v0.1"
PACKAGE_SCHEMA_VERSION = "rtl_repair_trajectory_package_v0.1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    "reference.sv",
    "reference rtl",
    "refmodule",
    "testbench.sv",
    "module tb",
    "candidate_evidence",
    "mutation",
    "iverilog",
    "verilator",
    "yosys",
    "vvp",
)


def _sha256_file(path: Path) -> str:
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
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} is not an object")
        rows.append(value)
    return rows


def _contains_private_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_private_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return any(marker in lowered for marker in PRIVATE_MARKERS)
    return False


def _parse_artifacts(values: list[str] | None, *, paths: bool) -> dict[str, Path | str]:
    result: dict[str, Path | str] = {}
    for value in values or []:
        key, separator, raw = value.partition("=")
        if not separator or not key or not raw or key in result:
            raise ValueError("artifact bindings must use unique KEY=VALUE entries")
        result[key] = Path(raw) if paths else raw
    return result


def _resolve_artifacts(
    bindings: dict[str, Path | str],
) -> tuple[dict[str, str], list[str]]:
    resolved: dict[str, str] = {}
    errors: list[str] = []
    for key, value in bindings.items():
        if isinstance(value, Path):
            if value.is_symlink() or not value.is_file():
                errors.append(f"artifact is missing or symlinked: {key}")
                continue
            resolved[key] = _sha256_file(value)
        elif isinstance(value, str) and SHA256_RE.fullmatch(value):
            resolved[key] = value
        else:
            errors.append(f"artifact hash is invalid: {key}")
    return dict(sorted(resolved.items())), sorted(set(errors))


def _write_exclusive(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)


def _output_hashes(root: Path) -> dict[str, str]:
    return {
        path.name: _sha256_file(path)
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }


def validate_repair_trajectory_package(
    package_dir: Path,
    *,
    expected_artifacts: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    expected_files = {
        "all.jsonl",
        "manifest.json",
        "statistics.json",
        "dataset_card.md",
        "provenance_report.json",
        "validation_report.json",
        "validation_report.md",
    }
    if package_dir.is_symlink() or not package_dir.is_dir():
        return {"ok": False, "errors": ["package directory is missing or symlinked"]}, 1
    actual_files = {path.name for path in package_dir.iterdir() if path.is_file()}
    if actual_files != expected_files:
        errors.append("repair package layout is not exact")
    for path in package_dir.rglob("*"):
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            errors.append(f"repair package contains an unsafe entry: {path.name}")
    try:
        rows = _load_jsonl(package_dir / "all.jsonl")
        manifest = _load_json(package_dir / "manifest.json")
        statistics = _load_json(package_dir / "statistics.json")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [f"could not load repair package: {exc}"]}, 1
    if len(rows) != 1:
        errors.append("repair package must contain exactly one trajectory")
    if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        errors.append("repair package manifest schema mismatch")
    if manifest.get("row_count") != len(rows):
        errors.append("repair package manifest row count mismatch")
    if statistics.get("row_count") != len(rows):
        errors.append("repair package statistics row count mismatch")
    if manifest.get("promotion_allowed") is not False or manifest.get("training_allowed") is not False:
        errors.append("repair trajectory package has unsafe training/promotion metadata")
    if expected_artifacts is not None and manifest.get("artifact_bindings") != dict(sorted(expected_artifacts.items())):
        errors.append("repair package artifact bindings do not match expected values")
    for index, row in enumerate(rows, 1):
        required = {
            "schema_version", "task_id", "source_id", "top_module",
            "failure_category_before_repair", "failed_attempt", "accepted_attempt",
            "failed_candidate_id", "failed_candidate_sha256", "failed_candidate_rtl",
            "accepted_candidate_id", "accepted_candidate_sha256", "accepted_candidate_rtl",
            "sanitized_diagnostics", "public_task",
        }
        if set(row) != required:
            errors.append(f"trajectory row {index} has an invalid field set")
            continue
        if row.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"trajectory row {index} schema mismatch")
        if row.get("failed_attempt") != 1 or row.get("accepted_attempt") != 2:
            errors.append(f"trajectory row {index} attempt lineage mismatch")
        for field in ("failed_candidate_sha256", "accepted_candidate_sha256"):
            if not isinstance(row.get(field), str) or not SHA256_RE.fullmatch(row.get(field, "")):
                errors.append(f"trajectory row {index} {field} is invalid")
        for label, field in (("failed", "failed_candidate_rtl"), ("accepted", "accepted_candidate_rtl")):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"trajectory row {index} {field} is invalid")
            elif hashlib.sha256(row[field].encode("utf-8")).hexdigest() != row[f"{label}_candidate_sha256"]:
                errors.append(f"trajectory row {index} {label} candidate hash mismatch")
        if row.get("failure_category_before_repair") != "compile_failure":
            errors.append(f"trajectory row {index} failure category is not compile_failure")
        if not isinstance(row.get("sanitized_diagnostics"), list) or not all(isinstance(item, str) for item in row["sanitized_diagnostics"]):
            errors.append(f"trajectory row {index} diagnostics are invalid")
        if _contains_private_marker(row):
            errors.append(f"trajectory row {index} contains private content")
    report = {
        "ok": not errors,
        "row_count": len(rows),
        "errors": sorted(set(errors)),
        "private_content_detected": any(_contains_private_marker(row) for row in rows),
        "package_tree_sha256": package_tree_sha256(package_dir),
    }
    return report, 0 if report["ok"] else 1


def package_repair_trajectories(
    *,
    tasks_path: Path,
    candidates_path: Path,
    attempts_path: Path,
    repair_packet_path: Path,
    output_dir: Path,
    artifact_bindings: dict[str, Path] | None = None,
    artifact_hashes: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    if output_dir.exists() or output_dir.is_symlink():
        return {"ok": False, "errors": ["repair package output already exists"]}, 1
    try:
        tasks = _load_jsonl(tasks_path)
        candidates = _load_jsonl(candidates_path)
        attempts = _load_jsonl(attempts_path)
        packet = _load_json(repair_packet_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [f"could not load repair inputs: {exc}"]}, 1
    rows = packet.get("rows") if isinstance(packet, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
        return {"ok": False, "errors": ["repair packet must contain exactly one row"]}, 1
    packet_row = rows[0]
    task_by_id = {row.get("task_id"): row for row in tasks}
    candidate_by_id = {row.get("candidate_id"): row for row in candidates}
    attempt_rows = {row.get("candidate_id"): row for row in attempts}
    previous = packet_row.get("previous_candidate")
    public_task = packet_row.get("task")
    feedback = packet_row.get("verification_feedback")
    if not isinstance(previous, dict) or not isinstance(public_task, dict) or not isinstance(feedback, dict):
        return {"ok": False, "errors": ["repair packet fields are malformed"]}, 1
    task_id = public_task.get("task_id")
    task = task_by_id.get(task_id)
    failed_id = f"{task_id}_attempt_01"
    accepted_id = f"{task_id}_attempt_02"
    failed_record = candidate_by_id.get(failed_id)
    accepted_record = candidate_by_id.get(accepted_id)
    failed_attempt = attempt_rows.get(failed_id)
    accepted_attempt = attempt_rows.get(accepted_id)
    errors: list[str] = []
    if task is None or public_task != task:
        errors.append("repair packet public task does not match canonical task")
    if failed_record is None or accepted_record is None or failed_attempt is None or accepted_attempt is None:
        errors.append("repair inputs do not contain both canonical attempts")
    if errors:
        return {"ok": False, "errors": errors}, 1
    assert task is not None and failed_record is not None and accepted_record is not None
    assert failed_attempt is not None and accepted_attempt is not None
    failed_rtl = failed_record["candidate"]["rtl"]
    accepted_rtl = accepted_record["candidate"]["rtl"]
    if previous.get("task_id") != task_id or hashlib.sha256(previous.get("rtl", "").encode("utf-8")).hexdigest() != failed_record["candidate_sha256"]:
        errors.append("repair packet failed candidate does not match canonical attempt 01")
    if failed_attempt.get("accepted") is not False or failed_attempt.get("failure_category") != "compile_failure":
        errors.append("attempt 01 is not the expected compile failure")
    if accepted_attempt.get("accepted") is not True or accepted_attempt.get("attempt") != 2:
        errors.append("attempt 02 is not the accepted repair")
    if failed_rtl == accepted_rtl:
        errors.append("accepted repair reproduces the failed candidate")
    diagnostics = feedback.get("diagnostics")
    if feedback.get("failure_category") != "compile_failure" or not isinstance(diagnostics, list) or not all(isinstance(item, str) for item in diagnostics):
        errors.append("repair feedback is not sanitized compile feedback")
    trajectory = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "source_id": task["source_id"],
        "top_module": task["top_module"],
        "failure_category_before_repair": "compile_failure",
        "failed_attempt": 1,
        "accepted_attempt": 2,
        "failed_candidate_id": failed_id,
        "failed_candidate_sha256": failed_record["candidate_sha256"],
        "failed_candidate_rtl": failed_rtl,
        "accepted_candidate_id": accepted_id,
        "accepted_candidate_sha256": accepted_record["candidate_sha256"],
        "accepted_candidate_rtl": accepted_rtl,
        "sanitized_diagnostics": diagnostics,
        "public_task": task,
    }
    if _contains_private_marker(trajectory):
        errors.append("repair trajectory contains private content")
    artifacts, artifact_errors = _resolve_artifacts({**(artifact_bindings or {}), **(artifact_hashes or {})})
    errors.extend(artifact_errors)
    if errors:
        return {"ok": False, "errors": sorted(set(errors))}, 1
    output_dir.mkdir(mode=0o700, parents=True)
    output_paths = {
        "all": output_dir / "all.jsonl",
        "manifest": output_dir / "manifest.json",
        "statistics": output_dir / "statistics.json",
        "dataset_card": output_dir / "dataset_card.md",
        "provenance": output_dir / "provenance_report.json",
        "validation": output_dir / "validation_report.json",
        "validation_md": output_dir / "validation_report.md",
    }
    try:
        _write_exclusive(output_paths["all"], (json.dumps(trajectory, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": output_dir.name,
            "row_count": 1,
            "source_commit": task.get("provenance", {}).get("source_commit"),
            "promotion_allowed": False,
            "training_allowed": False,
            "training_scope": "repair_trajectory_only",
            "artifact_bindings": artifacts,
            "failed_candidate_excluded_from_clean_generation": True,
        }
        _write_exclusive(output_paths["manifest"], (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_exclusive(output_paths["statistics"], (json.dumps({"schema_version": "rtl_repair_trajectory_statistics_v0.1", "row_count": 1, "compile_failures_repaired": 1, "promotion_allowed": False}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_exclusive(output_paths["dataset_card"], b"# RTL repair trajectories\n\nAuxiliary repair data; not a clean generation dataset and not promotion-authorized.\n")
        _write_exclusive(output_paths["provenance"], (json.dumps({"schema_version": "rtl_repair_trajectory_provenance_v0.1", "artifact_bindings": artifacts, "failed_candidate_excluded_from_clean_generation": True, "promotion_allowed": False}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_exclusive(output_paths["validation"], b"{}\n")
        _write_exclusive(output_paths["validation_md"], b"# Repair trajectory validation\n")
        preliminary, preliminary_code = validate_repair_trajectory_package(output_dir, expected_artifacts=artifacts)
        output_paths["validation"].write_text(json.dumps(preliminary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        output_paths["validation_md"].write_text(
            f"# Repair trajectory validation\n\n- Result: {'passed' if preliminary_code == 0 else 'failed'}\n- Rows: 1\n- Promotion allowed: false\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    for path in output_dir.iterdir():
        path.chmod(0o600)
    report, code = validate_repair_trajectory_package(output_dir, expected_artifacts=artifacts)
    return {"ok": code == 0, "package_dir": str(output_dir), "validation": report, "errors": report.get("errors", [])}, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=Path)
    parser.add_argument("--repair-packet", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--artifact-binding", action="append", metavar="KEY=PATH")
    parser.add_argument("--artifact-hash", action="append", metavar="KEY=SHA256")
    args = parser.parse_args(argv)
    try:
        bindings = _parse_artifacts(args.artifact_binding, paths=True)
        hashes = _parse_artifacts(args.artifact_hash, paths=False)
        result, code = package_repair_trajectories(
            tasks_path=args.tasks,
            candidates_path=args.candidates,
            attempts_path=args.attempts,
            repair_packet_path=args.repair_packet,
            output_dir=args.output_dir,
            artifact_bindings={key: value for key, value in bindings.items() if isinstance(value, Path)},
            artifact_hashes={key: value for key, value in hashes.items() if isinstance(value, str)},
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
