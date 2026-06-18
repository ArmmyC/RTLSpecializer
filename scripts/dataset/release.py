"""Dataset release assembly helpers for dataset_v0.1 JSONL files."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .constants import (
    ANSWER_SCHEMA_VERSION, ARTIFACT_FIELDS, DATASET_VERSION, SOURCES,
    TASK_SCHEMA_VERSION,
)
from .io_utils import load_jsonl, write_jsonl
from .review_promotion import STUB_PHRASES
from .split_dataset import ratios_valid, split_rows
from .validation import validate_dataset_file


UNCERTAIN_LICENSES = {"unknown", "uncertain", "todo"}
PRIVATE_MARKERS = ("private", "proprietary", "company_internal", "confidential")


@dataclass(frozen=True)
class ReleaseConfig:
    release_name: str
    output_root: Path
    input_paths: list[Path]
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15)
    seed: int = 7
    allow_family_overlap: bool = False
    allow_source_overlap: bool = False
    min_rows: int = 1
    strict: bool = False


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_fingerprint(row: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def artifact_fingerprint(row: dict[str, Any]) -> str | None:
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    task = messages[1].get("content") if len(messages) > 1 and isinstance(messages[1], dict) else {}
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    pieces = [
        f"{name}\0{value}" for name in sorted(ARTIFACT_FIELDS)
        if isinstance((value := artifacts.get(name)), str) and value.strip()
    ]
    if not pieces:
        return None
    return hashlib.sha256("\0".join(pieces).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_release_inputs(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in paths:
        report = validate_dataset_file(path, strict=True)
        if not report.ok:
            warnings.extend(item.format() for item in report.errors + report.warnings)
        loaded, problems = load_jsonl(path)
        for problem in problems:
            rejected.append({"id": None, "reason": "input load failed", "errors": [problem.message], "row": {"input": str(path)}})
        for line, row in loaded:
            row = deepcopy(row)
            row["_release_input"] = str(path)
            row["_release_line"] = line
            rows.append(row)
    return rows, rejected, warnings


def release_eligibility_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("dataset_version") != DATASET_VERSION:
        errors.append(f"dataset_version must be {DATASET_VERSION}")
    if row.get("review_status") not in {"validated", "reviewed"}:
        errors.append("review_status must be validated or reviewed")
    if row.get("source") not in SOURCES:
        errors.append("source must be an allowed source enum")
    license_value = row.get("license")
    if not isinstance(license_value, str) or not license_value.strip():
        errors.append("license must be non-empty")
    elif license_value.strip().lower() in UNCERTAIN_LICENSES:
        errors.append("license must not be unknown, uncertain, or todo")
    searchable = canonical_json({
        "source": row.get("source"),
        "license": row.get("license"),
        "provenance": row.get("provenance"),
    }).lower()
    if any(marker in searchable for marker in PRIVATE_MARKERS):
        errors.append("private/proprietary source marker is not allowed")
    answer = None
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    if len(messages) > 2 and isinstance(messages[2], dict):
        answer = messages[2].get("content")
    if isinstance(answer, dict) and any(phrase in canonical_json(answer) for phrase in STUB_PHRASES):
        errors.append("assistant answer still contains generic import stub text")
    return errors


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(row)
    cleaned.pop("_release_input", None)
    cleaned.pop("_release_line", None)
    return cleaned


def _validate_single(row: dict[str, Any], temp: Path) -> list[str]:
    write_jsonl(temp, [_clean_row(row)])
    report = validate_dataset_file(temp, strict=True)
    try:
        temp.unlink()
    except FileNotFoundError:
        pass
    return [item.format() for item in report.errors + report.warnings]


def _reason(errors: list[str]) -> str:
    text = " ".join(errors).lower()
    if "duplicate row id" in text:
        return "duplicate row id"
    if "duplicate full-row fingerprint" in text:
        return "duplicate row fingerprint"
    if "duplicate artifact fingerprint" in text:
        return "duplicate artifact fingerprint"
    if "license" in text:
        return "license gate"
    if "review_status" in text:
        return "review status gate"
    if "stub" in text:
        return "stub answer"
    if "private" in text or "proprietary" in text:
        return "private source gate"
    if "validation" in text or "field=" in text:
        return "validation failed"
    return "release eligibility failed"


def _reject(row: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    return {"id": row.get("id"), "reason": _reason(errors), "errors": errors, "row": _clean_row(row)}


def _stats(split: dict[str, list[dict[str, Any]]], rejected: list[dict[str, Any]], leakage: dict[str, Any]) -> dict[str, Any]:
    accepted = [row for rows in split.values() for row in rows]
    claim_levels: dict[str, Counter[str]] = defaultdict(Counter)
    for row in accepted:
        answer = row["messages"][2]["content"]
        for domain, level in answer.get("claim_levels", {}).items():
            claim_levels[domain][level] += 1
    return {
        "rows_by_split": {name: len(rows) for name, rows in split.items()},
        "rows_by_source": dict(sorted(Counter(row.get("source") for row in accepted).items())),
        "rows_by_task_type": dict(sorted(Counter(row.get("task_family") for row in accepted).items())),
        "rows_by_design_family": dict(sorted(Counter(row.get("design_family") for row in accepted).items())),
        "rows_by_review_status": dict(sorted(Counter(row.get("review_status") for row in accepted).items())),
        "claim_levels": {domain: dict(sorted(counter.items())) for domain, counter in sorted(claim_levels.items())},
        "rejected_reasons": dict(sorted(Counter(row.get("reason") for row in rejected).items())),
        "duplicate_leakage_checks": leakage,
    }


def _dataset_card(config: ReleaseConfig, stats: dict[str, Any], result: dict[str, Any]) -> str:
    tasks = "\n".join(f"- `{name}`: {count}" for name, count in stats["rows_by_task_type"].items()) or "- none"
    families = "\n".join(f"- `{name}`: {count}" for name, count in stats["rows_by_design_family"].items()) or "- none"
    sources = "\n".join(f"- `{name}`: {count}" for name, count in stats["rows_by_source"].items()) or "- none"
    return f"""# Dataset card: {config.release_name}

