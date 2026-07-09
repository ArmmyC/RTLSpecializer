"""Inventory and cleanup-planning helpers for the local data workspace."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
from typing import Any

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.release import file_sha256


TASK_SCHEMAS = {"rtl_task.v0.1", "rtl_task_v0.1"}
ANSWER_SCHEMAS = {"rtl_answer.v0.1", "rtl_answer_v0.1"}
KNOWN_SCHEMAS = TASK_SCHEMAS | ANSWER_SCHEMAS
DEFAULT_INVENTORY_MD = Path("data/reports/data_workspace_inventory.md")
DEFAULT_INVENTORY_JSON = Path("data/reports/data_workspace_inventory.json")
DEFAULT_PLAN_MD = Path("data/reports/data_workspace_cleanup_plan.md")
DEFAULT_PLAN_JSON = Path("data/reports/data_workspace_cleanup_plan.json")
DEFAULT_APPLIED_MD = Path("data/reports/data_workspace_cleanup_applied.md")
DEFAULT_APPLIED_JSON = Path("data/reports/data_workspace_cleanup_applied.json")
ROLE_ORDER = (
    "raw_source",
    "normalized_task",
    "teacher_answer_batch",
    "repaired_answer_batch",
    "assembled_answer_jsonl",
    "distill_dataset",
    "validation_report",
    "repair_report",
    "assembly_report",
    "eval_prompt",
    "eval_run",
    "unknown",
)
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(payload) + "\n", encoding="utf-8")


def _safe_read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON: {exc.msg}"
    except (OSError, UnicodeError) as exc:
        return None, f"could not read file: {exc}"


def _structured_rows(path: Path) -> tuple[list[dict[str, Any]], str | None, str | None, str | None]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows_with_lines, problems = load_jsonl(path)
        rows = [row for _, row in rows_with_lines]
        parse_error = None
        if problems:
            parse_error = "; ".join(
                f"line {problem.line}: {problem.message}" if problem.line else problem.message
                for problem in problems
            )
        return rows, "jsonl", parse_error, None
    if suffix != ".json":
        return [], None, None, None
    payload, error = _safe_read_json(path)
    if error:
        return [], None, error, None
    if isinstance(payload, dict) and isinstance(payload.get("answers"), list):
        rows = [row for row in payload["answers"] if isinstance(row, dict)]
        return rows, "answers_wrapper", None, payload.get("batch_schema_version")
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = [row for row in payload["rows"] if isinstance(row, dict)]
        return rows, "rows_wrapper", None, payload.get("batch_schema_version")
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows, "json_array", None, None
    if isinstance(payload, dict):
        if isinstance(payload.get("messages"), list) or isinstance(payload.get("schema_version"), str) or isinstance(payload.get("source_id"), str):
            return [payload], "json_single", None, payload.get("batch_schema_version")
    return [], None, None, None


def _detect_schema(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "unknown"
    schemas: Counter[str] = Counter()
    message_rows = 0
    for row in rows:
        schema_version = row.get("schema_version")
        if isinstance(schema_version, str) and schema_version in KNOWN_SCHEMAS:
            schemas[schema_version] += 1
        elif isinstance(row.get("messages"), list):
            message_rows += 1
    if schemas and len(schemas) == 1 and sum(schemas.values()) == len(rows):
        return next(iter(schemas))
    if message_rows == len(rows):
        return "train/chat row with messages"
    if schemas and sum(schemas.values()) == len(rows):
        return next(iter(schemas.most_common(1)))[0]
    return "unknown"


def _extract_source_ids(rows: list[dict[str, Any]]) -> list[str]:
    source_ids: list[str] = []
    for row in rows:
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            source_ids.append(source_id)
            continue
        messages = row.get("messages")
        if isinstance(messages, list):
            user_content = messages[1].get("content") if len(messages) > 1 and isinstance(messages[1], dict) else None
            nested_source_id = user_content.get("source_id") if isinstance(user_content, dict) else None
            if isinstance(nested_source_id, str) and nested_source_id:
                source_ids.append(nested_source_id)
    deduped: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        if source_id in seen:
            continue
        seen.add(source_id)
        deduped.append(source_id)
    return deduped


def _infer_dataset_name(path: Path, source_ids: list[str]) -> str:
    lowered = str(path).replace("\\", "/").lower()
    if "/distill/" in lowered:
        parts = lowered.split("/distill/", 1)[1].split("/")
        if parts and parts[0]:
            return parts[0]
    if "rtlcoder_teacher" in lowered or "synthetic_bug" in lowered:
        return "rtlcoder_synthetic"
    if "verilog-eval" in lowered or "verilog_eval" in lowered or "verilog eval" in lowered:
        return "verilog_eval"
    if "rtlcoder" in lowered or any(source_id.startswith("rtlcoder_") for source_id in source_ids):
        return "rtlcoder_synthetic" if ("synthetic" in lowered or any("_synthetic_" in source_id for source_id in source_ids)) else "rtlcoder"
    if "verilog_eval" in lowered or any(source_id.lower().startswith("prob") for source_id in source_ids):
        return "verilog_eval"
    if "internal" in lowered:
        return "internal"
    return "unknown"


def _classify_role(
    relative_path: str,
    name: str,
    suffix: str,
    rows: list[dict[str, Any]],
    detected_schema: str,
    container_kind: str | None,
    batch_schema_version: str | None,
) -> str:
    lowered = relative_path.lower()
    report_suffix = suffix in {".json", ".md"}
    if report_suffix and "assembly" in name and "report" in name:
        return "assembly_report"
    if report_suffix and "repair" in name and "report" in name:
        return "repair_report"
    if report_suffix and any(marker in name for marker in ("validation", "readiness_report", "triage_report", "selection_report")):
        return "validation_report"
    if batch_schema_version == "rtl_answer_teacher_batch_v0.1":
        return "teacher_answer_batch"
    if detected_schema in ANSWER_SCHEMAS:
        if "repaired_rtl_answer_batches" in lowered or "/answers/repaired/" in lowered or "/repaired/" in lowered:
            return "repaired_answer_batch"
        if suffix == ".jsonl" and ("/answers/assembled/" in lowered or any(marker in name for marker in ("assembled", "clean")) or "/merged/" in lowered):
            return "assembled_answer_jsonl"
        if any(marker in lowered for marker in ("teacher_answer_returns", "teacher_returns")) or name.startswith("batch_"):
            return "teacher_answer_batch"
        if suffix == ".jsonl":
            return "assembled_answer_jsonl"
        return "teacher_answer_batch"
    if detected_schema in TASK_SCHEMAS:
        if "normalization_batches" in lowered and container_kind == "rows_wrapper":
            return "unknown"
        return "normalized_task"
    if detected_schema == "train/chat row with messages":
        if "/distill/" in lowered:
            return "distill_dataset"
        if "/eval/" in lowered and "prompt" in lowered:
            return "eval_prompt"
        if "/eval/" in lowered:
            return "eval_run"
        return "unknown"
    if "/eval/" in lowered and any(marker in lowered for marker in ("prompt", "prompts")):
        return "eval_prompt"
    if "/eval/" in lowered:
        return "eval_run"
    if lowered.startswith(".local_data/") or "/.local_data/" in lowered or "/raw/" in lowered or "raw_index" in name:
        return "raw_source"
    return "unknown"


def _likely_safe_to_archive(relative_path: str, role: str, duplicate_sha256: bool, source_overlap: bool) -> bool:
    lowered = relative_path.lower()
    if role in {"validation_report", "repair_report", "assembly_report"}:
        return True
    if duplicate_sha256:
        return True
    if lowered.startswith("review/") and role in {"teacher_answer_batch", "repaired_answer_batch", "assembled_answer_jsonl"}:
        return True
    if lowered.startswith("review/") and source_overlap:
        return True
    return False


def collect_data_workspace_inventory(
    *,
    data_dir: Path,
    output_md: Path | None = None,
    output_json: Path | None = None,
) -> tuple[dict[str, Any], int]:
    if not data_dir.exists():
        result = {
            "ok": False,
            "data_dir": str(data_dir),
            "errors": [f"data dir not found: {data_dir}"],
            "files_scanned": 0,
            "files": [],
        }
        if output_md:
            output_md.parent.mkdir(parents=True, exist_ok=True)
            output_md.write_text("# Data Workspace Inventory\n\n- OK: false\n- Error: data dir not found\n", encoding="utf-8")
        if output_json:
            _write_json(output_json, result)
        return result, 1

    file_entries: list[dict[str, Any]] = []
    sha_groups: dict[str, list[int]] = defaultdict(list)
    source_id_to_indexes: dict[str, list[int]] = defaultdict(list)
    role_counts: Counter[str] = Counter()
    schema_counts: Counter[str] = Counter()
    total_bytes = 0

    for path in sorted(candidate for candidate in data_dir.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(data_dir).as_posix()
        size_bytes = path.stat().st_size
        total_bytes += size_bytes
        suffix = path.suffix.lower()
        rows, container_kind, parse_error, batch_schema_version = _structured_rows(path)
        row_count = len(rows) if rows else None
        detected_schema = _detect_schema(rows)
        source_ids = _extract_source_ids(rows)
        dataset_name = _infer_dataset_name(path, source_ids)
        role = _classify_role(relative_path, path.name.lower(), suffix, rows, detected_schema, container_kind, batch_schema_version)
        sha256 = file_sha256(path)
        entry = {
            "path": relative_path,
            "size_bytes": size_bytes,
            "extension": suffix or "",
            "row_count": row_count,
            "detected_role": role,
            "detected_schema": detected_schema,
            "container_kind": container_kind,
            "batch_schema_version": batch_schema_version,
            "sha256": sha256,
            "duplicate_sha256": False,
            "duplicate_sha256_group_size": 0,
            "source_id_overlap": False,
            "source_id_overlap_count": 0,
            "source_id_count": len(source_ids),
            "source_id_samples": source_ids[:10],
            "likely_safe_to_archive": False,
            "dataset_name": dataset_name,
            "parse_error": parse_error,
        }
        role_counts[role] += 1
        schema_counts[detected_schema] += 1
        sha_groups[sha256].append(len(file_entries))
        for source_id in source_ids:
            source_id_to_indexes[source_id].append(len(file_entries))
        file_entries.append(entry)

    duplicate_groups: list[dict[str, Any]] = []
    for sha256, indexes in sorted(sha_groups.items()):
        if len(indexes) < 2:
            continue
        duplicate_groups.append({
            "sha256": sha256,
            "file_count": len(indexes),
            "paths": [file_entries[index]["path"] for index in indexes],
        })
        for index in indexes:
            file_entries[index]["duplicate_sha256"] = True
            file_entries[index]["duplicate_sha256_group_size"] = len(indexes)

    overlapping_source_ids = 0
    overlapping_files: set[int] = set()
    overlap_samples: dict[int, list[str]] = defaultdict(list)
    for source_id, indexes in source_id_to_indexes.items():
        unique_indexes = sorted(set(indexes))
        if len(unique_indexes) < 2:
            continue
        overlapping_source_ids += 1
        for index in unique_indexes:
            file_entries[index]["source_id_overlap"] = True
            file_entries[index]["source_id_overlap_count"] += 1
            overlapping_files.add(index)
            if len(overlap_samples[index]) < 10:
                overlap_samples[index].append(source_id)

    for index, entry in enumerate(file_entries):
        entry["overlap_source_id_samples"] = overlap_samples.get(index, [])
        entry["likely_safe_to_archive"] = _likely_safe_to_archive(
            entry["path"],
            entry["detected_role"],
            entry["duplicate_sha256"],
            entry["source_id_overlap"],
        )

    task_file_count = sum(1 for entry in file_entries if entry["detected_role"] == "normalized_task")
    answer_file_count = sum(
        1
        for entry in file_entries
        if entry["detected_role"] in {"teacher_answer_batch", "repaired_answer_batch", "assembled_answer_jsonl"}
    )
    report_file_count = sum(
        1
        for entry in file_entries
        if entry["detected_role"] in {"validation_report", "repair_report", "assembly_report"}
    )
    result = {
        "ok": True,
        "created_by": "inventory_data_workspace",
        "data_dir": str(data_dir),
        "files_scanned": len(file_entries),
        "total_bytes": total_bytes,
        "task_file_count": task_file_count,
        "answer_file_count": answer_file_count,
        "report_file_count": report_file_count,
        "unknown_file_count": sum(1 for entry in file_entries if entry["detected_role"] == "unknown"),
        "duplicate_file_count": sum(1 for entry in file_entries if entry["duplicate_sha256"]),
        "duplicate_sha256_group_count": len(duplicate_groups),
        "overlapping_source_id_file_count": len(overlapping_files),
        "overlapping_source_id_count": overlapping_source_ids,
        "likely_safe_to_archive_count": sum(1 for entry in file_entries if entry["likely_safe_to_archive"]),
        "role_counts": {role: role_counts.get(role, 0) for role in ROLE_ORDER if role_counts.get(role, 0)},
        "schema_counts": dict(sorted(schema_counts.items())),
        "duplicate_groups": duplicate_groups,
        "files": file_entries,
    }
    if output_json:
        _write_json(output_json, result)
    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_inventory_markdown(result), encoding="utf-8", newline="\n")
    return result, 0


def _inventory_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Data Workspace Inventory",
        "",
        "## Summary",
        "",
        f"- OK: {str(result['ok']).lower()}",
        f"- Files scanned: {result['files_scanned']}",
        f"- Total bytes: {result['total_bytes']}",
        f"- Detected task files: {result['task_file_count']}",
        f"- Detected answer files: {result['answer_file_count']}",
        f"- Detected reports: {result['report_file_count']}",
        f"- Duplicate files: {result['duplicate_file_count']}",
        f"- Overlapping source-id files: {result['overlapping_source_id_file_count']}",
        f"- Likely safe to archive: {result['likely_safe_to_archive_count']}",
        f"- Unknown files: {result['unknown_file_count']}",
        "",
        "## Roles",
        "",
    ]
    if result["role_counts"]:
        lines.extend(f"- `{role}`: {count}" for role, count in result["role_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Duplicate SHA256 Groups", ""])
    if result["duplicate_groups"]:
        for group in result["duplicate_groups"][:20]:
            lines.append(f"- `{group['sha256']}` ({group['file_count']} files)")
            lines.extend(f"  - `{path}`" for path in group["paths"][:5])
    else:
        lines.append("- none")
    lines.extend(["", "## Sample Files", ""])
    for entry in result["files"][:20]:
        lines.append(
            f"- `{entry['path']}`: role={entry['detected_role']}, schema={entry['detected_schema']}, "
            f"rows={entry['row_count']}, duplicate={str(entry['duplicate_sha256']).lower()}, "
            f"overlap={str(entry['source_id_overlap']).lower()}, archive={str(entry['likely_safe_to_archive']).lower()}"
        )
    return "\n".join(lines) + "\n"


def _load_inventory(inventory_json: Path) -> dict[str, Any]:
    payload, error = _safe_read_json(inventory_json)
    if error or not isinstance(payload, dict):
        raise ValueError(f"could not read inventory JSON: {error or 'payload must be a JSON object'}")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("inventory JSON must contain a files array")
    return payload


def _is_under(relative_path: str, prefix: str) -> bool:
    return relative_path == prefix or relative_path.startswith(f"{prefix}/")


def _root_dataset_folder(dataset_name: str) -> str:
    if dataset_name.startswith("rtlcoder"):
        return "rtlcoder"
    if dataset_name.startswith("verilog_eval"):
        return "verilog_eval"
    if dataset_name == "internal":
        return "internal"
    return "internal"


def _ensure_unique_destination(relative_path: str, sha256: str, occupied: set[str]) -> str:
    candidate = relative_path
    target = Path(candidate)
    if candidate not in occupied:
        occupied.add(candidate)
        return candidate
    suffix = f"__{sha256[:8]}"
    candidate = target.with_name(f"{target.stem}{suffix}{target.suffix}").as_posix()
    counter = 1
    while candidate in occupied:
        candidate = target.with_name(f"{target.stem}{suffix}_{counter}{target.suffix}").as_posix()
        counter += 1
    occupied.add(candidate)
    return candidate


def _archive_destination(relative_path: str) -> str:
    if relative_path.startswith("review/"):
        return f"archive/old_review_outputs/{relative_path[len('review/'):]}"
    return f"archive/old_review_outputs/{relative_path}"


def _propose_move(entry: dict[str, Any]) -> dict[str, Any] | None:
    path = entry["path"]
    role = entry["detected_role"]
    dataset_name = entry.get("dataset_name") or "unknown"
    filename = Path(path).name
    if _is_under(path, "golden") or filename == ".gitkeep" or path == "README.md":
        return None

    if role == "normalized_task":
        if _is_under(path, "normalized/tasks"):
            return None
        return {
            "new_path": f"normalized/tasks/{filename}",
            "reason": "canonical normalized task rows belong under data/normalized/tasks",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "raw_source":
        dataset_folder = _root_dataset_folder(dataset_name)
        if _is_under(path, f"raw/{dataset_folder}"):
            return None
        if path.startswith(".local_data/"):
            return {
                "new_path": f"raw/{dataset_folder}/{path[len('.local_data/') :]}",
                "reason": "raw source trees are safer under data/raw/<dataset>/ while staying unedited",
                "confidence": "medium",
                "manual_review_needed": False,
            }
        return {
            "new_path": f"raw/{dataset_folder}/{filename}",
            "reason": "raw-stage JSON and source material belong under data/raw/<dataset>/",
            "confidence": "high" if "raw_index" in filename else "medium",
            "manual_review_needed": False,
        }
    if role == "teacher_answer_batch":
        dataset_folder = dataset_name if dataset_name != "unknown" else "unknown_dataset"
        if _is_under(path, f"answers/teacher_returns/{dataset_folder}"):
            return None
        confidence = "high" if entry["detected_schema"] in ANSWER_SCHEMAS else "medium"
        return {
            "new_path": f"answers/teacher_returns/{dataset_folder}/{filename}",
            "reason": "teacher batch inputs/returns are easier to track under data/answers/teacher_returns/<dataset>/",
            "confidence": confidence,
            "manual_review_needed": entry["detected_schema"] not in ANSWER_SCHEMAS,
        }
    if role == "repaired_answer_batch":
        dataset_folder = dataset_name if dataset_name != "unknown" else "unknown_dataset"
        if _is_under(path, f"answers/repaired/{dataset_folder}"):
            return None
        return {
            "new_path": f"answers/repaired/{dataset_folder}/{filename}",
            "reason": "repaired answer copies belong under data/answers/repaired/<dataset>/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "assembled_answer_jsonl":
        if _is_under(path, "answers/assembled"):
            return None
        return {
            "new_path": f"answers/assembled/{filename}",
            "reason": "assembled answer JSONL files belong under data/answers/assembled/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "distill_dataset":
        dataset_folder = dataset_name if dataset_name != "unknown" else "unknown_dataset"
        if _is_under(path, f"distill/{dataset_folder}"):
            return None
        return {
            "new_path": f"distill/{dataset_folder}/{filename}",
            "reason": "teacher-distill packaging outputs belong under data/distill/<dataset>/",
            "confidence": "high" if path.startswith("distill/") else "medium",
            "manual_review_needed": not path.startswith("distill/"),
        }
    if role == "validation_report":
        if _is_under(path, "reports/validation"):
            return None
        return {
            "new_path": f"reports/validation/{filename}",
            "reason": "validation-style reports belong under data/reports/validation/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "repair_report":
        if _is_under(path, "reports/repair"):
            return None
        return {
            "new_path": f"reports/repair/{filename}",
            "reason": "repair reports belong under data/reports/repair/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "assembly_report":
        if _is_under(path, "reports/assembly"):
            return None
        return {
            "new_path": f"reports/assembly/{filename}",
            "reason": "assembly reports belong under data/reports/assembly/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "eval_prompt":
        if _is_under(path, "eval/prompts"):
            return None
        return {
            "new_path": f"eval/prompts/{filename}",
            "reason": "evaluation prompt exports belong under data/eval/prompts/",
            "confidence": "high",
            "manual_review_needed": False,
        }
    if role == "eval_run":
        if _is_under(path, "eval/runs") or _is_under(path, "eval/comparisons"):
            return None
        target_dir = "eval/comparisons" if "comparison" in path.lower() else "eval/runs"
        return {
            "new_path": f"{target_dir}/{filename}",
            "reason": "evaluation outputs belong under data/eval/runs/ or data/eval/comparisons/",
            "confidence": "medium",
            "manual_review_needed": False,
        }
    if entry.get("likely_safe_to_archive"):
        return {
            "new_path": _archive_destination(path),
            "reason": "legacy overlapping review outputs are safer under data/archive/old_review_outputs/",
            "confidence": "low",
            "manual_review_needed": True,
        }
    return None


def plan_data_workspace_cleanup(
    *,
    data_dir: Path,
    inventory_json: Path,
    plan_md: Path | None = None,
    plan_json: Path | None = None,
    apply: bool = False,
    dry_run: bool = True,
    include_medium_confidence: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        inventory = _load_inventory(inventory_json)
    except ValueError as exc:
        result = {
            "ok": False,
            "created_by": "plan_data_workspace_cleanup",
            "data_dir": str(data_dir),
            "errors": [str(exc)],
            "proposed_moves": [],
        }
        if plan_json:
            _write_json(plan_json, result)
        if plan_md:
            plan_md.parent.mkdir(parents=True, exist_ok=True)
            plan_md.write_text("# Data Workspace Cleanup Plan\n\n- OK: false\n", encoding="utf-8")
        return result, 1

    occupied = {entry["path"] for entry in inventory["files"]}
    proposed_moves: list[dict[str, Any]] = []
    for entry in inventory["files"]:
        proposal = _propose_move(entry)
        if proposal is None:
            continue
        new_path = _ensure_unique_destination(proposal["new_path"], entry["sha256"], occupied)
        manual_review_needed = proposal["manual_review_needed"] or new_path != proposal["new_path"]
        proposed_moves.append({
            "old_path": entry["path"],
            "new_path": new_path,
            "reason": proposal["reason"],
            "confidence": proposal["confidence"],
            "manual_review_needed": manual_review_needed,
            "detected_role": entry["detected_role"],
            "detected_schema": entry["detected_schema"],
            "dataset_name": entry.get("dataset_name"),
            "sha256": entry["sha256"],
            "apply_eligible": False,
            "applied": False,
            "status": "planned",
        })

    apply_high = apply and not dry_run
    moved_count = 0
    skipped_for_confidence = 0
    skipped_for_manual_review = 0
    for move in proposed_moves:
        confidence_rank = CONFIDENCE_RANK[move["confidence"]]
        eligible = confidence_rank >= CONFIDENCE_RANK["high"] or (include_medium_confidence and confidence_rank >= CONFIDENCE_RANK["medium"])
        eligible = eligible and not move["manual_review_needed"]
        move["apply_eligible"] = eligible
        if not apply_high:
            continue
        if not eligible:
            move["status"] = "planned_only"
            if move["manual_review_needed"]:
                skipped_for_manual_review += 1
            else:
                skipped_for_confidence += 1
            continue
        source = data_dir / Path(move["old_path"])
        destination = data_dir / Path(move["new_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        move["applied"] = True
        move["status"] = "moved"
        moved_count += 1

    result = {
        "ok": True,
        "created_by": "plan_data_workspace_cleanup",
        "data_dir": str(data_dir),
        "inventory_json": str(inventory_json),
        "apply_requested": apply,
        "dry_run": dry_run or not apply,
        "include_medium_confidence": include_medium_confidence,
        "proposed_move_count": len(proposed_moves),
        "high_confidence_move_count": sum(1 for move in proposed_moves if move["confidence"] == "high"),
        "medium_confidence_move_count": sum(1 for move in proposed_moves if move["confidence"] == "medium"),
        "low_confidence_move_count": sum(1 for move in proposed_moves if move["confidence"] == "low"),
        "manual_review_move_count": sum(1 for move in proposed_moves if move["manual_review_needed"]),
        "apply_eligible_move_count": sum(1 for move in proposed_moves if move["apply_eligible"]),
        "applied_move_count": moved_count,
        "skipped_for_confidence_count": skipped_for_confidence,
        "skipped_for_manual_review_count": skipped_for_manual_review,
        "proposed_moves": proposed_moves,
    }
    markdown = _cleanup_plan_markdown(result)
    if plan_json:
        _write_json(plan_json, result)
    elif apply_high:
        _write_json(DEFAULT_APPLIED_JSON, result)
    if plan_md:
        plan_md.parent.mkdir(parents=True, exist_ok=True)
        plan_md.write_text(markdown, encoding="utf-8", newline="\n")
    elif apply_high:
        DEFAULT_APPLIED_MD.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_APPLIED_MD.write_text(markdown, encoding="utf-8", newline="\n")
    return result, 0


def _cleanup_plan_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Data Workspace Cleanup Plan",
        "",
        "## Summary",
        "",
        f"- OK: {str(result['ok']).lower()}",
        f"- Apply requested: {str(result['apply_requested']).lower()}",
        f"- Dry run: {str(result['dry_run']).lower()}",
        f"- Proposed moves: {result['proposed_move_count']}",
        f"- High-confidence moves: {result['high_confidence_move_count']}",
        f"- Medium-confidence moves: {result['medium_confidence_move_count']}",
        f"- Low-confidence moves: {result['low_confidence_move_count']}",
        f"- Manual-review moves: {result['manual_review_move_count']}",
        f"- Apply-eligible moves: {result['apply_eligible_move_count']}",
        f"- Applied moves: {result['applied_move_count']}",
        "",
        "## Moves",
        "",
    ]
    if result["proposed_moves"]:
        for move in result["proposed_moves"][:200]:
            lines.append(
                f"- `{move['old_path']}` -> `{move['new_path']}` "
                f"[{move['confidence']}, manual_review={str(move['manual_review_needed']).lower()}, status={move['status']}]"
            )
            lines.append(f"  - {move['reason']}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"
