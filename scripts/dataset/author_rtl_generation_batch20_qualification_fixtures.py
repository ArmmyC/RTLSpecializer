#!/usr/bin/env python3
"""Author public-spec qualification fixtures for the frozen batch-20 rows.

The files emitted by this command are qualification controls, not teacher
answers.  The templates below are independently authored from the public
prompt/interface records.  No reference RTL, original testbench, model, or
EDA tool is read or invoked.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.dataset.rtl_generation_batch_corrections import MUTATION_NAMES
from scripts.dataset.rtl_generation_batch_selection import BATCH20_SOURCE_IDS
from scripts.dataset.rtl_generation_asset_qualification import (
    AUTHORING_ROW_SCHEMA_VERSION,
    CORRECTION_VERSION,
    QualificationError,
)


def _module(body: str) -> str:
    return body.strip() + "\n"


PUBLIC_FIXTURES: dict[str, dict[str, str]] = {
    "Prob029_m2014_q4g": {
        "positive": _module("""
module TopModule (
  input in1, input in2, input in3, output logic out
);
  assign out = ~(in1 ^ in2) ^ in3;
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input in1, input in2, input in3, output logic out
);
  assign out = 1'b0;
endmodule
"""),
        "inverted_output": _module("""
module TopModule (
  input in1, input in2, input in3, output logic out
);
  assign out = ~((~(in1 ^ in2)) ^ in3);
endmodule
"""),
    },
    "Prob055_conditional": {
        "positive": _module("""
module TopModule (
  input [7:0] a, input [7:0] b, input [7:0] c, input [7:0] d,
  output reg [7:0] min
);
  always @* begin
    min = a;
    if (b < min) min = b;
    if (c < min) min = c;
    if (d < min) min = d;
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input [7:0] a, input [7:0] b, input [7:0] c, input [7:0] d,
  output reg [7:0] min
);
  always @* min = 8'b0;
endmodule
"""),
        "wrong_min_operand": _module("""
module TopModule (
  input [7:0] a, input [7:0] b, input [7:0] c, input [7:0] d,
  output reg [7:0] min
);
  always @* begin
    min = a;
    if (b < min) min = b;
    if (c < min) min = c;
  end
endmodule
"""),
    },
    "Prob092_gatesv100": {
        "positive": _module("""
module TopModule (
  input [99:0] in,
  output [98:0] out_both,
  output [99:1] out_any,
  output [99:0] out_different
);
  assign out_both = in[98:0] & in[99:1];
  assign out_any = in[99:1] | in[98:0];
  assign out_different = in ^ {in[0], in[99:1]};
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input [99:0] in,
  output [98:0] out_both,
  output [99:1] out_any,
  output [99:0] out_different
);
  assign out_both = 99'b0;
  assign out_any = 99'b0;
  assign out_different = 100'b0;
endmodule
"""),
        "wrong_neighbor_direction": _module("""
module TopModule (
  input [99:0] in,
  output [98:0] out_both,
  output [99:1] out_any,
  output [99:0] out_different
);
  assign out_both = in[98:0] & in[99:1];
  assign out_any = in[99:1];
  assign out_different = in ^ {in[0], in[99:1]};
endmodule
"""),
    },
    "Prob106_always_nolatches": {
        "positive": _module("""
module TopModule (
  input [15:0] scancode,
  output reg left, output reg down, output reg right, output reg up
);
  always @* begin
    left = 1'b0; down = 1'b0; right = 1'b0; up = 1'b0;
    case (scancode)
      16'he06b: left = 1'b1;
      16'he072: down = 1'b1;
      16'he074: right = 1'b1;
      16'he075: up = 1'b1;
      default: begin end
    endcase
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input [15:0] scancode,
  output reg left, output reg down, output reg right, output reg up
);
  always @* begin left = 0; down = 0; right = 0; up = 0; end
endmodule
"""),
        "wrong_scancode_mapping": _module("""
module TopModule (
  input [15:0] scancode,
  output reg left, output reg down, output reg right, output reg up
);
  always @* begin
    left = 0; down = 0; right = 0; up = 0;
    case (scancode)
      16'he06b: up = 1;
      16'he072: right = 1;
      16'he074: down = 1;
      16'he075: left = 1;
      default: begin end
    endcase
  end
endmodule
"""),
    },
    "Prob112_always_case2": {
        "positive": _module("""
