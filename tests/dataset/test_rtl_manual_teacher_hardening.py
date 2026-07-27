from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

import pytest
import jsonschema

import scripts.dataset.rtl_manual_teacher_verification as verification
from scripts.dataset.rtl_manual_teacher_verification import (
    export_teacher_repair_packets,
    ingest_candidate_evidence,
    prepare_candidate_verification,
    validate_teacher_candidate_batch,
)
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT, evidence_for_plan, initial_flow


def _packet(tmp_path: Path) -> Path:
    result, code = verification.export_teacher_generation_packets(
        FIXTURE_ROOT / "generation_tasks.jsonl", tmp_path / "packets"
    )
    assert code == 0, result
    return tmp_path / "packets/packet_0001.json"


def _private_kwargs() -> dict[str, Path]:
    return {
        "private_assets_path": FIXTURE_ROOT / "verification_assets.jsonl",
        "private_assets_root": FIXTURE_ROOT / "private_assets",
    }


def test_packet_id_tampering_is_rejected(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    value = json.loads(packet.read_text(encoding="utf-8"))
    value["packet_id"] = value["packet_id"][:-1] + ("0" if value["packet_id"][-1] != "0" else "1")
    packet.write_text(json.dumps(value), encoding="utf-8")
    result, code = validate_teacher_candidate_batch(
        packet, FIXTURE_ROOT / "responses/valid_initial_response.json", **_private_kwargs()
    )
    assert code != 0
    assert "packet_id" in result["errors"][0]


def test_private_assets_are_required_even_for_direct_api_calls(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    result, code = validate_teacher_candidate_batch(
        packet, FIXTURE_ROOT / "responses/valid_initial_response.json"
    )
    assert code != 0
    assert "private-assets" in result["errors"][0]


def test_missing_nested_output_parent_is_created_and_stage_shares_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, candidates, _ = initial_flow(tmp_path)
    output = tmp_path / "missing" / "nested" / "run_001"
    original_mkdtemp = verification.tempfile.mkdtemp
    observed: dict[str, Path] = {}

    def capture_mkdtemp(*args, **kwargs):
        observed["dir"] = Path(kwargs["dir"])
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(verification.tempfile, "mkdtemp", capture_mkdtemp)
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, output,
    )
    assert code == 0, result
    assert output.is_dir()
    assert observed["dir"] == output.parent


def test_symlinked_or_file_output_parent_is_rejected(tmp_path: Path) -> None:
    _, candidates, _ = initial_flow(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    symlink_parent = tmp_path / "symlink-parent"
    try:
        symlink_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, symlink_parent / "run",
    )
    assert code != 0
    assert "symlink" in result["errors"][0]
    file_parent = tmp_path / "file-parent"
    file_parent.write_text("not a directory", encoding="utf-8")
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, file_parent / "run",
    )
    assert code != 0
    assert "not a directory" in result["errors"][0]


def test_failed_publication_restores_managed_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, candidates, output = initial_flow(tmp_path)
    before = (output / "verification_plan.jsonl").read_bytes()
    original_replace = verification.os.replace

    def fail_stage_publish(source, destination):
        if Path(source).name.startswith(f".{output.name}.stage-"):
            raise OSError("synthetic publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(verification.os, "replace", fail_stage_publish)
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, output, overwrite=True,
    )
    assert code != 0
    assert (output / "verification_plan.jsonl").read_bytes() == before
    assert not list(output.parent.glob(f".{output.name}.backup-*"))


def test_selection_filters_after_complete_validation_and_rejects_bool(tmp_path: Path) -> None:
    _, candidates, _ = initial_flow(tmp_path)
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, tmp_path / "selected", attempt=True,
    )
    assert code != 0
    assert "1..4" in result["errors"][0]
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, tmp_path / "unknown", candidate_ids=["does_not_exist"],
    )
    assert code != 0
    assert "unknown" in result["errors"][0]


def test_unknown_existing_handoff_output_is_rejected(tmp_path: Path) -> None:
    _, candidates, _ = initial_flow(tmp_path)
    output = tmp_path / "managed"
    output.mkdir()
    (output / "caller-owned.txt").write_text("keep", encoding="utf-8")
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidates, output, overwrite=True,
    )
    assert code != 0
    assert "unknown" in result["errors"][0]


