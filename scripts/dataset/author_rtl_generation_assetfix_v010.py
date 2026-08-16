#!/usr/bin/env python3
"""Author the two-task assetfix_v010 retry from public specifications only.

The overlay repairs the public verification contract for Prob149 and the
positive FSM fixture for Prob139.  It never reads reference RTL or an
upstream testbench, and it never executes HDL, RTLBench, Docker, or an
external model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import textwrap
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


CORRECTION_VERSION = "assetfix_v010"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SOURCE_IDS = ("Prob149_ece241_2013_q4", "Prob139_2013_q2bfsm")
MUTATION_NAMES = {
    "Prob139_2013_q2bfsm": ("missing_reset", "wrong_sequence_timing"),
    "Prob149_ece241_2013_q4": ("no_dfr", "wrong_flow_levels"),
}
AUTHORING_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"
MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.2"

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
FIXTURE_MARKERS = PRIVATE_MARKERS + (
    "`include",
    "testbench",
    "mismatches:",
    "$finish",
    "$display",
    "package ",
    "interface ",
)
MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)


def _sv(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


PUBLIC_TESTBENCHES = {
    "Prob139_2013_q2bfsm": _sv(
        """
        module tb;
          integer mismatch_count;
          reg clk, resetn, x, y;
          wire f, g;
          TopModule dut (.clk(clk), .resetn(resetn), .x(x), .y(y), .f(f), .g(g));
          always #5 clk = ~clk;
          initial begin
            mismatch_count = 0;
            clk = 0;
            resetn = 0;
            x = 0;
            y = 0;
            @(posedge clk); #1;
            if (f !== 0 || g !== 0) mismatch_count = mismatch_count + 1;
            resetn = 1;
            @(posedge clk); #1;
            if (f !== 1 || g !== 0) mismatch_count = mismatch_count + 1;
            @(posedge clk); #1;
            if (f !== 0 || g !== 0) mismatch_count = mismatch_count + 1;
            x = 1; @(posedge clk); #1;
            x = 0; @(posedge clk); #1;
            x = 1; @(posedge clk); #1;
            if (g !== 1) mismatch_count = mismatch_count + 1;
            y = 1; @(posedge clk); #1;
            if (g !== 1) mismatch_count = mismatch_count + 1;
            y = 0; @(posedge clk); #1;
            if (g !== 1) mismatch_count = mismatch_count + 1;
            $display("Mismatches: %0d", mismatch_count);
            $finish;
          end
        endmodule
        """
    ),
    "Prob149_ece241_2013_q4": _sv(
        """
        module tb;
          integer mismatch_count;
          reg clk, reset;
          reg [3:1] s;
          wire fr3, fr2, fr1, dfr;
          task automatic sample;
            input [3:1] value;
            input [2:0] expected_flow;
            input expected_dfr;
            begin
              s = value;
              @(posedge clk); #1;
              if ({fr3, fr2, fr1} !== expected_flow || dfr !== expected_dfr)
                mismatch_count = mismatch_count + 1;
            end
          endtask
          TopModule dut (.clk(clk), .reset(reset), .s(s), .fr3(fr3), .fr2(fr2), .fr1(fr1), .dfr(dfr));
          always #5 clk = ~clk;
          initial begin
            mismatch_count = 0;
            clk = 0;
            reset = 1;
            s = 3'b000;
            sample(3'b000, 3'b111, 1'b1);
            reset = 0;
            sample(3'b111, 3'b000, 1'b1);
            sample(3'b011, 3'b001, 1'b0);
            sample(3'b001, 3'b011, 1'b0);
            sample(3'b000, 3'b111, 1'b0);
            sample(3'b001, 3'b011, 1'b1);
            sample(3'b011, 3'b001, 1'b1);
            $display("Mismatches: %0d", mismatch_count);
            $finish;
          end
        endmodule
        """
    ),
}


PUBLIC_FIXTURES = {
    "Prob139_2013_q2bfsm": {
        "positive": _sv(
            """
            module TopModule(input logic clk, input logic resetn, input logic x, input logic y, output logic f, output logic g);
              logic [3:0] state;
              localparam A=0, FP=1, MONITOR=2, X1=3, X10=4, G1=5, G2=6, ON=7, OFF=8;
              always @(posedge clk) begin
                if (!resetn) state <= A;
                else case (state)
                  A: state <= FP;
                  FP: state <= MONITOR;
                  MONITOR: state <= x ? X1 : MONITOR;
                  X1: state <= x ? X1 : X10;
                  X10: state <= x ? G1 : MONITOR;
                  G1: state <= y ? ON : G2;
                  G2: state <= y ? ON : OFF;
                  ON: state <= ON;
                  default: state <= OFF;
                endcase
              end
              assign f = (state == FP);
              assign g = (state == G1) || (state == G2) || (state == ON);
            endmodule
            """
        ),
        "missing_reset": _sv(
            """
            module TopModule(input logic clk, input logic resetn, input logic x, input logic y, output logic f, output logic g);
              logic [3:0] state;
              always @(posedge clk) state <= state + 1'b1;
              assign f = 1'b0;
              assign g = 1'b0;
            endmodule
            """
        ),
        "wrong_sequence_timing": _sv(
            """
            module TopModule(input logic clk, input logic resetn, input logic x, input logic y, output logic f, output logic g);
              logic [1:0] count;
              always @(posedge clk) begin
                if (!resetn) count <= 0;
                else count <= count + 1'b1;
              end
              assign f = 1'b0;
              assign g = (count == 2);
            endmodule
            """
        ),
    },
    "Prob149_ece241_2013_q4": {
        "positive": _sv(
            """
            module TopModule(input logic clk, input logic reset, input logic [3:1] s, output logic fr3, output logic fr2, output logic fr1, output logic dfr);
              logic [3:1] previous_s;
              always_comb begin
                case (s)
                  3'b111: {fr3, fr2, fr1} = 3'b000;
                  3'b011: {fr3, fr2, fr1} = 3'b001;
                  3'b001: {fr3, fr2, fr1} = 3'b011;
                  default: {fr3, fr2, fr1} = 3'b111;
                endcase
              end
              always @(posedge clk) begin
                if (reset) begin
                  previous_s <= 3'b000;
                  dfr <= 1'b1;
                end else begin
                  dfr <= (s > previous_s);
                  previous_s <= s;
                end
              end
            endmodule
            """
        ),
        "no_dfr": _sv(
            """
            module TopModule(input logic clk, input logic reset, input logic [3:1] s, output logic fr3, output logic fr2, output logic fr1, output logic dfr);
              always_comb begin
                case (s)
                  3'b111: {fr3, fr2, fr1} = 3'b000;
                  3'b011: {fr3, fr2, fr1} = 3'b001;
                  3'b001: {fr3, fr2, fr1} = 3'b011;
                  default: {fr3, fr2, fr1} = 3'b111;
                endcase
              end
              assign dfr = 1'b0;
            endmodule
            """
        ),
        "wrong_flow_levels": _sv(
            """
            module TopModule(input logic clk, input logic reset, input logic [3:1] s, output logic fr3, output logic fr2, output logic fr1, output logic dfr);
              assign fr3 = s[3];
              assign fr2 = s[2];
              assign fr1 = s[1];
              assign dfr = 1'b0;
            endmodule
            """
        ),
    },
}


class AuthoringError(ValueError):
    """Raised when an immutable authoring input is invalid."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise AuthoringError(f"missing or symlinked JSON input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise AuthoringError(f"missing or symlinked JSONL input: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_exclusive(path: Path, content: bytes) -> str:
    if path.exists() or path.is_symlink():
        raise AuthoringError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _validate_public_content(source_id: str) -> None:
    audit, errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
    if errors or audit.get("support_file_count") != 0:
        raise AuthoringError(f"testbench static audit failed for {source_id}: {errors}")
    if tuple(PUBLIC_FIXTURES[source_id]) != ("positive", *MUTATION_NAMES[source_id]):
        raise AuthoringError(f"fixture order mismatch for {source_id}")
    for name, content in PUBLIC_FIXTURES[source_id].items():
        lowered = content.casefold()
        for marker in FIXTURE_MARKERS:
            if marker.casefold() in lowered:
                raise AuthoringError(f"forbidden fixture marker {marker}: {source_id}:{name}")
        if MODULE_RE.findall(content) != ["TopModule"]:
            raise AuthoringError(f"fixture must declare only TopModule: {source_id}:{name}")


def author(*, correction_root: Path, inventory_path: Path, selection_path: Path, ids_path: Path, authorization_path: Path) -> dict[str, Any]:
    if correction_root.exists() or correction_root.is_symlink():
        raise AuthoringError(f"refusing to replace existing correction root: {correction_root}")
    inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
    selection = _load_json(selection_path)
    authorization = _load_json(authorization_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if ids != list(SOURCE_IDS):
        raise AuthoringError("retry IDs do not match the pinned order")
    if selection.get("ok") is not True or selection.get("selection_kind") != "qualification_retry":
        raise AuthoringError("selection is not a successful qualification retry")
    if selection.get("correction_version") != CORRECTION_VERSION:
        raise AuthoringError("selection correction version mismatch")
    if selection.get("selection_ids_sha256") != sha256_file(ids_path):
        raise AuthoringError("selection ID hash mismatch")
    if authorization.get("status") != "prepared_pending_isolated_execution_authorization":
        raise AuthoringError("retry is not in the prepared state")
    if authorization.get("selection_report_sha256") != sha256_file(selection_path):
        raise AuthoringError("authorization selection hash mismatch")
    if authorization.get("selection_ids_sha256") != sha256_file(ids_path):
        raise AuthoringError("authorization ID hash mismatch")
    for source_id in SOURCE_IDS:
        row = inventory.get(source_id)
        if row is None or row.get("top_module") != "TopModule":
            raise AuthoringError(f"invalid inventory identity: {source_id}")
        _validate_public_content(source_id)

    correction_root.mkdir(mode=0o700, parents=True)
    tasks_root = correction_root / "tasks"
    qualification_root = correction_root / "qualification"
    tasks_root.mkdir(mode=0o700)
    qualification_root.mkdir(mode=0o700)
    manifest_rows: list[dict[str, Any]] = []
    authoring_rows: list[dict[str, Any]] = []
    testbench_hashes: dict[str, str] = {}
    fixture_hashes: dict[str, dict[str, str]] = {}
    for source_id in SOURCE_IDS:
        source = inventory[source_id]
        testbench_bytes = PUBLIC_TESTBENCHES[source_id].encode("utf-8")
        testbench_path = tasks_root / source_id / "testbench.sv"
        testbench_hash = _write_exclusive(testbench_path, testbench_bytes)
        testbench_hashes[source_id] = testbench_hash
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            fixture_path = qualification_root / source_id / f"{name}.sv"
            fixture_hashes[source_id][name] = _write_exclusive(fixture_path, content.encode("utf-8"))
        authoring_rows.append({
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "source_id": source_id,
            "task_id": source["task_id"],
            "top_module": "TopModule",
            "positive_rtl_path": f"{source_id}/positive.sv",
            "negative_mutations": [
                {"name": name, "rtl_path": f"{source_id}/{name}.sv", "authoring_method": "trusted_manual_public_spec_mutation", "oracle_basis": "public_specification_only", "expected_outcome": "rejected"}
                for name in MUTATION_NAMES[source_id]
            ],
            "reference_used": False,
            "support_files": [],
        })
        manifest_rows.append({
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source_dataset": "VerilogEval",
            "source_id": source_id,
            "task_id": source["task_id"],
            "split": "train",
            "top_module": "TopModule",
            "original_prompt_sha256": source["source_prompt_sha256"],
            "original_reference_rtl_sha256": source["reference_rtl_sha256"],
            "original_testbench_sha256": source["testbench_sha256"],
            "corrected_testbench_sha256": testbench_hash,
            "correction_version": CORRECTION_VERSION,
            "correction_reason": "repair public interface/oracle defects identified in read-only diagnosis",
            "authoring_method": "trusted_manual_public_spec",
            "dependency_closure": "passed",
            "reference_modified": False,
            "reference_copied_to_support": False,
            "support_files": [],
            "verification_readiness": "pending_isolated_qualification",
            "qualification_status": "pending_isolated_qualification",
            "fixture_hashes": fixture_hashes[source_id],
        })

    authoring_manifest_path = qualification_root / "authoring_manifest.jsonl"
    authoring_hash = _write_exclusive(authoring_manifest_path, _jsonl_bytes(authoring_rows))
    manifest_path = correction_root / "manifest.jsonl"
    manifest_hash = _write_exclusive(manifest_path, _jsonl_bytes(manifest_rows))
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": INVENTORY_SHA256,
        "frozen_split_sha256": SPLIT_SHA256,
        "selection_report_sha256": sha256_file(selection_path),
        "selection_ids_sha256": sha256_file(ids_path),
        "authorization_sha256": sha256_file(authorization_path),
        "selected_source_ids": list(SOURCE_IDS),
        "selected_count": len(SOURCE_IDS),
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(SOURCE_IDS),
        "positive_case_count": len(SOURCE_IDS),
        "negative_case_count": len(SOURCE_IDS) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "dependency_closure": "passed",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    attestation_hash = _write_exclusive(attestation_path, _json_bytes(attestation))
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": manifest_hash,
        "authoring_manifest": authoring_manifest_path.as_posix(),
        "authoring_manifest_sha256": authoring_hash,
        "authoring_attestation": attestation_path.as_posix(),
        "authoring_attestation_sha256": attestation_hash,
        "selected_source_ids": list(SOURCE_IDS),
        "positive_case_count": len(SOURCE_IDS),
        "negative_case_count": len(SOURCE_IDS) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_status": "pending_isolated_qualification",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(correction_root=args.correction_root, inventory_path=args.inventory, selection_path=args.selection, ids_path=args.ids, authorization_path=args.authorization)
    except (OSError, UnicodeError, json.JSONDecodeError, AuthoringError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