module TopModule (
  input [3:0] in, output reg [1:0] pos
);
  always @* begin
    if (in[3]) pos = 2'd3;
    else if (in[2]) pos = 2'd2;
    else if (in[1]) pos = 2'd1;
    else pos = 2'd0;
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input [3:0] in, output reg [1:0] pos
);
  always @* pos = 2'b0;
endmodule
"""),
        "highest_bit_priority": _module("""
module TopModule (
  input [3:0] in, output reg [1:0] pos
);
  always @* begin
    if (in[0]) pos = 2'd0;
    else if (in[1]) pos = 2'd1;
    else if (in[2]) pos = 2'd2;
    else pos = 2'd3;
  end
endmodule
"""),
    },
    "Prob122_kmap4": {
        "positive": _module("""
module TopModule (
  input a, input b, input c, input d, output reg out
);
  always @* out = a ^ b ^ c ^ d;
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input a, input b, input c, input d, output reg out
);
  always @* out = 1'b0;
endmodule
"""),
        "inverted_output": _module("""
module TopModule (
  input a, input b, input c, input d, output reg out
);
  always @* out = ~(a ^ b ^ c ^ d);
endmodule
"""),
    },
    "Prob060_m2014_q4k": {
        "positive": _module("""
module TopModule (
  input clk, input resetn, input in, output out
);
  reg [3:0] q;
  always @(posedge clk) begin
    if (!resetn) q <= 4'b0;
    else q <= {q[2:0], in};
  end
  assign out = q[3];
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input clk, input resetn, input in, output out
);
  assign out = 1'b0;
endmodule
"""),
        "one_cycle_latency": _module("""
module TopModule (
  input clk, input resetn, input in, output out
);
  reg [4:0] q;
  always @(posedge clk) begin
    if (!resetn) q <= 5'b0;
    else q <= {q[3:0], in};
  end
  assign out = q[4];
endmodule
"""),
    },
    "Prob061_2014_q4a": {
        "positive": _module("""
module TopModule (
  input clk, input w, input R, input E, input L, output reg Q
);
  always @(posedge clk) begin
    if (L) Q <= R;
    else if (E) Q <= w;
  end
endmodule
"""),
        "hold_when_enabled": _module("""
module TopModule (
  input clk, input w, input R, input E, input L, output reg Q
);
  always @(posedge clk) begin
    if (L) Q <= R;
  end
endmodule
"""),
        "load_priority_removed": _module("""
module TopModule (
  input clk, input w, input R, input E, input L, output reg Q
);
  always @(posedge clk) begin
    if (E) Q <= w;
    else if (L) Q <= R;
  end
endmodule
"""),
    },
    "Prob084_ece241_2013_q12": {
        "positive": _module("""
module TopModule (
  input clk, input enable, input S, input A, input B, input C, output reg Z
);
  reg [7:0] q;
  always @(posedge clk) begin
    if (enable) q <= {q[6:0], S};
  end
  always @* Z = q[{A, B, C}];
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input clk, input enable, input S, input A, input B, input C, output reg Z
);
  reg [7:0] q;
  always @(posedge clk) if (enable) q <= {q[6:0], S};
  always @* Z = 1'b0;
endmodule
"""),
        "wrong_mux_address": _module("""
module TopModule (
  input clk, input enable, input S, input A, input B, input C, output reg Z
);
  reg [7:0] q;
  always @(posedge clk) if (enable) q <= {q[6:0], S};
  always @* Z = q[7 - {A, B, C}];
endmodule
"""),
    },
    "Prob085_shift4": {
        "positive": _module("""