def test_candidate_append_uses_global_task_attempt_candidate_order(tmp_path: Path) -> None:
    source_task = json.loads((FIXTURE_ROOT / "generation_tasks.jsonl").read_text(encoding="utf-8"))
    task_b = json.loads(json.dumps(source_task))
    task_a = json.loads(json.dumps(source_task))
    task_b["task_id"], task_b["source_id"] = "rtlgen_b", "SyntheticB"
    task_a["task_id"], task_a["source_id"] = "rtlgen_a", "SyntheticA"
    tasks = tmp_path / "two-tasks.jsonl"
    tasks.write_text(json.dumps(task_b) + "\n" + json.dumps(task_a) + "\n", encoding="utf-8")
    source_asset = json.loads((FIXTURE_ROOT / "verification_assets.jsonl").read_text(encoding="utf-8"))
    asset_b = json.loads(json.dumps(source_asset))
    asset_a = json.loads(json.dumps(source_asset))
    assets = tmp_path / "two-assets.jsonl"
    private_root = tmp_path / "private-assets"
    for asset, task in ((asset_b, task_b), (asset_a, task_a)):
        asset["task_id"], asset["source_id"] = task["task_id"], task["source_id"]
        asset["input_hashes"]["source_prompt_sha256"] = hashlib.sha256(task["specification"].encode()).hexdigest()
        asset["reference_rtl_path"] = asset["reference_rtl_path"].replace("rtlgen_synthetic_example", task["task_id"])
        asset["testbench_path"] = asset["testbench_path"].replace("rtlgen_synthetic_example", task["task_id"])
        asset["support_files"][0] = asset["support_files"][0].replace("rtlgen_synthetic_example", task["task_id"])
        asset["input_hashes"]["support_files"][0]["path"] = asset["input_hashes"]["support_files"][0]["path"].replace("rtlgen_synthetic_example", task["task_id"])
        for relative in (asset["reference_rtl_path"], asset["testbench_path"], asset["support_files"][0]):
            target = private_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((FIXTURE_ROOT / "private_assets" / relative.replace(task["task_id"], "rtlgen_synthetic_example")).read_bytes())
    assets.write_text(json.dumps(asset_b) + "\n" + json.dumps(asset_a) + "\n", encoding="utf-8")
    packets = tmp_path / "packets"
    result, code = verification.export_teacher_generation_packets(tasks, packets, batch_size=1)
    assert code == 0, result
    responses = {}
    for number, task in ((1, task_b), (2, task_a)):
        response = json.loads((FIXTURE_ROOT / "responses/valid_initial_response.json").read_text(encoding="utf-8"))
        response["rows"][0]["task_id"] = task["task_id"]
        responses[number] = tmp_path / f"response-{number}.json"
        responses[number].write_text(json.dumps(response), encoding="utf-8")

    def process(order: list[int], output: Path) -> list[dict]:
        for number in order:
            result, code = validate_teacher_candidate_batch(
                packets / f"packet_{number:04d}.json", responses[number],
                private_assets_path=assets, private_assets_root=private_root,
                output_path=output, append=output.exists(),
            )
            assert code == 0, result
        return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    first = process([1, 2], tmp_path / "first.jsonl")
    second = process([2, 1], tmp_path / "second.jsonl")
    assert [(row["task_id"], row["attempt"], row["candidate_id"]) for row in first] == [
        ("rtlgen_a", 1, "rtlgen_a_attempt_01"), ("rtlgen_b", 1, "rtlgen_b_attempt_01")
    ]
    assert first == second


