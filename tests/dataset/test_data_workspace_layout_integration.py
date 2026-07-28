from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from jsonschema import validate

from scripts.dataset.data_workspace_layout import (
    build_inventory,
    initialize_manual_rtl_run,
    migrate_legacy_rtl_data_workspace,
    validate_manual_rtl_run,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "data_workspace_layout" / "legacy"
SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def test_inventory_migrate_initialize_validate_inventory_integration(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    golden_before = _hash_tree(data / "golden")
    sources_before = _hash_tree(data / ".local_data")

    initial = build_inventory(data)
    dry = migrate_legacy_rtl_data_workspace(data, "pilot_001")
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    applied = migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)
    valid, code = validate_manual_rtl_run(data / "runs/manual_rtl_teacher/pilot_001")
    final = build_inventory(data)

    validate(instance=initial, schema=__import__("json").loads((SCHEMA_ROOT / "data_workspace_inventory_v0.1.schema.json").read_text(encoding="utf-8")))
    validate(instance=dry, schema=__import__("json").loads((SCHEMA_ROOT / "data_workspace_migration_plan_v0.1.schema.json").read_text(encoding="utf-8")))
    validate(instance=applied, schema=__import__("json").loads((SCHEMA_ROOT / "data_workspace_migration_plan_v0.1.schema.json").read_text(encoding="utf-8")))
    validate(instance=json.loads((data / "runs/manual_rtl_teacher/pilot_001/run_manifest.json").read_text(encoding="utf-8")), schema=json.loads((SCHEMA_ROOT / "manual_rtl_run_manifest_v0.1.schema.json").read_text(encoding="utf-8")))
    assert code == 0, valid
    assert initial["summary"]["unknown_entry_count"] >= 1
    assert dry["mappings"]
    assert final["summary"]["raw_source_bytes"] >= initial["summary"]["raw_source_bytes"]
    assert _hash_tree(data / "golden") == golden_before
    assert _hash_tree(data / ".local_data") == sources_before
    assert all("/home/" not in path for path in dry["unknown_paths"])