## Purpose

This release assembles validated/reviewed `dataset_v0.1` RTL review rows into deterministic train/val/test JSONL files for future evaluation or training workflows.

## Row counts

- Train: {result['train_rows']}
- Val: {result['val_rows']}
- Test: {result['test_rows']}
- Rejected: {result['rejected_rows']}

## Included sources

{sources}

## Task distribution

{tasks}

## Design family distribution

{families}

## Claim-level policy

Rows preserve claim levels from reviewed inputs. `verified` and `tool_supported` claims are accepted only if the existing strict dataset validator accepts their evidence.

## Provenance and license warning

Rows with empty, unknown, uncertain, or todo licenses are excluded. Public rows still require upstream license review before use outside this repository.

## Known limitations

Release assembly does not prove RTL correctness, area improvement, activity improvement, or power behavior. It does not run EDA tools, execute artifacts, call LLMs, download data, upload data, or train a model.

## Rebuild command

```bash
python scripts/dataset/build_dataset_release.py --release-name {config.release_name} {' '.join('--input ' + str(path) for path in config.input_paths)} --output-dir {config.output_root} --seed {config.seed}
```
"""


def build_release(config: ReleaseConfig) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    train_ratio, val_ratio, test_ratio = config.ratios
    if not ratios_valid(train_ratio, val_ratio, test_ratio):
        errors.append("ratios must be non-negative and sum to 1.0")
    if config.min_rows < 1:
        errors.append("min_rows must be at least 1")
    if errors:
        return _result(False, config, 0, 0, 0, 0, 0, 0, errors, warnings), 1

    loaded_rows, rejected, input_warnings = load_release_inputs(config.input_paths)
    warnings.extend(input_warnings)
    release_dir = config.output_root / config.release_name
    temp = release_dir / ".row_validation.tmp.jsonl"
    accepted: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    seen_rows: dict[str, str] = {}
    seen_artifacts: dict[str, str] = {}
    leakage_summary = {
        "duplicate_row_ids": 0,
        "duplicate_row_fingerprints": 0,
        "duplicate_artifact_fingerprints": 0,
        "family_overlap_allowed": config.allow_family_overlap,
        "source_overlap_allowed": config.allow_source_overlap,
    }
    release_dir.mkdir(parents=True, exist_ok=True)
    for row in loaded_rows:
        row_errors = release_eligibility_errors(row)
        row_errors.extend(_validate_single(row, temp))
        row_id = row.get("id")
        if isinstance(row_id, str):
            if row_id in seen_ids:
                row_errors.append(f"duplicate row id: {row_id}; first seen in {seen_ids[row_id]}")
                leakage_summary["duplicate_row_ids"] += 1
            else:
                seen_ids[row_id] = str(row.get("_release_input"))
        fp = row_fingerprint(_clean_row(row))
        if fp in seen_rows:
            row_errors.append(f"duplicate full-row fingerprint; first seen in {seen_rows[fp]}")
            leakage_summary["duplicate_row_fingerprints"] += 1
        else:
            seen_rows[fp] = str(row_id)
        artifact_fp = artifact_fingerprint(row)
        if artifact_fp:
            if artifact_fp in seen_artifacts:
                row_errors.append(f"duplicate artifact fingerprint; first seen in {seen_artifacts[artifact_fp]}")
                leakage_summary["duplicate_artifact_fingerprints"] += 1
            else:
                seen_artifacts[artifact_fp] = str(row_id)
        if row_errors:
            rejected.append(_reject(row, row_errors))
        else:
            accepted.append(_clean_row(row))

    if len(accepted) < config.min_rows:
        errors.append(f"accepted rows {len(accepted)} is below min_rows {config.min_rows}")
    split = split_rows(accepted, config.ratios, config.seed, config.allow_family_overlap) if accepted else {"train": [], "val": [], "test": []}
    if not config.allow_family_overlap:
        families: dict[str, set[str]] = defaultdict(set)
        for split_name, rows in split.items():
            for row in rows:
                families[row["design_family"]].add(split_name)
        overlaps = {family: sorted(names) for family, names in families.items() if len(names) > 1}
        if overlaps:
            errors.extend(f"design family appears in multiple splits: {family} {names}" for family, names in overlaps.items())
    for name, rows in split.items():
        write_jsonl(release_dir / f"{name}.jsonl", rows)
    write_jsonl(release_dir / "rejected_rows.jsonl", rejected)
    write_jsonl(release_dir / "all_accepted.unsplit.jsonl", accepted)
    for name in ("train", "val", "test"):
        report = validate_dataset_file(release_dir / f"{name}.jsonl", strict=True)
        errors.extend(item.format() for item in report.errors + report.warnings)
    stats = _stats(split, rejected, leakage_summary)
    (release_dir / "stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = _result(not errors, config, len(loaded_rows), len(accepted), len(rejected), len(split["train"]), len(split["val"]), len(split["test"]), errors, warnings)
    (release_dir / "dataset_card.md").write_text(_dataset_card(config, stats, result), encoding="utf-8")
    files = {
        "train": ("train.jsonl", len(split["train"])),
        "val": ("val.jsonl", len(split["val"])),
        "test": ("test.jsonl", len(split["test"])),
        "rejected": ("rejected_rows.jsonl", len(rejected)),
        "stats": ("stats.json", 1),
        "dataset_card": ("dataset_card.md", 1),
        "all_accepted": ("all_accepted.unsplit.jsonl", len(accepted)),
    }
    manifest = {
        "release_name": config.release_name,
        "dataset_version": DATASET_VERSION,
        "schema_versions": {"task": TASK_SCHEMA_VERSION, "answer": ANSWER_SCHEMA_VERSION},
        "created_by": "build_dataset_release.py",
        "seed": config.seed,
        "ratios": {"train": train_ratio, "val": val_ratio, "test": test_ratio},
        "input_files": [str(path) for path in config.input_paths],
        "files": {
            key: {"path": rel, "sha256": file_sha256(release_dir / rel), "rows": rows}
            for key, (rel, rows) in files.items()
        },
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result, 0 if result["ok"] else 1


def _result(ok: bool, config: ReleaseConfig, input_rows: int, accepted_rows: int, rejected_rows: int, train_rows: int, val_rows: int, test_rows: int, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": ok,
        "release_name": config.release_name,
        "input_files": len(config.input_paths),
        "input_rows": input_rows,
        "accepted_rows": accepted_rows,
        "rejected_rows": rejected_rows,
        "train_rows": train_rows,
        "val_rows": val_rows,
        "test_rows": test_rows,
        "output_dir": str(config.output_root / config.release_name),
        "errors": errors,
        "warnings": warnings,
    }