def test_rtlbench_failure_category_fixtures_are_ingested(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    base = json.loads(evidence_for_plan(run).read_text(encoding="utf-8"))
    fixtures = {
        "internal_error": ({"attempted": True, "passed": False, "reason": "internal_error"}, {"attempted": False, "passed": None, "reason": "internal_error"}, "internal_error"),
        "partial_failure": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": False, "reason": "partial_failure"}, "partial_failure"),
        "tool_unavailable": ({"attempted": False, "passed": None, "reason": "tool_unavailable"}, {"attempted": False, "passed": None, "reason": "tool_unavailable"}, "tool_unavailable"),
        "compile_failure": ({"attempted": True, "passed": False, "reason": "compile_failure"}, {"attempted": False, "passed": None, "reason": "compile_failure"}, "compile_failure"),
        "simulation_compile_failure": ({"attempted": True, "passed": True, "reason": None}, {"attempted": False, "passed": None, "reason": "compile_failure"}, "compile_failure"),
        "functional_mismatch": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": False, "reason": "functional_mismatch"}, "functional_mismatch"),
        "simulation_result_missing": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": False, "reason": "simulation_result_missing"}, "simulation_result_missing"),
        "simulation_failure": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": False, "reason": "simulation_failure"}, "simulation_failure"),
        "timeout": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": False, "reason": "timeout"}, "timeout"),
        "accepted": ({"attempted": True, "passed": True, "reason": None}, {"attempted": True, "passed": True, "reason": None}, "passed"),
    }
    for name, (compile_status, simulation_status, category) in fixtures.items():
        value = json.loads(json.dumps(base))
        value["checks"]["compile"]["candidate"] = compile_status
        value["checks"]["simulation"]["candidate_passes"] = simulation_status
        value["failure_category"] = category
        value["accepted"] = name == "accepted"
        if name == "accepted":
            value["mismatch_summary"] = {"contract": "mismatch_count_v1", "reported_counts": [0], "reported_sample_counts": [20], "maximum_count": 0, "timeout_reported": False}
        elif category in {"functional_mismatch"}:
            value["mismatch_summary"] = {"contract": "mismatch_count_v1", "reported_counts": [1], "reported_sample_counts": [1], "maximum_count": 1, "timeout_reported": False}
        elif category == "timeout":
            value["mismatch_summary"] = {"contract": "mismatch_count_v1", "reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": True}
        else:
            value["mismatch_summary"] = {"contract": "mismatch_count_v1", "reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": False}
        evidence = tmp_path / f"{name}.jsonl"
        evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
        output = tmp_path / f"{name}-attempts.jsonl"
        result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
        assert code == 0, (name, result)
        assert json.loads(output.read_text(encoding="utf-8"))["failure_category"] == category


def test_process_timeout_and_testbench_timeout_marker_are_distinct(tmp_path: Path) -> None:
    _, candidates, run = initial_flow(tmp_path)
    base = json.loads(evidence_for_plan(run).read_text(encoding="utf-8"))

    def ingest_variant(name: str, compile_status: dict, simulation_status: dict, marker: bool, category: str):
        value = json.loads(json.dumps(base))
        value["checks"]["compile"]["candidate"] = compile_status
        value["checks"]["simulation"]["candidate_passes"] = simulation_status
        value["mismatch_summary"] = {
            "contract": "mismatch_count_v1", "reported_counts": [],
            "reported_sample_counts": [], "maximum_count": None,
            "timeout_reported": marker,
        }
        value["failure_category"] = category
        value["accepted"] = False
        evidence = tmp_path / f"{name}.jsonl"
        evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
        output = tmp_path / f"{name}-attempts.jsonl"
        result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
        assert code == 0, (name, result)
        return output

    marker_output = ingest_variant(
        "testbench-timeout",
        {"attempted": True, "passed": True, "reason": None},
        {"attempted": True, "passed": False, "reason": "timeout"},
        True,
        "timeout",
    )
    process_output = ingest_variant(
        "simulator-timeout",
        {"attempted": True, "passed": True, "reason": None},
        {"attempted": True, "passed": False, "reason": "timeout"},
        False,
        "timeout",
    )
    compiler_output = ingest_variant(
        "compiler-timeout",
        {"attempted": True, "passed": False, "reason": "timeout"},
        {"attempted": False, "passed": None, "reason": "compile_failure"},
        False,
        "timeout",
    )
    for output in (marker_output, process_output, compiler_output):
        attempt = json.loads(output.read_text(encoding="utf-8"))
        assert attempt["failure_category"] == "timeout"

    invalid = json.loads(json.dumps(base))
    invalid["checks"]["simulation"]["candidate_passes"] = {"attempted": True, "passed": False, "reason": "simulation_failure"}
    invalid["mismatch_summary"] = {
        "contract": "mismatch_count_v1", "reported_counts": [],
        "reported_sample_counts": [], "maximum_count": None,
        "timeout_reported": True,
    }
    invalid["failure_category"] = "simulation_failure"
    invalid["accepted"] = False
    invalid_path = tmp_path / "invalid-timeout-marker.jsonl"
    invalid_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    result, code = ingest_candidate_evidence(
        run / "verification_plan.jsonl", invalid_path, tmp_path / "invalid-timeout-marker.out"
    )
    assert code != 0

    repair_dir = tmp_path / "timeout-repair"
    result, code = export_teacher_repair_packets(
        FIXTURE_ROOT / "generation_tasks.jsonl", candidates, compiler_output, repair_dir
    )
    assert code == 0, result
    assert result["repairable_attempts"] == 1


def test_pending_and_impossible_final_leaves_are_rejected(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    base = json.loads(evidence_for_plan(run).read_text(encoding="utf-8"))
    mutations = []
    pending = json.loads(json.dumps(base))
    pending["checks"]["simulation"]["candidate_passes"] = {"attempted": False, "passed": None, "reason": "pending"}
    mutations.append(pending)
    optional = json.loads(json.dumps(base))
    optional["checks"]["lint"]["candidate"] = {"attempted": True, "passed": True, "reason": None}
    mutations.append(optional)
    accepted_counts = json.loads(json.dumps(base))
    accepted_counts["checks"]["simulation"]["candidate_passes"] = {"attempted": True, "passed": True, "reason": None}
    accepted_counts["accepted"] = True
    accepted_counts["failure_category"] = "passed"
    accepted_counts["mismatch_summary"] = {"contract": "mismatch_count_v1", "reported_counts": [1], "reported_sample_counts": [1], "maximum_count": 1, "timeout_reported": False}
    mutations.append(accepted_counts)
    for index, value in enumerate(mutations):
        evidence = tmp_path / f"invalid-{index}.jsonl"
        evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
        result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, tmp_path / f"invalid-{index}.out")
        assert code != 0


def test_append_history_is_sorted_and_acceptance_is_terminal(tmp_path: Path) -> None:
    _, candidates, run1 = initial_flow(tmp_path)
    attempts = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run1 / "verification_plan.jsonl", evidence_for_plan(run1), attempts)
    assert code == 0, result
    repairs = tmp_path / "repairs" / "attempt_02"
    result, code = export_teacher_repair_packets(FIXTURE_ROOT / "generation_tasks.jsonl", candidates, attempts, repairs)
    assert code == 0, result
    result, code = validate_teacher_candidate_batch(
        repairs / "packet_0001.json", FIXTURE_ROOT / "responses/valid_repair_response.json",
        output_path=candidates, append=True, **_private_kwargs(),
    )
    assert code == 0, result
    candidate2 = candidates
    run2 = tmp_path / "handoffs" / "run_002"
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl", FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets", candidate2, run2, attempt=2,
    )
    assert code == 0, result
    result, code = ingest_candidate_evidence(run2 / "verification_plan.jsonl", evidence_for_plan(run2, accepted=True), attempts, append=True)
    assert code == 0, result
    rows = [json.loads(line) for line in attempts.read_text(encoding="utf-8").splitlines()]
    assert [row["attempt"] for row in rows] == [1, 2]
    final_repairs = tmp_path / "repairs" / "attempt_03"
    result, code = export_teacher_repair_packets(FIXTURE_ROOT / "generation_tasks.jsonl", candidate2, attempts, final_repairs)
    assert code == 0, result
    assert result["repairable_attempts"] == 0
    public_artifacts = [
        tmp_path / "packets/packet_0001.json",
        tmp_path / "packets/packet_0001.md",
        tmp_path / "repairs/attempt_02/packet_0001.json",
        tmp_path / "repairs/attempt_02/packet_0001.md",
        candidates,
        attempts,
    ]
    for artifact in public_artifacts:
        text = artifact.read_text(encoding="utf-8")
        assert "reference.sv" not in text
        assert "testbench.sv" not in text
        assert "helper.svh" not in text
        assert ".local_data" not in text


