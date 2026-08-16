#!/usr/bin/env python3
"""Author the four-task v006 verification retry from public task metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import textwrap
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    is_qualification_retry_selection,
    static_testbench_audit,
)


CORRECTION_VERSION = "assetfix_v006_retry_02"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
BASE_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
AUTHORING_ROW_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"


def _sv(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


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
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _tb(body: str, declarations: str, ports: str) -> str:
    return _sv(
        f"""
        module tb;
          integer mismatch_count;
          {textwrap.dedent(declarations).strip()}
          TopModule dut ({ports});
          initial begin
            mismatch_count = 0;
        {textwrap.indent(textwrap.dedent(body).strip(), "    ")}
            $display("Mismatches: %0d", mismatch_count);
            $finish;
          end
        endmodule
        """
    )


def _neighbor_tb(width: int) -> str:
    if width == 100:
        last = 99
        inner_last = 98
        value_decl = "reg [99:0]"
        expected_decl = "reg [99:0]"
        vector_literal = "{100{1'b1}}"
    else:
        last = 3
        inner_last = 2
        value_decl = "reg [3:0]"
        expected_decl = "reg [3:0]"
        vector_literal = "4'b1111"
    return _sv(
        f"""
        module tb;
          integer mismatch_count;
          {value_decl} in;
          wire {expected_decl[4:]} out_both, out_any, out_different;
          {expected_decl} expected_both, expected_any, expected_different;
          integer i, j;
          task check;
            input {value_decl[4:]} value;
            begin
              in = value;
              #1;
              expected_both = 0;
              expected_any = 0;
              expected_different = 0;
              for (j = 0; j < {inner_last}; j = j + 1) begin
                expected_both[j] = value[j] & value[j + 1];
                expected_any[j + 1] = value[j + 1] | value[j];
                expected_different[j] = value[j] ^ value[j + 1];
              end
              expected_different[{last}] = value[{last}] ^ value[0];
              if (out_both !== expected_both) mismatch_count = mismatch_count + 1;
              if (out_any !== expected_any) mismatch_count = mismatch_count + 1;
              if (out_different !== expected_different) mismatch_count = mismatch_count + 1;
            end
          endtask
          TopModule dut (.in(in), .out_both(out_both), .out_any(out_any), .out_different(out_different));
          initial begin
            mismatch_count = 0;
            check(0);
            check({vector_literal});
            for (i = 0; i < {width}; i = i + 1) begin
              in = 0;
              in[i] = 1'b1;
              check(in);
            end
            $display("Mismatches: %0d", mismatch_count);
            $finish;
          end
        endmodule
        """
    )


PUBLIC_TESTBENCHES = {
    "Prob092_gatesv100": _neighbor_tb(100),
    "Prob094_gatesv": _neighbor_tb(4),
    "Prob101_circuit4": _tb(
        """
        for (i = 0; i < 16; i = i + 1) begin
          value = i;
          #1;
          if (q !== (value[2] | value[1])) mismatch_count = mismatch_count + 1;
        end
        """,
        "reg [3:0] value; wire q; integer i;",
        ".a(value[3]), .b(value[2]), .c(value[1]), .d(value[0]), .q(q)",
    ),
    "Prob112_always_case2": _tb(
        """
        for (i = 0; i < 16; i = i + 1) begin
          in = i;
          #1;
          expected = 2'd0;
          if (in[0]) expected = 2'd0;
          else if (in[1]) expected = 2'd1;
          else if (in[2]) expected = 2'd2;
          else if (in[3]) expected = 2'd3;
          if (pos !== expected) mismatch_count = mismatch_count + 1;
        end
        """,
        "reg [3:0] in; wire [1:0] pos; reg [1:0] expected; integer i;",
        ".in(in), .pos(pos)",
    ),
}


PUBLIC_FIXTURES = {
    "Prob092_gatesv100": {
        "positive": _sv("""
        module TopModule(input logic [99:0] in, output logic [99:0] out_both, output logic [99:0] out_any, output logic [99:0] out_different);
          assign out_both = {1'b0, in[98:0] & in[99:1]};
          assign out_any = {in[99:1] | in[98:0], 1'b0};
          assign out_different = in ^ {in[0], in[99:1]};
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input logic [99:0] in, output logic [99:0] out_both, output logic [99:0] out_any, output logic [99:0] out_different); assign out_both=0; assign out_any=0; assign out_different=0; endmodule"),
        "wrong_neighbor_direction": _sv("module TopModule(input logic [99:0] in, output logic [99:0] out_both, output logic [99:0] out_any, output logic [99:0] out_different); assign out_both={1'b0,in[98:0]&in[99:1]}; assign out_any={in[99:1],1'b0}; assign out_different=in^{in[0],in[99:1]}; endmodule"),
    },
    "Prob094_gatesv": {
        "positive": _sv("""
        module TopModule(input logic [3:0] in, output logic [3:0] out_both, output logic [3:0] out_any, output logic [3:0] out_different);
          assign out_both = {1'b0, in[2:0] & in[3:1]};
          assign out_any = {in[3:1] | in[2:0], 1'b0};
          assign out_different = in ^ {in[0], in[3:1]};
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input logic [3:0] in, output logic [3:0] out_both, output logic [3:0] out_any, output logic [3:0] out_different); assign out_both=0; assign out_any=0; assign out_different=0; endmodule"),
        "wrong_neighbor_direction": _sv("module TopModule(input logic [3:0] in, output logic [3:0] out_both, output logic [3:0] out_any, output logic [3:0] out_different); assign out_both={1'b0,in[2:0]&in[3:1]}; assign out_any={in[3:1],1'b0}; assign out_different=in^{in[0],in[3:1]}; endmodule"),
    },
    "Prob101_circuit4": {
        "positive": _sv("module TopModule(input logic a, input logic b, input logic c, input logic d, output logic q); assign q=b|c; endmodule"),
        "constant_zero": _sv("module TopModule(input logic a, input logic b, input logic c, input logic d, output logic q); assign q=1'b0; endmodule"),
        "wrong_signal": _sv("module TopModule(input logic a, input logic b, input logic c, input logic d, output logic q); assign q=d; endmodule"),
    },
    "Prob112_always_case2": {
        "positive": _sv("module TopModule(input logic [3:0] in, output logic [1:0] pos); always_comb begin if(in[0]) pos=2'd0; else if(in[1]) pos=2'd1; else if(in[2]) pos=2'd2; else pos=2'd3; end endmodule"),
        "constant_zero": _sv("module TopModule(input logic [3:0] in, output logic [1:0] pos); always_comb pos=2'd0; endmodule"),
        "highest_bit_priority": _sv("module TopModule(input logic [3:0] in, output logic [1:0] pos); always_comb begin if(in[3]) pos=2'd3; else if(in[2]) pos=2'd2; else if(in[1]) pos=2'd1; else pos=2'd0; end endmodule"),
    },
}


def author(*, correction_root: Path, inventory_path: Path, selection_path: Path, ids_path: Path) -> dict[str, Any]:
    if correction_root.exists() or correction_root.is_symlink():
        raise ValueError(f"refusing to replace existing overlay: {correction_root}")
    inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
    selection = _load_json(selection_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not is_qualification_retry_selection(selection):
        raise ValueError("selection is not an explicitly bound qualification retry")
    if ids != list(PUBLIC_TESTBENCHES) or selection.get("selected_count") != len(ids):
        raise ValueError("retry selection order or count mismatch")
    if selection.get("correction_version") != CORRECTION_VERSION:
        raise ValueError("retry correction version mismatch")
    for source_id in ids:
        source = inventory.get(source_id)
        if source is None or source.get("task_id") != next(row["task_id"] for row in selection["rows"] if row["source_id"] == source_id):
            raise ValueError(f"retry selection identity mismatch: {source_id}")
        selected_row = next(row for row in selection["rows"] if row["source_id"] == source_id)
        if source.get("top_module") != "TopModule" or selected_row.get("split") != "train":
            raise ValueError(f"retry source is not a train TopModule row: {source_id}")
        audit, errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        if errors or audit.get("support_file_count") != 0:
            raise ValueError(f"static testbench audit failed: {source_id}: {errors}")
        if tuple(PUBLIC_FIXTURES[source_id]) != ("positive", *MUTATION_NAMES[source_id]):
            raise ValueError(f"fixture order mismatch: {source_id}")

    correction_root.mkdir(mode=0o700, parents=True)
    tasks_root = correction_root / "tasks"
    qualification_root = correction_root / "qualification"
    tasks_root.mkdir(mode=0o700)
    qualification_root.mkdir(mode=0o700)
    authoring_rows: list[dict[str, Any]] = []
    testbench_hashes: dict[str, str] = {}
    fixture_hashes: dict[str, dict[str, str]] = {}
    for source_id in ids:
        tb_path = tasks_root / source_id / "testbench.sv"
        _write_exclusive(tb_path, PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        testbench_hashes[source_id] = _sha256_file(tb_path)
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            lowered = content.casefold()
            if any(marker in lowered for marker in ("`include", "refmodule", "reference.sv", "/home/", "/tmp/", "/root/", "testbench")):
                raise ValueError(f"private or dependency marker in fixture: {source_id}:{name}")
            fixture_path = qualification_root / source_id / f"{name}.sv"
            _write_exclusive(fixture_path, content.encode("utf-8"))
            fixture_hashes[source_id][name] = _sha256_file(fixture_path)
        authoring_rows.append({
            "schema_version": AUTHORING_ROW_SCHEMA_VERSION,
            "source_id": source_id,
            "task_id": inventory[source_id]["task_id"],
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
                for name in MUTATION_NAMES[source_id]
            ],
            "reference_used": False,
            "support_files": [],
        })
    authoring_manifest = qualification_root / "authoring_manifest.jsonl"
    _write_exclusive(authoring_manifest, b"".join((json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in authoring_rows))
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": _sha256_file(ids_path),
        "selection_report_sha256": _sha256_file(selection_path),
        "selected_source_ids": ids,
        "selected_count": len(ids),
        "public_specification_hashes": {source_id: inventory[source_id]["source_prompt_sha256"] for source_id in ids},
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(ids),
        "positive_case_count": len(ids),
        "negative_case_count": len(ids) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "correction_reason": "repair public oracle width and behavioral expectation defects identified in read-only diagnosis",
        "dependency_closure": "pending_static_validation",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    _write_exclusive(attestation_path, (json.dumps(attestation, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "authoring_manifest": authoring_manifest.as_posix(),
        "authoring_attestation": attestation_path.as_posix(),
        "source_ids": ids,
        "testbench_count": len(ids),
        "positive_case_count": len(ids),
        "negative_case_count": len(ids) * 2,
        "testbench_hashes": testbench_hashes,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(
            correction_root=args.correction_root,
            inventory_path=args.inventory,
            selection_path=args.selection,
            ids_path=args.ids,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
