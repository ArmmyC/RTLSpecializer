#!/usr/bin/env python3
"""Validate the v008 authored assets without executing HDL.

The audit verifies the public-specification authoring manifest, correction
manifest, file layout, permissions, hashes, privacy markers, and explicit
retry lineage.  It does not compile or simulate any fixture or testbench.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.author_rtl_generation_assetfix_v008 import (
    CORRECTION_VERSION,
    RETRY_SOURCE_IDS,
    SOURCE_IDS,
)
from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


REPORT_SCHEMA_VERSION = "rtl_verification_assetfix_v008_static_validation_v0.1"
PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "refmodule",
    "candidate_evidence",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"symlink input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"symlink input: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def audit(
    *,
    correction_root: Path,
    manifest_path: Path,
    authoring_manifest_path: Path,
    attestation_path: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_files = {
        "manifest.jsonl",
        "authoring_attestation.json",
        "qualification/authoring_manifest.jsonl",
    }
    expected_files.update(
        f"tasks/{source_id}/testbench.sv" for source_id in SOURCE_IDS
    )
    for source_id in SOURCE_IDS:
        expected_files.update(
            f"qualification/{source_id}/{name}.sv"
            for name in ("positive", *MUTATION_NAMES[source_id])
        )

    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    owner_pairs: set[tuple[int, int]] = set()
    root_stat = correction_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or correction_root.is_symlink():
        errors.append("correction root must be a real directory")
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        errors.append("correction root mode must be 0700")

    for path in sorted(correction_root.rglob("*")):
        relative = path.relative_to(correction_root).as_posix()
        metadata = path.lstat()
        owner_pairs.add((metadata.st_uid, metadata.st_gid))
        if stat.S_ISLNK(metadata.st_mode):
            errors.append(f"symlink found: {relative}")
            continue
        if stat.S_ISDIR(metadata.st_mode):
            actual_dirs.add(relative)
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                errors.append(f"directory mode is not 0700: {relative}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            errors.append(f"special file found: {relative}")
            continue
        actual_files.add(relative)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            errors.append(f"file mode is not 0600: {relative}")
        if metadata.st_nlink != 1:
            errors.append(f"hard link found: {relative}")
        try:
            text = path.read_text(encoding="utf-8").casefold()
        except UnicodeDecodeError:
            text = ""
        for marker in PRIVATE_MARKERS:
            if marker.casefold() in text:
                errors.append(f"private marker {marker} found in {relative}")

    if actual_files != expected_files:
        errors.append("correction file layout differs from the 20-task/60-case contract")
    if len(owner_pairs) > 1:
        errors.append("correction files do not share one UID/GID pair")

    manifest_rows = load_jsonl(manifest_path)
    authoring_rows = load_jsonl(authoring_manifest_path)
    attestation = load_json(attestation_path)
    if [row.get("source_id") for row in manifest_rows] != list(SOURCE_IDS):
        errors.append("correction manifest order mismatch")
    if [row.get("source_id") for row in authoring_rows] != list(SOURCE_IDS):
        errors.append("authoring manifest order mismatch")
    if len(manifest_rows) != 20 or len(authoring_rows) != 20:
        errors.append("manifest row count is not 20")
    if attestation.get("correction_version") != CORRECTION_VERSION:
        errors.append("authoring attestation correction version mismatch")
    if attestation.get("selected_source_ids") != list(SOURCE_IDS):
        errors.append("authoring attestation order mismatch")
    if attestation.get("retry_source_ids") != [source_id for source_id in SOURCE_IDS if source_id in RETRY_SOURCE_IDS]:
        errors.append("authoring attestation retry lineage mismatch")
    if attestation.get("reference_rtl_supplied") is not False:
        errors.append("authoring attestation reference flag is not false")
    if attestation.get("support_file_count") != 0:
        errors.append("authoring attestation support-file count is not zero")

    manifest_by_id = {row.get("source_id"): row for row in manifest_rows}
    authoring_by_id = {row.get("source_id"): row for row in authoring_rows}
    fixture_count = 0
    distinct_mutation_count = 0
    for source_id in SOURCE_IDS:
        manifest = manifest_by_id.get(source_id, {})
        authoring = authoring_by_id.get(source_id, {})
        testbench_path = correction_root / manifest.get("testbench_path", "")
        if not testbench_path.is_file() or testbench_path.is_symlink():
            errors.append(f"testbench path is invalid: {source_id}")
        else:
            content = testbench_path.read_bytes()
            audit_result, audit_errors = static_testbench_audit(content)
            if audit_errors:
                errors.extend(f"{source_id}: {item}" for item in audit_errors)
            if sha256_file(testbench_path) != manifest.get("corrected_testbench_sha256"):
                errors.append(f"testbench hash mismatch: {source_id}")
            if manifest.get("static_audit") != audit_result:
                errors.append(f"static audit mismatch: {source_id}")

        expected_names = ("positive", *MUTATION_NAMES[source_id])
        negative_rows = authoring.get("negative_mutations", [])
        if [row.get("name") for row in negative_rows] != list(MUTATION_NAMES[source_id]):
            errors.append(f"mutation order mismatch: {source_id}")
        for name in expected_names:
            path = correction_root / "qualification" / source_id / f"{name}.sv"
            fixture_count += 1
            if not path.is_file() or path.is_symlink():
                errors.append(f"fixture path is invalid: {source_id}:{name}")
                continue
            fixture_hash = sha256_file(path)
            if fixture_hash != attestation.get("fixture_hashes", {}).get(source_id, {}).get(name):
                errors.append(f"fixture hash missing or mismatched: {source_id}:{name}")
            if name != "positive":
                distinct_mutation_count += 1
                if fixture_hash == attestation.get("fixture_hashes", {}).get(source_id, {}).get("positive"):
                    errors.append(f"mutation bytes equal positive bytes: {source_id}:{name}")
    if fixture_count != 60 or distinct_mutation_count != 40:
        errors.append("qualification fixture count mismatch")

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "correction_version": CORRECTION_VERSION,
        "selected_count": len(SOURCE_IDS),
        "testbench_count": 20,
        "positive_case_count": 20,
        "negative_case_count": 40,
        "qualification_case_count": 60,
        "retry_source_ids": [source_id for source_id in SOURCE_IDS if source_id in RETRY_SOURCE_IDS],
        "new_uncovered_source_count": 16,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "semantic_equivalence_checks": "not_executed_static_only",
        "static_dependency_closure_passed": not errors,
        "manifest_sha256": sha256_file(manifest_path),
        "authoring_manifest_sha256": sha256_file(authoring_manifest_path),
        "authoring_attestation_sha256": sha256_file(attestation_path),
        "owner_uid_gid_pairs": [list(pair) for pair in sorted(owner_pairs)],
        "errors": sorted(set(errors)),
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--authoring-manifest", required=True, type=Path)
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit(
            correction_root=args.correction_root,
            manifest_path=args.manifest,
            authoring_manifest_path=args.authoring_manifest,
            attestation_path=args.attestation,
        )
        if args.report.exists() or args.report.is_symlink():
            raise ValueError(f"refusing to replace report: {args.report}")
        args.report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.report.open("xb") as handle:
            handle.write((json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        args.report.chmod(0o600)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result = {"schema_version": REPORT_SCHEMA_VERSION, "ok": False, "errors": [str(exc)]}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