def test_evidence_output_modes_and_aliases_fail_closed(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    output = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
    assert code == 0, result
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
    assert code != 0
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output, append=True, overwrite=True)
    assert code != 0
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output, overwrite=True)
    assert code == 0, result
    directory = tmp_path / "output-directory"
    directory.mkdir()
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, directory)
    assert code != 0
    alias, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, run / "verification_plan.jsonl")
    assert code != 0
    assert alias["errors"]
    linked = tmp_path / "hard-linked-output.jsonl"
    os.link(output, linked)
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, linked, overwrite=True)
    assert code != 0


def test_schema_documents_are_parseable_and_generation_attempt_is_not_unrestricted(tmp_path: Path) -> None:
    schemas = [Path(__file__).parents[2] / "schemas" / name for name in (
        "rtl_generation_attempt_v0.1.schema.json",
        "rtl_teacher_repair_packet_v0.1.schema.json",
        "rtl_candidate_verification_plan_v0.1.schema.json",
    )]
    assert schemas
    for path in schemas:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["$schema"]

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert "properties" in node or "$ref" in node
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)


def test_generation_attempt_status_schema_covers_all_contract_states() -> None:
    schema = json.loads(
        (Path(__file__).parents[2] / "schemas/rtl_generation_attempt_v0.1.schema.json").read_text(encoding="utf-8")
    )
    status_schema = schema["$defs"]["status"]

    def matches(value, rule):
        if "const" in rule and value != rule["const"]:
            return False
        if "enum" in rule and value not in rule["enum"]:
            return False
        if "type" in rule:
            expected = rule["type"]
            expected = expected if isinstance(expected, list) else [expected]
            actual = "null" if value is None else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string" if isinstance(value, str) else "object"
            if actual not in expected:
                return False
        return True

    def schema_accepts(value):
        if set(value) != {"attempted", "passed", "reason"}:
            return False
        return any(
            all(matches(value[name], rule) for name, rule in branch["properties"].items())
            for branch in status_schema["oneOf"]
        )

    valid = [
        {"attempted": True, "passed": True, "reason": None},
        *({"attempted": True, "passed": False, "reason": reason} for reason in (
            "compile_failure", "functional_mismatch", "simulation_result_missing",
            "simulation_failure", "timeout", "tool_unavailable", "internal_error", "partial_failure",
        )),
        *({"attempted": False, "passed": None, "reason": reason} for reason in (
            "not_requested", "tool_unavailable", "compile_failure", "internal_error",
        )),
    ]
    invalid = [
        {"attempted": False, "passed": True, "reason": "tool_unavailable"},
        {"attempted": True, "passed": None, "reason": "internal_error"},
        {"attempted": True, "passed": True, "reason": "compile_failure"},
        {"attempted": True, "passed": False, "reason": None},
        {"attempted": False, "passed": None, "reason": "pending"},
        {"attempted": False, "passed": None, "reason": None},
    ]
    assert all(schema_accepts(value) for value in valid)
    assert not any(schema_accepts(value) for value in invalid)