module TopModule (
  input clk, input areset, input load, input ena, input [3:0] data,
  output reg [3:0] q
);
  always @(posedge clk or posedge areset) begin
    if (areset) q <= 4'b0;
    else if (load) q <= data;
    else if (ena) q <= {1'b0, q[3:1]};
  end
endmodule
"""),
        "missing_reset": _module("""
module TopModule (
  input clk, input areset, input load, input ena, input [3:0] data,
  output reg [3:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena) q <= {1'b0, q[3:1]};
  end
endmodule
"""),
        "load_priority_removed": _module("""
module TopModule (
  input clk, input areset, input load, input ena, input [3:0] data,
  output reg [3:0] q
);
  always @(posedge clk or posedge areset) begin
    if (areset) q <= 4'b0;
    else if (ena) q <= {1'b0, q[3:1]};
    else if (load) q <= data;
  end
endmodule
"""),
    },
    "Prob105_rotate100": {
        "positive": _module("""
module TopModule (
  input clk, input load, input [1:0] ena, input [99:0] data,
  output reg [99:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena == 2'b01) q <= {q[0], q[99:1]};
    else if (ena == 2'b10) q <= {q[98:0], q[99]};
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input clk, input load, input [1:0] ena, input [99:0] data,
  output reg [99:0] q
);
  always @(posedge clk) q <= 100'b0;
endmodule
"""),
        "wrong_rotation_direction": _module("""
module TopModule (
  input clk, input load, input [1:0] ena, input [99:0] data,
  output reg [99:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena == 2'b01) q <= {q[98:0], q[99]};
    else if (ena == 2'b10) q <= {q[0], q[99:1]};
  end
endmodule
"""),
    },
    "Prob115_shift18": {
        "positive": _module("""
module TopModule (
  input clk, input load, input ena, input [1:0] amount,
  input [63:0] data, output reg [63:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena) begin
      case (amount)
        2'b00: q <= q <<< 1;
        2'b01: q <= q <<< 8;
        2'b10: q <= $signed(q) >>> 1;
        2'b11: q <= $signed(q) >>> 8;
      endcase
    end
  end
endmodule
"""),
        "logical_right_shift": _module("""
module TopModule (
  input clk, input load, input ena, input [1:0] amount,
  input [63:0] data, output reg [63:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena) begin
      case (amount)
        2'b00: q <= q << 1;
        2'b01: q <= q << 8;
        2'b10: q <= q >> 1;
        2'b11: q <= q >> 8;
      endcase
    end
  end
endmodule
"""),
        "wrong_shift_amount": _module("""
module TopModule (
  input clk, input load, input ena, input [1:0] amount,
  input [63:0] data, output reg [63:0] q
);
  always @(posedge clk) begin
    if (load) q <= data;
    else if (ena) begin
      case (amount)
        2'b00: q <= q <<< 8;
        2'b01: q <= q <<< 1;
        2'b10: q <= $signed(q) >>> 8;
        2'b11: q <= $signed(q) >>> 1;
      endcase
    end
  end
endmodule
"""),
    },
    "Prob082_lfsr32": {
        "positive": _module("""
module TopModule (
  input clk, input reset, output reg [31:0] q
);
  always @(posedge clk) begin
    if (reset) q <= 32'h1;
    else if (q[0]) q <= {1'b0, q[31:1]} ^ 32'h80200003;
    else q <= {1'b0, q[31:1]};
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input clk, input reset, output reg [31:0] q
);
  always @(posedge clk) q <= 32'b0;
endmodule
"""),
        "wrong_feedback_tap": _module("""
module TopModule (
  input clk, input reset, output reg [31:0] q
);
  always @(posedge clk) begin
    if (reset) q <= 32'h1;
    else if (q[0]) q <= {1'b0, q[31:1]} ^ 32'h80200001;
    else q <= {1'b0, q[31:1]};
  end
endmodule
"""),
    },
    "Prob086_lfsr5": {
        "positive": _module("""
module TopModule (
  input clk, input reset, output reg [4:0] q
);
  always @(posedge clk) begin
    if (reset) q <= 5'h1;
    else if (q[0]) q <= {1'b0, q[4:1]} ^ 5'b10100;
    else q <= {1'b0, q[4:1]};
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input clk, input reset, output reg [4:0] q
);
  always @(posedge clk) q <= 5'b0;
endmodule
"""),
        "wrong_feedback_tap": _module("""
module TopModule (
  input clk, input reset, output reg [4:0] q
);
  always @(posedge clk) begin
    if (reset) q <= 5'h1;
    else if (q[0]) q <= {1'b0, q[4:1]} ^ 5'b10000;
    else q <= {1'b0, q[4:1]};
  end
endmodule
"""),
    },
    "Prob100_fsm3comb": {
        "positive": _module("""
module TopModule (
  input in, input [1:0] state, output reg [1:0] next_state, output out
);
  always @* begin
    case (state)
      2'b00: next_state = in ? 2'b01 : 2'b00;
      2'b01: next_state = in ? 2'b01 : 2'b10;
      2'b10: next_state = in ? 2'b11 : 2'b00;
      default: next_state = in ? 2'b01 : 2'b10;
    endcase
  end
  assign out = (state == 2'b11);
endmodule
"""),
        "constant_zero": _module("""
module TopModule (
  input in, input [1:0] state, output reg [1:0] next_state, output out
);
  always @* next_state = 2'b0;
  assign out = 1'b0;
endmodule
"""),
        "wrong_fsm_transition": _module("""
module TopModule (
  input in, input [1:0] state, output reg [1:0] next_state, output out
);
  always @* begin
    case (state)
      2'b00: next_state = in ? 2'b00 : 2'b00;
      2'b01: next_state = in ? 2'b01 : 2'b10;
      2'b10: next_state = in ? 2'b11 : 2'b00;
      default: next_state = in ? 2'b01 : 2'b10;
    endcase
  end
  assign out = (state == 2'b11);
endmodule
"""),
    },
}


def _fsm_positive(async_reset: bool, sync_name: str) -> str:
    reset_event = " or posedge areset" if async_reset else ""
    reset_signal = sync_name
    reset_clause = f"if ({reset_signal}) state <= 1'b1;" if async_reset else f"if ({reset_signal}) state <= 1'b1;"
    return _module(f"""
module TopModule (
  input clk, input in, input {sync_name}, output out
);
  reg state;
  always @(posedge clk{reset_event}) begin
    {reset_clause}
    else if (state) state <= in ? 1'b1 : 1'b0;
    else state <= in ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")


def _fsm_missing_reset(async_reset: bool, reset_name: str) -> str:
    event = "posedge clk" if not async_reset else "posedge clk"
    return _module(f"""
module TopModule (
  input clk, input in, input {reset_name}, output out
);
  reg state;
  always @({event}) begin
    if (state) state <= in ? 1'b1 : 1'b0;
    else state <= in ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")


PUBLIC_FIXTURES.update({
    "Prob107_fsm1s": {"positive": _fsm_positive(False, "reset"), "missing_reset": _fsm_missing_reset(False, "reset"), "wrong_fsm_transition": _module("""
module TopModule (input clk, input in, input reset, output out);
  reg state;
  always @(posedge clk) begin
    if (reset) state <= 1'b1;
    else if (state) state <= in ? 1'b0 : 1'b0;
    else state <= in ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")},
    "Prob109_fsm1": {"positive": _fsm_positive(True, "areset"), "missing_reset": _fsm_missing_reset(True, "areset"), "wrong_fsm_transition": _module("""
module TopModule (input clk, input in, input areset, output out);
  reg state;
  always @(posedge clk or posedge areset) begin
    if (areset) state <= 1'b1;
    else if (state) state <= in ? 1'b0 : 1'b0;
    else state <= in ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")},
})


def _fsm2(async_reset: bool, reset_name: str) -> str:
    event = " or posedge areset" if async_reset else ""
    return _module(f"""
module TopModule (
  input clk, input j, input k, input {reset_name}, output out
);
  reg state;
  always @(posedge clk{event}) begin
    if ({reset_name}) state <= 1'b0;
    else if (!state) state <= j ? 1'b1 : 1'b0;
    else state <= k ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")


def _fsm2_missing(reset_name: str) -> str:
    return _module(f"""
module TopModule (input clk, input j, input k, input {reset_name}, output out);
  reg state;
  always @(posedge clk) begin
    if (!state) state <= j ? 1'b1 : 1'b0;
    else state <= k ? 1'b0 : 1'b1;
  end
  assign out = state;
endmodule
""")


def _fsm2_wrong(async_reset: bool, reset_name: str) -> str:
    event = " or posedge areset" if async_reset else ""
    return _module(f"""
module TopModule (input clk, input j, input k, input {reset_name}, output out);
  reg state;
  always @(posedge clk{event}) begin
    if ({reset_name}) state <= 1'b0;
    else if (!state) state <= j ? 1'b1 : 1'b0;
    else state <= k ? 1'b1 : 1'b1;
  end
  assign out = state;
endmodule
""")


PUBLIC_FIXTURES.update({
    "Prob110_fsm2": {"positive": _fsm2(True, "areset"), "missing_reset": _fsm2_missing("areset"), "wrong_fsm_transition": _fsm2_wrong(True, "areset")},
    "Prob111_fsm2s": {"positive": _fsm2(False, "reset"), "missing_reset": _fsm2_missing("reset"), "wrong_fsm_transition": _fsm2_wrong(False, "reset")},
})


PUBLIC_FIXTURES["Prob133_2014_q3fsm"] = {
    "positive": _module("""
module TopModule (
  input clk, input reset, input s, input w, output reg z
);
  reg active;
  reg [1:0] sample_count;
  reg [2:0] ones;
  always @(posedge clk) begin
    if (reset) begin
      active <= 1'b0;
      sample_count <= 2'b0;
      ones <= 3'b0;
      z <= 1'b0;
    end else begin
      z <= 1'b0;
      if (!active) begin
        if (s) begin
          active <= 1'b1;
          sample_count <= 2'b0;
          ones <= 3'b0;
        end
      end else if (sample_count == 2) begin
        z <= ((ones + w) == 2);
        sample_count <= 2'b0;
        ones <= 3'b0;
      end else begin
        sample_count <= sample_count + 1'b1;
        ones <= ones + w;
      end
    end
  end
endmodule
"""),
    "missing_reset": _module("""
module TopModule (
  input clk, input reset, input s, input w, output reg z
);
  reg active;
  reg [1:0] sample_count;
  reg [2:0] ones;
  always @(posedge clk) begin
    z <= 1'b0;
    if (!active) begin
      if (s) begin active <= 1'b1; sample_count <= 0; ones <= 0; end
    end else if (sample_count == 2) begin
      z <= ((ones + w) == 2); sample_count <= 0; ones <= 0;
    end else begin
      sample_count <= sample_count + 1'b1; ones <= ones + w;
    end
  end
endmodule
"""),
    "wrong_sequence_count": _module("""
module TopModule (
  input clk, input reset, input s, input w, output reg z
);
  reg active;
  reg [1:0] sample_count;
  reg [2:0] ones;
  always @(posedge clk) begin
    if (reset) begin active <= 0; sample_count <= 0; ones <= 0; z <= 0; end
    else begin
      z <= 0;
      if (!active) begin if (s) begin active <= 1; sample_count <= 0; ones <= 0; end end
      else if (sample_count == 2) begin z <= ((ones + w) == 1); sample_count <= 0; ones <= 0; end
      else begin sample_count <= sample_count + 1'b1; ones <= ones + w; end
    end
  end
endmodule
"""),
}


def _write_exclusive(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"refusing to replace existing fixture: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _load_inventory_task_ids(inventory_path: Path) -> dict[str, str]:
    if inventory_path.is_symlink() or not inventory_path.is_file():
        raise QualificationError(f"inventory is not a regular file: {inventory_path}")
    rows = []
    for line_number, line in enumerate(
        inventory_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QualificationError(
                f"inventory row {line_number} is not valid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise QualificationError(f"inventory row {line_number} is not an object")
        rows.append(row)
    task_ids = {}
    for row in rows:
        source_id = row.get("source_id")
        task_id = row.get("task_id")
        if not isinstance(source_id, str) or not isinstance(task_id, str):
            continue
        task_ids[source_id] = task_id
    missing = [source_id for source_id in BATCH20_SOURCE_IDS if source_id not in task_ids]
    if missing:
        raise QualificationError(
            "inventory is missing pinned source IDs: " + ", ".join(missing)
        )
    return task_ids


def author(output_root: Path, inventory_path: Path) -> dict[str, object]:
    if output_root.exists() or output_root.is_symlink():
        raise QualificationError(f"authoring output already exists: {output_root}")
    if set(PUBLIC_FIXTURES) != set(BATCH20_SOURCE_IDS):
        raise QualificationError("public fixture catalog does not cover the pinned selection")
    task_ids = _load_inventory_task_ids(inventory_path)
    output_root.mkdir(mode=0o700, parents=True)
    rows = []
    for source_id in BATCH20_SOURCE_IDS:
        fixtures = PUBLIC_FIXTURES[source_id]
        expected_negative_names = list(MUTATION_NAMES[source_id])
        if set(fixtures) != {"positive", *expected_negative_names}:
            raise QualificationError(f"fixture mutation set mismatch: {source_id}")
        source_dir = output_root / source_id
        for name, content in fixtures.items():
            _write_exclusive(source_dir / f"{name}.sv", content)
        rows.append({
            "schema_version": AUTHORING_ROW_SCHEMA_VERSION,
            "source_id": source_id,
            "task_id": task_ids[source_id],
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
                for name in expected_negative_names
            ],
            "reference_used": False,
            "support_files": [],
        })
    manifest = output_root / "authoring_manifest.jsonl"
    _write_exclusive(manifest, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return {"ok": True, "source_ids": list(BATCH20_SOURCE_IDS), "fixture_count": 60, "manifest": manifest.as_posix()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(args.output_root, args.inventory)
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
