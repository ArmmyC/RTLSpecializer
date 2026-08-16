#!/usr/bin/env python3
"""Author the v008 qualification retry from public task specifications only.

This command consumes the preserved qualification metadata and the existing
public-specification authoring constants.  It never reads reference RTL,
executes HDL, or changes the preserved v008 overlay or qualification run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import textwrap
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.author_rtl_generation_assetfix_v008 import (
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit
from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
)


CORRECTION_VERSION = "assetfix_v008_retry_01"
SOURCE_IDS = (
    "Prob131_mt2015_q4",
    "Prob135_m2014_q6b",
    "Prob138_2012_q2fsm",
    "Prob139_2013_q2bfsm",
    "Prob146_fsm_serialdata",
    "Prob148_2013_q2afsm",
    "Prob150_review2015_fsmonehot",
    "Prob154_fsm_ps2data",
)
CLASSIFICATIONS = {
    "Prob131_mt2015_q4": "semantically_equivalent_mutation",
    "Prob135_m2014_q6b": "incorrect_public_spec_interpretation",
    "Prob138_2012_q2fsm": "testbench_timing_defect",
    "Prob139_2013_q2bfsm": "positive_fixture_defect",
    "Prob146_fsm_serialdata": "testbench_timing_defect",
    "Prob148_2013_q2afsm": "testbench_timing_defect",
    "Prob150_review2015_fsmonehot": "incorrect_public_spec_interpretation",
    "Prob154_fsm_ps2data": "ambiguous_public_specification",
}
REASONS = {
    "Prob131_mt2015_q4": (
        "The failed wrong_composition mutation is equivalent over all four public input combinations because the two intermediate terms cannot both be one. Replace only that mutation with a behaviorally distinct one."
    ),
    "Prob135_m2014_q6b": (
        "The preserved positive fixture and oracle do not consistently encode the public next-state y[1] table. Replace the public-spec-derived fixture and use exhaustive state/input expectations."
    ),
    "Prob138_2012_q2fsm": (
        "The positive fixture follows the public transition table, but the checker samples one edge before the expected E-state output. Move the expected-one check to the third asserted input edge."
    ),
    "Prob139_2013_q2bfsm": (
        "The positive fixture omits the permanent-g1 P1 state required after observing y=1. Add P1 to the public-spec-derived g equation."
    ),
    "Prob146_fsm_serialdata": (
        "The checker inserts an extra data bit before the eight LSB-first bits, so the positive fixture receives nine data cycles. Remove that extra tick and retain explicit bad-stop coverage."
    ),
    "Prob148_2013_q2afsm": (
        "The checker expects the lower-priority grant one edge before the FSM can return to A and arbitrate r[2]. Add the return-to-A edge before checking D."
    ),
    "Prob150_review2015_fsmonehot": (
        "The checker expects done while the current state is Count, but the public Moore table asserts done only in Wait. Check Wait_next and current-state outputs separately."
    ),
    "Prob154_fsm_ps2data": (
        "The public algorithm requires in[3]=1 for the first byte, while the preserved example uses 8'h81 whose bit 3 is zero. Use an unambiguous 8'h08 start byte and record the example contradiction."
    ),
}


def _sv(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


RETRY_TESTBENCHES = dict(PUBLIC_TESTBENCHES)
RETRY_TESTBENCHES["Prob135_m2014_q6b"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg [2:0] y;
      reg w;
      wire Y1;
      integer i;
      reg expected;
      TopModule dut (.y(y), .w(w), .Y1(Y1));

      function automatic expected_y1;
        input [2:0] state;
        input input_w;
        begin
          case (state)
            3'd0: expected_y1 = 1'b0;
            3'd1: expected_y1 = 1'b1;
            3'd2: expected_y1 = input_w;
            3'd3: expected_y1 = 1'b0;
            3'd4: expected_y1 = input_w;
            3'd5: expected_y1 = 1'b1;
            default: expected_y1 = 1'b0;
          endcase
        end
      endfunction

      initial begin
        mismatch_count = 0;
        for (i = 0; i < 6; i = i + 1) begin
          y = i;
          w = 0;
          #1;
          expected = expected_y1(y, w);
          if (Y1 !== expected) mismatch_count = mismatch_count + 1;
          w = 1;
          #1;
          expected = expected_y1(y, w);
          if (Y1 !== expected) mismatch_count = mismatch_count + 1;
        end
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)
RETRY_TESTBENCHES["Prob138_2012_q2fsm"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg clk, reset, w;
      wire z;
      TopModule dut (.clk(clk), .reset(reset), .w(w), .z(z));
      always #5 clk = ~clk;

      initial begin
        mismatch_count = 0;
        clk = 0;
        reset = 1;
        w = 0;
        @(posedge clk); #1;
        if (z !== 1'b0) mismatch_count = mismatch_count + 1;
        reset = 0;
        w = 1;
        @(posedge clk); #1;
        if (z !== 1'b0) mismatch_count = mismatch_count + 1;
        @(posedge clk); #1;
        if (z !== 1'b0) mismatch_count = mismatch_count + 1;
        @(posedge clk); #1;
        if (z !== 1'b1) mismatch_count = mismatch_count + 1;
        w = 0;
        @(posedge clk); #1;
        if (z !== 1'b0) mismatch_count = mismatch_count + 1;
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)
RETRY_TESTBENCHES["Prob146_fsm_serialdata"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg clk, reset, in;
      wire [7:0] out_byte;
      wire done;
      reg [7:0] bits;
      integer k;
      task automatic tick;
        input value;
        begin in = value; @(posedge clk); #1; end
      endtask
      TopModule dut (.clk(clk), .in(in), .reset(reset), .out_byte(out_byte), .done(done));
      always #5 clk = ~clk;

      initial begin
        mismatch_count = 0;
        clk = 0;
        reset = 1;
        in = 1;
        tick(1);
        reset = 0;
        bits = 8'ha6;
        tick(0);
        for (k = 0; k < 8; k = k + 1) tick(bits[k]);
        tick(1);
        if (done !== 1 || out_byte !== bits) mismatch_count = mismatch_count + 1;
        tick(0);
        for (k = 0; k < 8; k = k + 1) tick(bits[k]);
        tick(0);
        if (done !== 0) mismatch_count = mismatch_count + 1;
        tick(1);
        if (done !== 1 || out_byte !== bits) mismatch_count = mismatch_count + 1;
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)
RETRY_TESTBENCHES["Prob148_2013_q2afsm"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg clk, resetn;
      reg [2:0] r;
      wire [2:0] g;
      TopModule dut (.clk(clk), .resetn(resetn), .r(r), .g(g));
      always #5 clk = ~clk;

      initial begin
        mismatch_count = 0;
        clk = 0;
        resetn = 0;
        r = 0;
        @(posedge clk); #1;
        if (g !== 3'b000) mismatch_count = mismatch_count + 1;
        resetn = 1;
        r = 3'b111;
        @(posedge clk); #1;
        if (g !== 3'b001) mismatch_count = mismatch_count + 1;
        r = 0;
        @(posedge clk); #1;
        if (g !== 3'b000) mismatch_count = mismatch_count + 1;
        r = 3'b010;
        @(posedge clk); #1;
        if (g !== 3'b010) mismatch_count = mismatch_count + 1;
        r = 3'b100;
        @(posedge clk); #1;
        if (g !== 3'b000) mismatch_count = mismatch_count + 1;
        @(posedge clk); #1;
        if (g !== 3'b100) mismatch_count = mismatch_count + 1;
        r = 0;
        @(posedge clk); #1;
        if (g !== 3'b000) mismatch_count = mismatch_count + 1;
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)
RETRY_TESTBENCHES["Prob150_review2015_fsmonehot"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg d, done_counting, ack;
      reg [9:0] state;
      wire B3_next, S_next, S1_next, Count_next, Wait_next, done, counting, shift_ena;
      TopModule dut (.d(d), .done_counting(done_counting), .ack(ack), .state(state), .B3_next(B3_next), .S_next(S_next), .S1_next(S1_next), .Count_next(Count_next), .Wait_next(Wait_next), .done(done), .counting(counting), .shift_ena(shift_ena));

      initial begin
        mismatch_count = 0;
        d = 0;
        done_counting = 0;
        ack = 0;
        state = 10'b0000000001;
        #1;
        if (!S_next || S1_next || done || counting || shift_ena) mismatch_count = mismatch_count + 1;
        d = 1;
        #1;
        if (!S1_next || S_next) mismatch_count = mismatch_count + 1;
        state = 10'b0000010000;
        #1;
        if (!shift_ena) mismatch_count = mismatch_count + 1;
        state = 10'b0001000000;
        #1;
        if (!B3_next) mismatch_count = mismatch_count + 1;
        state = 10'b0100000000;
        done_counting = 1;
        #1;
        if (!Wait_next || done || !counting) mismatch_count = mismatch_count + 1;
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)
RETRY_TESTBENCHES["Prob154_fsm_ps2data"] = _sv(
    """
    module tb;
      integer mismatch_count;
      reg clk, reset;
      reg [7:0] in;
      wire [23:0] out_bytes;
      wire done;
      TopModule dut (.clk(clk), .reset(reset), .in(in), .out_bytes(out_bytes), .done(done));
      always #5 clk = ~clk;

      initial begin
        mismatch_count = 0;
        clk = 0;
        reset = 1;
        in = 0;
        @(posedge clk); #1;
        if (done) mismatch_count = mismatch_count + 1;
        reset = 0;
        in = 8'h00;
        @(posedge clk); #1;
        if (done) mismatch_count = mismatch_count + 1;
        in = 8'h08;
        @(posedge clk); #1;
        if (done) mismatch_count = mismatch_count + 1;
        in = 8'h12;
        @(posedge clk); #1;
        if (done) mismatch_count = mismatch_count + 1;
        in = 8'h34;
        @(posedge clk); #1;
        if (done !== 1 || out_bytes !== 24'h081234) mismatch_count = mismatch_count + 1;
        in = 8'h00;
        @(posedge clk); #1;
        if (done) mismatch_count = mismatch_count + 1;
        $display("Mismatches: %0d", mismatch_count);
        $finish;
      end
    endmodule
    """
)


RETRY_FIXTURES = {
    source_id: dict(PUBLIC_FIXTURES[source_id]) for source_id in SOURCE_IDS
}
RETRY_FIXTURES["Prob131_mt2015_q4"]["wrong_composition_distinct"] = _sv(
    """
    module TopModule(input logic x, input logic y, output logic z);
      logic a, b;
      assign a = (x ^ y) & x;
      assign b = ~(x ^ y);
      assign z = a & b;
    endmodule
    """
)
del RETRY_FIXTURES["Prob131_mt2015_q4"]["wrong_composition"]
RETRY_FIXTURES["Prob135_m2014_q6b"]["positive"] = _sv(
    """
    module TopModule(input logic [2:0] y, input logic w, output logic Y1);
      always @* begin
        case ({y, w})
          4'h0: Y1 = 1'b0;
          4'h1: Y1 = 1'b0;
          4'h2: Y1 = 1'b1;
          4'h3: Y1 = 1'b1;
          4'h4: Y1 = 1'b0;
          4'h5: Y1 = 1'b1;
          4'h6: Y1 = 1'b0;
          4'h7: Y1 = 1'b0;
          4'h8: Y1 = 1'b0;
          4'h9: Y1 = 1'b1;
          4'ha: Y1 = 1'b1;
          4'hb: Y1 = 1'b1;
          default: Y1 = 1'b0;
        endcase
      end
    endmodule
    """
)
RETRY_FIXTURES["Prob139_2013_q2bfsm"]["positive"] = _sv(
    """
    module TopModule(input logic clk, input logic resetn, input logic x, input logic y, output logic f, output logic g);
      logic [3:0] state;
      localparam A=0, FP=1, X1=2, X10=3, X101=4, G1=5, G2=6, P0=7, P1=8;
      always @(posedge clk) begin
        if (!resetn) state <= A;
        else case (state)
          A: state <= FP;
          FP: state <= X1;
          X1: state <= x ? X10 : X1;
          X10: state <= x ? X101 : X1;
          X101: state <= G1;
          G1: state <= y ? P1 : G2;
          G2: state <= y ? P1 : P0;
          P0: state <= P0;
          P1: state <= P1;
          default: state <= P0;
        endcase
      end
      assign f = (state == FP);
      assign g = (state == G1) || (state == G2) || (state == P1);
    endmodule
    """
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _write_json(path: Path, value: Any) -> None:
    _write_exclusive(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _validate_parent(*, parent_run: Path, parent_selection: Path, parent_ids: Path, report: Path, evidence: Path, failed: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    selection = _load_json(parent_selection)
    report_value = _load_json(parent_run / "reports/qualification_report.json")
    cases = _load_jsonl(parent_run / "qualification/attempt_01/case_manifest.jsonl")
    evidence_rows = _load_jsonl(evidence)
    failed_ids = [line.strip() for line in failed.read_text(encoding="utf-8").splitlines() if line.strip()]
    if failed_ids != list(SOURCE_IDS):
        raise ValueError("preserved failed-task order does not match the v008 retry set")
    if report_value.get("selected_tasks") != 20 or report_value.get("qualified_tasks") != 12:
        raise ValueError("preserved v008 qualification counts are not 20 selected / 12 qualified")
    if len(cases) != 60 or len(evidence_rows) != 60:
        raise ValueError("preserved v008 qualification does not contain 60 cases")
    if _sha256_file(parent_selection) != "50b3d14f130a0402ba3d47f41695b00ea84afc4a265f1a770936feeca1a38ff9":
        raise ValueError("parent selection hash mismatch")
    if _sha256_file(report) != "0bcb360dd21539f62c48de3c19c389062f37809b7905e6c76573ff3ec7d3c671":
        raise ValueError("parent qualification report hash mismatch")
    if _sha256_file(evidence) != "147c8535eb26110354e17f6050aad646561ebe1cbfc0d65d8eb34d74de217cff":
        raise ValueError("parent qualification evidence hash mismatch")
    return selection, cases, evidence_rows


def author(
    *,
    parent_run: Path,
    parent_selection: Path,
    parent_ids: Path,
    parent_report: Path,
    parent_evidence: Path,
    parent_sidecar: Path,
    parent_failed: Path,
    inventory_path: Path,
    split_path: Path,
    analysis_path: Path,
    selection_output: Path,
    ids_output: Path,
    correction_root: Path,
) -> dict[str, Any]:
    if correction_root.exists() or selection_output.exists() or ids_output.exists():
        raise ValueError("retry output already exists")
    selection_parent, cases, evidence_rows = _validate_parent(
        parent_run=parent_run,
        parent_selection=parent_selection,
        parent_ids=parent_ids,
        report=parent_report,
        evidence=parent_evidence,
        failed=parent_failed,
    )
    analysis = _load_json(analysis_path)
    if analysis.get("schema_version") != "rtl_asset_qualification_failure_analysis_v0.2":
        raise ValueError("failure analysis schema mismatch")
    analysis_by_source = {row["source_id"]: row for row in analysis.get("rows", [])}
    if list(analysis_by_source) != list(SOURCE_IDS):
        raise ValueError("failure analysis order mismatch")
    inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
    split = _load_json(split_path)
    train_ids = set((split.get("splits") or {}).get("train", []))
    parent_rows = {row["source_id"]: row for row in selection_parent.get("rows", [])}
    ids_bytes = ("\n".join(SOURCE_IDS) + "\n").encode("utf-8")
    _write_exclusive(ids_output, ids_bytes)
    ids_hash = _sha256_file(ids_output)
    rows = []
    for order, source_id in enumerate(SOURCE_IDS, 1):
        source = inventory.get(source_id)
        if source is None or source_id not in train_ids:
            raise ValueError(f"retry source is not a pinned train row: {source_id}")
        parent_row = parent_rows[source_id]
        rows.append({
            **parent_row,
            "selection_order": order,
            "selection_reason": "bounded correction retry after read-only qualification diagnosis",
            "retry_classification": CLASSIFICATIONS[source_id],
            "current_readiness": "needs_testbench",
            "split": "train",
        })
    selection = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_kind": "qualification_retry",
        "selection_id": "verilog_eval_v008_final_train_remainder_retry_01",
        "source_dataset": "VerilogEval",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "base_inventory_sha256": BASE_INVENTORY_SHA256,
        "base_split_sha256": BASE_SPLIT_SHA256,
        "correction_version": CORRECTION_VERSION,
        "split": "train",
        "selected_count": len(SOURCE_IDS),
        "selection_ids_sha256": ids_hash,
        "parent_run_id": parent_run.name,
        "parent_qualification_report_sha256": _sha256_file(parent_report),
        "parent_qualification_evidence_sha256": _sha256_file(parent_evidence),
        "parent_qualified_subset_binding_sha256": _sha256_file(parent_run / "reports/qualification_freeze.json"),
        "parent_failure_analysis_sha256": _sha256_file(analysis_path),
        "retry_reason": "public-spec-only correction of diagnosed v008 qualification failures",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "rows": rows,
        "ok": True,
        "errors": [],
    }
    _write_json(selection_output, selection)
    selection_hash = _sha256_file(selection_output)

    correction_root.mkdir(mode=0o700, parents=True)
    tasks_root = correction_root / "tasks"
    qualification_root = correction_root / "qualification"
    tasks_root.mkdir(mode=0o700)
    qualification_root.mkdir(mode=0o700)
    manifest_rows = []
    authoring_rows = []
    fixture_hashes = {}
    testbench_hashes = {}
    case_by_source = {source_id: [row for row in cases if row.get("source_id") == source_id] for source_id in SOURCE_IDS}
    evidence_by_candidate = {row.get("candidate_id"): row for row in evidence_rows}
    for source_id in SOURCE_IDS:
        source = inventory[source_id]
        testbench = RETRY_TESTBENCHES[source_id]
        audit, audit_errors = static_testbench_audit(testbench.encode("utf-8"))
        if audit_errors:
            raise ValueError(f"static testbench audit failed: {source_id}: {audit_errors}")
        tb_path = tasks_root / source_id / "testbench.sv"
        _write_exclusive(tb_path, testbench.encode("utf-8"))
        testbench_hash = _sha256_file(tb_path)
        testbench_hashes[source_id] = testbench_hash
        names = list(RETRY_FIXTURES[source_id])
        if names[0] != "positive" or len(names) != 3:
            raise ValueError(f"fixture contract must have one positive and two negatives: {source_id}")
        fixture_hashes[source_id] = {}
        for name, content in RETRY_FIXTURES[source_id].items():
            lowered = content.casefold()
            if any(marker in lowered for marker in ("`include", "refmodule", "reference.sv", "/home/", "/tmp/", "/root/", "testbench")):
                raise ValueError(f"private marker in fixture: {source_id}:{name}")
            path = qualification_root / source_id / f"{name}.sv"
            _write_exclusive(path, content.encode("utf-8"))
            fixture_hashes[source_id][name] = _sha256_file(path)
        contracts = [{
            "schema_version": "rtl_correction_mutation_contract_v0.1",
            "execution_status": "pending_isolated_qualification",
            "expected_outcome": "accepted",
            "kind": "positive",
            "name": "public_spec_candidate",
            "oracle_basis": "public_specification_only",
        }]
        contracts.extend({
            "schema_version": "rtl_correction_mutation_contract_v0.1",
            "execution_status": "pending_isolated_qualification",
            "expected_outcome": "rejected",
            "kind": "negative",
            "name": name,
            "oracle_basis": "public_specification_only",
        } for name in names[1:])
        manifest_rows.append({
            "schema_version": "rtl_verification_asset_correction_row_v0.2",
            "source_dataset": "VerilogEval",
            "source_id": source_id,
            "task_id": source["task_id"],
            "split": "train",
            "design_family": source.get("design_family", "fsm"),
            "top_module": "TopModule",
            "upstream_commit": SOURCE_COMMIT,
            "original_prompt_sha256": source["source_prompt_sha256"],
            "original_reference_rtl_sha256": source["reference_rtl_sha256"],
            "original_testbench_sha256": source["testbench_sha256"],
            "corrected_testbench_sha256": testbench_hash,
            "correction_version": CORRECTION_VERSION,
            "correction_reason": REASONS[source_id],
            "authoring_method": "trusted_manual_public_spec_retry",
            "reference_modified": False,
            "reference_copied_to_support": False,
            "testbench_path": f"tasks/{source_id}/testbench.sv",
            "support_files": [],
            "dependency_closure": "passed",
            "verification_readiness": "pending_qualification",
            "qualification_status": "pending_isolated_qualification",
            "mutation_contracts": contracts,
            "static_audit": audit,
            "frozen_split_sha256": BASE_SPLIT_SHA256,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "public_specification_sha256": source["source_prompt_sha256"],
            "selection_ids_sha256": ids_hash,
            "selection_report_sha256": selection_hash,
        })
        authoring_rows.append({
            "schema_version": "rtl_asset_qualification_authoring_row_v0.1",
            "source_id": source_id,
            "task_id": source["task_id"],
            "top_module": "TopModule",
            "positive_rtl_path": f"{source_id}/positive.sv",
            "negative_mutations": [
                {
                    "name": name,
                    "rtl_path": f"{source_id}/{name}.sv",
                    "authoring_method": "trusted_manual_public_spec_mutation",
                    "oracle_basis": "public_specification_only",
                    "expected_outcome": "rejected",
                }
                for name in names[1:]
            ],
            "reference_used": False,
            "support_files": [],
            "lineage": "qualification_retry_overlay",
            "prior_assetfix_versions": ["assetfix_v008"],
        })
    manifest_path = correction_root / "manifest.jsonl"
    authoring_manifest_path = qualification_root / "authoring_manifest.jsonl"
    _write_exclusive(manifest_path, b"".join((json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in manifest_rows))
    _write_exclusive(authoring_manifest_path, b"".join((json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in authoring_rows))
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": BASE_INVENTORY_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": ids_hash,
        "selection_report_sha256": selection_hash,
        "parent_run_id": parent_run.name,
        "parent_qualification_report_sha256": _sha256_file(parent_report),
        "parent_qualification_evidence_sha256": _sha256_file(parent_evidence),
        "selected_source_ids": list(SOURCE_IDS),
        "selected_count": len(SOURCE_IDS),
        "classifications": CLASSIFICATIONS,
        "public_specification_hashes": {source_id: inventory[source_id]["source_prompt_sha256"] for source_id in SOURCE_IDS},
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(SOURCE_IDS),
        "positive_case_count": len(SOURCE_IDS),
        "negative_case_count": len(SOURCE_IDS) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec_retry",
        "dependency_closure": "pending_static_validation",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    _write_json(attestation_path, attestation)
    return {
        "ok": True,
        "selection": selection_output.as_posix(),
        "ids": ids_output.as_posix(),
        "selection_sha256": selection_hash,
        "selection_ids_sha256": ids_hash,
        "correction_root": correction_root.as_posix(),
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": _sha256_file(manifest_path),
        "authoring_manifest": authoring_manifest_path.as_posix(),
        "authoring_manifest_sha256": _sha256_file(authoring_manifest_path),
        "authoring_attestation": attestation_path.as_posix(),
        "source_ids": list(SOURCE_IDS),
        "selected_count": len(SOURCE_IDS),
        "positive_case_count": len(SOURCE_IDS),
        "negative_case_count": len(SOURCE_IDS) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-run", required=True, type=Path)
    parser.add_argument("--parent-selection", required=True, type=Path)
    parser.add_argument("--parent-ids", required=True, type=Path)
    parser.add_argument("--parent-report", required=True, type=Path)
    parser.add_argument("--parent-evidence", required=True, type=Path)
    parser.add_argument("--parent-sidecar", required=True, type=Path)
    parser.add_argument("--parent-failed", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--selection-output", required=True, type=Path)
    parser.add_argument("--ids-output", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(
            parent_run=args.parent_run,
            parent_selection=args.parent_selection,
            parent_ids=args.parent_ids,
            parent_report=args.parent_report,
            parent_evidence=args.parent_evidence,
            parent_sidecar=args.parent_sidecar,
            parent_failed=args.parent_failed,
            inventory_path=args.inventory,
            split_path=args.split,
            analysis_path=args.analysis,
            selection_output=args.selection_output,
            ids_output=args.ids_output,
            correction_root=args.correction_root,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