def test_generated_attempt_representatives_validate_against_committed_schema(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    accepted_evidence = evidence_for_plan(run, accepted=True)
    output = tmp_path / "accepted-attempt.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", accepted_evidence, output)
    assert code == 0, result
    accepted = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(
        (Path(__file__).parents[2] / "schemas/rtl_generation_attempt_v0.1.schema.json").read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    representatives = [
        accepted,
        {**accepted, "mismatch_summary": {**accepted["mismatch_summary"], "reported_sample_counts": [None]}},
        {**accepted, "accepted": False, "failure_category": "functional_mismatch", "checks": {**accepted["checks"], "simulation": {"candidate_passes": {"attempted": True, "passed": False, "reason": "functional_mismatch"}}}, "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [1], "reported_sample_counts": [20], "maximum_count": 1, "timeout_reported": False}},
        {**accepted, "accepted": False, "failure_category": "timeout", "checks": {**accepted["checks"], "simulation": {"candidate_passes": {"attempted": True, "passed": False, "reason": "timeout"}}}, "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": False}},
        {**accepted, "accepted": False, "failure_category": "internal_error", "checks": {**accepted["checks"], "compile": {"candidate": {"attempted": True, "passed": False, "reason": "internal_error"}}, "simulation": {"candidate_passes": {"attempted": False, "passed": None, "reason": "internal_error"}}}, "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": False}},
        {**accepted, "accepted": False, "failure_category": "tool_unavailable", "checks": {**accepted["checks"], "compile": {"candidate": {"attempted": False, "passed": None, "reason": "tool_unavailable"}}, "simulation": {"candidate_passes": {"attempted": False, "passed": None, "reason": "tool_unavailable"}}}, "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": False}},
    ]
    for value in representatives:
        errors = list(validator.iter_errors(value))
        assert not errors, errors
    malformed = {**accepted, "checks": {**accepted["checks"], "compile": {"candidate": {"attempted": True, "passed": None, "reason": "compile_failure"}}}}
    assert list(validator.iter_errors(malformed))
