#!/usr/bin/env python3
"""Author the v005 public-specification verification overlay without HDL execution.

The catalog in this module is deliberately explicit.  Every positive fixture,
negative mutation, and standalone testbench is authored from the public task
specification and interface.  This command never opens reference RTL or the
upstream testbench, never invokes a compiler or simulator, and refuses to
replace an existing overlay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


CORRECTION_VERSION = "assetfix_v005"
AUTHORING_ROW_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SELECTION_IDS_SHA256 = "acc36acecaa09650c544bbac680d30468edb0bdb3996a6801652f6265a7ac1e1"
SELECTION_REPORT_SHA256 = "be820b4bc13d3110580dfae8453fd6770a9ed1b5738b66744d3a129d1b34796c"

SOURCE_IDS = (
    "Prob002_m2014_q4i",
    "Prob003_step_one",
    "Prob005_notgate",
    "Prob008_m2014_q4h",
    "Prob011_norgate",
    "Prob013_m2014_q4e",
    "Prob014_andgate",
    "Prob023_vector100r",
    "Prob028_m2014_q4a",
    "Prob032_vector0",
    "Prob043_vector5",
    "Prob044_vectorgates",
    "Prob050_kmap1",
    "Prob057_kmap2",
    "Prob113_2012_q1g",
    "Prob089_ece241_2014_q5a",
    "Prob091_2012_q2b",
    "Prob119_fsm3",
    "Prob120_fsm3s",
    "Prob121_2014_q3bfsm",
    "Prob128_fsm_ps2",
    "Prob129_ece241_2013_q8",
    "Prob134_2014_q3c",
    "Prob137_fsm_serial",
    "Prob140_fsm_hdlc",
    "Prob143_fsm_onehot",
    "Prob149_ece241_2013_q4",
    "Prob150_review2015_fsmonehot",
    "Prob151_review2015_fsm",
    "Prob156_review2015_fancytimer",
)


def _sv(value: str) -> str:
    return value.strip() + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


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


def _public_rtl_errors(text: str) -> list[str]:
    lowered = text.casefold()
    errors: list[str] = []
    for marker in (
        "`include",
        "refmodule",
        "reference.sv",
        "_ref.sv",
        "private_assets",
        "/home/",
        "/tmp/",
        "/root/",
        "candidate_evidence",
        "testbench",
    ):
        if marker in lowered:
            errors.append(f"forbidden marker: {marker}")
    if lowered.count("module") < 1:
        errors.append("candidate has no module")
    if lowered.count("endmodule") != 1:
        errors.append("candidate must contain exactly one endmodule")
    if "module topmodule" not in lowered:
        errors.append("candidate does not declare TopModule")
    return errors


PUBLIC_FIXTURES: dict[str, dict[str, str]] = {
    "Prob002_m2014_q4i": {
        "positive": _sv("""
module TopModule(output logic out);
  assign out = 1'b0;
endmodule
"""),
        "constant_one": _sv("""
module TopModule(output logic out);
  assign out = 1'b1;
endmodule
"""),
        "unknown_output": _sv("""
module TopModule(output logic out);
  assign out = 1'bx;
endmodule
"""),
    },
    "Prob003_step_one": {
        "positive": _sv("""
module TopModule(output logic one);
  assign one = 1'b1;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(output logic one);
  assign one = 1'b0;
endmodule
"""),
        "unknown_output": _sv("""
module TopModule(output logic one);
  assign one = 1'bx;
endmodule
"""),
    },
    "Prob005_notgate": {
        "positive": _sv("""
module TopModule(input logic in, output logic out);
  assign out = ~in;
endmodule
"""),
        "identity_output": _sv("""
module TopModule(input logic in, output logic out);
  assign out = in;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic in, output logic out);
  assign out = 1'b0;
endmodule
"""),
    },
    "Prob008_m2014_q4h": {
        "positive": _sv("""
module TopModule(input logic in, output logic out);
  assign out = in;
endmodule
"""),
        "inverted_output": _sv("""
module TopModule(input logic in, output logic out);
  assign out = ~in;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic in, output logic out);
  assign out = 1'b0;
endmodule
"""),
    },
    "Prob011_norgate": {
        "positive": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = ~(a | b);
endmodule
"""),
        "wrong_or": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = a | b;
endmodule
"""),
        "wrong_and": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = a & b;
endmodule
"""),
    },
    "Prob013_m2014_q4e": {
        "positive": _sv("""
module TopModule(input logic in1, input logic in2, output logic out);
  assign out = ~(in1 | in2);
endmodule
"""),
        "wrong_or": _sv("""
module TopModule(input logic in1, input logic in2, output logic out);
  assign out = in1 | in2;
endmodule
"""),
        "wrong_and": _sv("""
module TopModule(input logic in1, input logic in2, output logic out);
  assign out = in1 & in2;
endmodule
"""),
    },
    "Prob014_andgate": {
        "positive": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = a & b;
endmodule
"""),
        "wrong_or": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = a | b;
endmodule
"""),
        "wrong_nand": _sv("""
module TopModule(input logic a, input logic b, output logic out);
  assign out = ~(a & b);
endmodule
"""),
    },
    "Prob023_vector100r": {
        "positive": _sv("""
module TopModule(input logic [99:0] in, output logic [99:0] out);
  integer i;
  always_comb begin
    for (i = 0; i < 100; i = i + 1)
      out[99-i] = in[i];
  end
endmodule
"""),
        "identity_order": _sv("""
module TopModule(input logic [99:0] in, output logic [99:0] out);
  assign out = in;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic [99:0] in, output logic [99:0] out);
  assign out = 100'b0;
endmodule
"""),
    },
    "Prob028_m2014_q4a": {
        "positive": _sv("""
module TopModule(input logic d, input logic ena, output logic q);
  always @ (d or ena) begin
    if (ena)
      q <= d;
  end
endmodule
"""),
        "combinational_assign": _sv("""
module TopModule(input logic d, input logic ena, output logic q);
  assign q = d;
endmodule
"""),
        "wrong_enable": _sv("""
module TopModule(input logic d, input logic ena, output logic q);
  always @ (d or ena) begin
    if (!ena)
      q <= d;
  end
endmodule
"""),
    },
    "Prob032_vector0": {
        "positive": _sv("""
module TopModule(
  input logic [2:0] vec,
  output logic [2:0] outv,
  output logic o2,
  output logic o1,
  output logic o0
);
  assign outv = vec;
  assign o2 = vec[2];
  assign o1 = vec[1];
  assign o0 = vec[0];
endmodule
"""),
        "wrong_bit_positions": _sv("""
module TopModule(
  input logic [2:0] vec,
  output logic [2:0] outv,
  output logic o2,
  output logic o1,
  output logic o0
);
  assign outv = vec;
  assign o2 = vec[0];
  assign o1 = vec[1];
  assign o0 = vec[2];
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(
  input logic [2:0] vec,
  output logic [2:0] outv,
  output logic o2,
  output logic o1,
  output logic o0
);
  assign outv = 3'b0;
  assign o2 = 1'b0;
  assign o1 = 1'b0;
  assign o0 = 1'b0;
endmodule
"""),
    },
    "Prob043_vector5": {
        "positive": _sv("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d, input logic e,
  output logic [24:0] out
);
  logic [4:0] values;
  integer i, j;
  always_comb begin
    values = {e, d, c, b, a};
    for (i = 0; i < 5; i = i + 1)
      for (j = 0; j < 5; j = j + 1)
        out[24-(i*5+j)] = (~values[i]) ^ values[j];
  end
endmodule
"""),
        "xor_comparison": _sv("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d, input logic e,
  output logic [24:0] out
);
  logic [4:0] values;
  integer i, j;
  always_comb begin
    values = {e, d, c, b, a};
    for (i = 0; i < 5; i = i + 1)
      for (j = 0; j < 5; j = j + 1)
        out[24-(i*5+j)] = values[i] ^ values[j];
  end
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d, input logic e,
  output logic [24:0] out
);
  assign out = 25'b0;
endmodule
"""),
    },
    "Prob044_vectorgates": {
        "positive": _sv("""
module TopModule(
  input logic [2:0] a, input logic [2:0] b,
  output logic [2:0] out_or_bitwise,
  output logic out_or_logical,
  output logic [5:0] out_not
);
  assign out_or_bitwise = a | b;
  assign out_or_logical = (|a) || (|b);
  assign out_not = {~b, ~a};
endmodule
"""),
        "wrong_inverse_halves": _sv("""
module TopModule(
  input logic [2:0] a, input logic [2:0] b,
  output logic [2:0] out_or_bitwise,
  output logic out_or_logical,
  output logic [5:0] out_not
);
  assign out_or_bitwise = a | b;
  assign out_or_logical = (|a) || (|b);
  assign out_not = {~a, ~b};
endmodule
"""),
        "logical_and": _sv("""
module TopModule(
  input logic [2:0] a, input logic [2:0] b,
  output logic [2:0] out_or_bitwise,
  output logic out_or_logical,
  output logic [5:0] out_not
);
  assign out_or_bitwise = a | b;
  assign out_or_logical = (|a) && (|b);
  assign out_not = {~b, ~a};
endmodule
"""),
    },
    "Prob050_kmap1": {
        "positive": _sv("""
module TopModule(input logic a, input logic b, input logic c, output logic out);
  assign out = a | b | c;
endmodule
"""),
        "wrong_and_reduction": _sv("""
module TopModule(input logic a, input logic b, input logic c, output logic out);
  assign out = a & b & c;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic a, input logic b, input logic c, output logic out);
  assign out = 1'b0;
endmodule
"""),
    },
    "Prob057_kmap2": {
        "positive": _sv("""
module TopModule(input logic a, input logic b, input logic c, input logic d, output logic out);
  function automatic logic truth(input logic [3:0] value);
    case (value)
      4'b0000, 4'b0001, 4'b0010, 4'b0100, 4'b0110, 4'b0111,
      4'b1000, 4'b1001, 4'b1011, 4'b1110: truth = 1'b1;
      default: truth = 1'b0;
    endcase
  endfunction
  assign out = truth({a,b,c,d});
endmodule
"""),
        "wrong_truth_table": _sv("""
module TopModule(input logic a, input logic b, input logic c, input logic d, output logic out);
  function automatic logic truth(input logic [3:0] value);
    case (value)
      4'b0000, 4'b0001, 4'b0010, 4'b0100, 4'b0110, 4'b0111,
      4'b1000, 4'b1001, 4'b1011, 4'b1110: truth = 1'b1;
      default: truth = 1'b0;
    endcase
  endfunction
  assign out = ~truth({a,b,c,d});
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic a, input logic b, input logic c, input logic d, output logic out);
  assign out = 1'b0;
endmodule
"""),
    },
    "Prob113_2012_q1g": {
        "positive": _sv("""
module TopModule(input logic [3:0] x, output logic f);
  function automatic logic truth(input logic [3:0] value);
    case ({value[2],value[3],value[0],value[1]})
      4'b0000, 4'b0010, 4'b1100, 4'b1101, 4'b1111,
      4'b1000, 4'b1001, 4'b1010: truth = 1'b1;
      default: truth = 1'b0;
    endcase
  endfunction
  assign f = truth(x);
endmodule
"""),
        "wrong_truth_table": _sv("""
module TopModule(input logic [3:0] x, output logic f);
  function automatic logic truth(input logic [3:0] value);
    case ({value[2],value[3],value[0],value[1]})
      4'b0000, 4'b0010, 4'b1100, 4'b1101, 4'b1111,
      4'b1000, 4'b1001, 4'b1010: truth = 1'b1;
      default: truth = 1'b0;
    endcase
  endfunction
  assign f = ~truth(x);
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic [3:0] x, output logic f);
  assign f = 1'b0;
endmodule
"""),
    },
    "Prob089_ece241_2014_q5a": {
        "positive": _sv("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic seen_one;
  always_ff @(posedge clk or posedge areset) begin
    if (areset) begin
      seen_one <= 1'b0;
      z <= 1'b0;
    end else begin
      z <= seen_one ? ~x : x;
      seen_one <= seen_one | x;
    end
  end
endmodule
"""),
        "always_invert": _sv("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic seen_one;
  always_ff @(posedge clk or posedge areset) begin
    if (areset) begin seen_one <= 1'b0; z <= 1'b0; end
    else begin z <= ~x; seen_one <= seen_one | x; end
  end
endmodule
"""),
        "never_complement": _sv("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic seen_one;
  always_ff @(posedge clk or posedge areset) begin
    if (areset) begin seen_one <= 1'b0; z <= 1'b0; end
    else begin z <= x; seen_one <= seen_one | x; end
  end
endmodule
"""),
    },
    "Prob091_2012_q2b": {
        "positive": _sv("""
module TopModule(input logic [5:0] y, input logic w, output logic Y1, output logic Y3);
  assign Y1 = y[0] & w;
  assign Y3 = (y[1] | y[2] | y[4]) & ~w;
endmodule
"""),
        "wrong_y3_enable": _sv("""
module TopModule(input logic [5:0] y, input logic w, output logic Y1, output logic Y3);
  assign Y1 = y[0] & w;
  assign Y3 = (y[1] | y[2] | y[4]) & w;
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic [5:0] y, input logic w, output logic Y1, output logic Y3);
  assign Y1 = 1'b0;
  assign Y3 = 1'b0;
endmodule
"""),
    },
    "Prob119_fsm3": {
        "positive": _sv("""
module TopModule(input logic clk, input logic areset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : C;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk or posedge areset)
    if (areset) state <= A; else state <= next_state;
  assign out = (state == D);
endmodule
"""),
        "wrong_transition": _sv("""
module TopModule(input logic clk, input logic areset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : B;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk or posedge areset)
    if (areset) state <= A; else state <= next_state;
  assign out = (state == D);
endmodule
"""),
        "constant_output": _sv("""
module TopModule(input logic clk, input logic areset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : C;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk or posedge areset)
    if (areset) state <= A; else state <= next_state;
  assign out = 1'b0;
endmodule
"""),
    },
    "Prob120_fsm3s": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : C;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk)
    if (reset) state <= A; else state <= next_state;
  assign out = (state == D);
endmodule
"""),
        "wrong_transition": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : B;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk)
    if (reset) state <= A; else state <= next_state;
  assign out = (state == D);
endmodule
"""),
        "missing_reset": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic out);
  typedef enum logic [1:0] {A=2'd0, B=2'd1, C=2'd2, D=2'd3} state_t;
  state_t state, next_state;
  always_comb begin
    case (state)
      A: next_state = in ? B : A;
      B: next_state = in ? B : C;
      C: next_state = in ? D : A;
      default: next_state = in ? B : C;
    endcase
  end
  always_ff @(posedge clk) state <= next_state;
  assign out = (state == D);
endmodule
"""),
    },
    "Prob121_2014_q3bfsm": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic x, output logic z);
  logic [2:0] state, next_state;
  always_comb begin
    case (state)
      3'b000: next_state = x ? 3'b001 : 3'b000;
      3'b001: next_state = x ? 3'b100 : 3'b001;
      3'b010: next_state = x ? 3'b001 : 3'b010;
      3'b011: next_state = x ? 3'b010 : 3'b001;
      default: next_state = x ? 3'b100 : 3'b011;
    endcase
  end
  always_ff @(posedge clk)
    if (reset) state <= 3'b000; else state <= next_state;
  assign z = (state == 3'b011) || (state == 3'b100);
endmodule
"""),
        "wrong_x1_transition": _sv("""
module TopModule(input logic clk, input logic reset, input logic x, output logic z);
  logic [2:0] state, next_state;
  always_comb begin
    case (state)
      3'b000: next_state = x ? 3'b001 : 3'b000;
      3'b001: next_state = x ? 3'b100 : 3'b001;
      3'b010: next_state = x ? 3'b001 : 3'b010;
      3'b011: next_state = x ? 3'b001 : 3'b001;
      default: next_state = x ? 3'b100 : 3'b011;
    endcase
  end
  always_ff @(posedge clk)
    if (reset) state <= 3'b000; else state <= next_state;
  assign z = (state == 3'b011) || (state == 3'b100);
endmodule
"""),
        "constant_zero": _sv("""
module TopModule(input logic clk, input logic reset, input logic x, output logic z);
  logic [2:0] state, next_state;
  always_comb next_state = state;
  always_ff @(posedge clk)
    if (reset) state <= 3'b000; else state <= next_state;
  assign z = 1'b0;
endmodule
"""),
    },
    "Prob128_fsm_ps2": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic [7:0] in, output logic done);
  typedef enum logic [1:0] {SEARCH=2'd0, FIRST=2'd1, SECOND=2'd2, PULSE=2'd3} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) begin state <= SEARCH; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        SEARCH: if (in[3]) state <= FIRST;
        FIRST: state <= SECOND;
        SECOND: state <= PULSE;
        default: begin done <= 1'b1; state <= SEARCH; end
      endcase
    end
  end
endmodule
"""),
        "done_after_two": _sv("""
module TopModule(input logic clk, input logic reset, input logic [7:0] in, output logic done);
  typedef enum logic [1:0] {SEARCH=2'd0, FIRST=2'd1, SECOND=2'd2, PULSE=2'd3} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) begin state <= SEARCH; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        SEARCH: if (in[3]) state <= FIRST;
        FIRST: state <= PULSE;
        SECOND: state <= PULSE;
        default: begin done <= 1'b1; state <= SEARCH; end
      endcase
    end
  end
endmodule
"""),
        "accept_without_header": _sv("""
module TopModule(input logic clk, input logic reset, input logic [7:0] in, output logic done);
  typedef enum logic [1:0] {SEARCH=2'd0, FIRST=2'd1, SECOND=2'd2, PULSE=2'd3} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) begin state <= SEARCH; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        SEARCH: state <= FIRST;
        FIRST: state <= SECOND;
        SECOND: state <= PULSE;
        default: begin done <= 1'b1; state <= SEARCH; end
      endcase
    end
  end
endmodule
"""),
    },
    "Prob129_ece241_2013_q8": {
        "positive": _sv("""
module TopModule(input logic clk, input logic aresetn, input logic x, output logic z);
  typedef enum logic [1:0] {NONE=2'd0, ONE=2'd1, ONEZERO=2'd2} state_t;
  state_t state;
  always_ff @(posedge clk or negedge aresetn) begin
    if (!aresetn) begin state <= NONE; z <= 1'b0; end
    else begin
      z <= (state == ONEZERO) && x;
      case (state)
        NONE: state <= x ? ONE : NONE;
        ONE: state <= x ? ONE : ONEZERO;
        default: state <= x ? ONE : NONE;
      endcase
    end
  end
endmodule
"""),
        "non_overlapping": _sv("""
module TopModule(input logic clk, input logic aresetn, input logic x, output logic z);
  typedef enum logic [1:0] {NONE=2'd0, ONE=2'd1, ONEZERO=2'd2} state_t;
  state_t state;
  always_ff @(posedge clk or negedge aresetn) begin
    if (!aresetn) begin state <= NONE; z <= 1'b0; end
    else begin
      z <= (state == ONEZERO) && x;
      case (state)
        NONE: state <= x ? ONE : NONE;
        ONE: state <= x ? ONE : ONEZERO;
        default: state <= x ? NONE : NONE;
      endcase
    end
  end
endmodule
"""),
        "wrong_output_timing": _sv("""
module TopModule(input logic clk, input logic aresetn, input logic x, output logic z);
  typedef enum logic [1:0] {NONE=2'd0, ONE=2'd1, ONEZERO=2'd2} state_t;
  state_t state;
  always_ff @(posedge clk or negedge aresetn) begin
    if (!aresetn) begin state <= NONE; z <= 1'b0; end
    else begin
      z <= (state == ONE) && x;
      case (state)
        NONE: state <= x ? ONE : NONE;
        ONE: state <= x ? ONE : ONEZERO;
        default: state <= x ? ONE : NONE;
      endcase
    end
  end
endmodule
"""),
    },
    "Prob134_2014_q3c": {
        "positive": _sv("""
module TopModule(input logic clk, input logic x, input logic [2:0] y, output logic Y0, output logic z);
  logic [2:0] next_state;
  always_comb begin
    case (y)
      3'b000: next_state = x ? 3'b001 : 3'b000;
      3'b001: next_state = x ? 3'b100 : 3'b001;
      3'b010: next_state = x ? 3'b001 : 3'b010;
      3'b011: next_state = x ? 3'b010 : 3'b001;
      default: next_state = x ? 3'b100 : 3'b011;
    endcase
    Y0 = next_state[0];
    z = (y == 3'b011) || (y == 3'b100);
  end
endmodule
"""),
        "wrong_x1_transition": _sv("""
module TopModule(input logic clk, input logic x, input logic [2:0] y, output logic Y0, output logic z);
  logic [2:0] next_state;
  always_comb begin
    case (y)
      3'b000: next_state = x ? 3'b001 : 3'b000;
      3'b001: next_state = x ? 3'b100 : 3'b001;
      3'b010: next_state = x ? 3'b001 : 3'b010;
      3'b011: next_state = x ? 3'b001 : 3'b001;
      default: next_state = x ? 3'b100 : 3'b011;
    endcase
    Y0 = next_state[0];
    z = (y == 3'b011) || (y == 3'b100);
  end
endmodule
"""),
        "wrong_output": _sv("""
module TopModule(input logic clk, input logic x, input logic [2:0] y, output logic Y0, output logic z);
  logic [2:0] next_state;
  always_comb begin
    case (y)
      3'b000: next_state = x ? 3'b001 : 3'b000;
      3'b001: next_state = x ? 3'b100 : 3'b001;
      3'b010: next_state = x ? 3'b001 : 3'b010;
      3'b011: next_state = x ? 3'b010 : 3'b001;
      default: next_state = x ? 3'b100 : 3'b011;
    endcase
    Y0 = next_state[0];
    z = (y == 3'b001);
  end
endmodule
"""),
    },
    "Prob137_fsm_serial": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic done);
  typedef enum logic [1:0] {IDLE=2'd0, DATA=2'd1, STOP=2'd2} state_t;
  state_t state;
  logic [3:0] bit_count;
  always_ff @(posedge clk) begin
    if (reset) begin state <= IDLE; bit_count <= 4'd0; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        IDLE: if (!in) begin state <= DATA; bit_count <= 4'd0; end
        DATA: if (bit_count == 4'd7) begin state <= STOP; bit_count <= 4'd0; end
              else bit_count <= bit_count + 1'b1;
        default: if (in) begin done <= 1'b1; state <= IDLE; end
      endcase
    end
  end
endmodule
"""),
        "seven_data_bits": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic done);
  typedef enum logic [1:0] {IDLE=2'd0, DATA=2'd1, STOP=2'd2} state_t;
  state_t state;
  logic [3:0] bit_count;
  always_ff @(posedge clk) begin
    if (reset) begin state <= IDLE; bit_count <= 4'd0; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        IDLE: if (!in) begin state <= DATA; bit_count <= 4'd0; end
        DATA: if (bit_count == 4'd6) begin state <= STOP; bit_count <= 4'd0; end
              else bit_count <= bit_count + 1'b1;
        default: if (in) begin done <= 1'b1; state <= IDLE; end
      endcase
    end
  end
endmodule
"""),
        "accept_zero_stop": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic done);
  typedef enum logic [1:0] {IDLE=2'd0, DATA=2'd1, STOP=2'd2} state_t;
  state_t state;
  logic [3:0] bit_count;
  always_ff @(posedge clk) begin
    if (reset) begin state <= IDLE; bit_count <= 4'd0; done <= 1'b0; end
    else begin
      done <= 1'b0;
      case (state)
        IDLE: if (!in) begin state <= DATA; bit_count <= 4'd0; end
        DATA: if (bit_count == 4'd7) begin state <= STOP; bit_count <= 4'd0; end
              else bit_count <= bit_count + 1'b1;
        default: if (!in) begin done <= 1'b1; state <= IDLE; end
      endcase
    end
  end
endmodule
"""),
    },
    "Prob140_fsm_hdlc": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic disc, output logic flag, output logic err);
  logic [3:0] ones;
  always_ff @(posedge clk) begin
    if (reset) begin ones <= 4'd0; disc <= 1'b0; flag <= 1'b0; err <= 1'b0; end
    else begin
      disc <= 1'b0; flag <= 1'b0; err <= 1'b0;
      if (in) begin
        if (ones >= 4'd6) err <= 1'b1;
        if (ones < 4'd7) ones <= ones + 1'b1;
      end else begin
        if (ones == 4'd5) disc <= 1'b1;
        if (ones == 4'd6) flag <= 1'b1;
        ones <= 4'd0;
      end
    end
  end
endmodule
"""),
        "flag_at_five": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic disc, output logic flag, output logic err);
  logic [3:0] ones;
  always_ff @(posedge clk) begin
    if (reset) begin ones <= 4'd0; disc <= 1'b0; flag <= 1'b0; err <= 1'b0; end
    else begin
      disc <= 1'b0; flag <= 1'b0; err <= 1'b0;
      if (in) begin if (ones >= 4'd6) err <= 1'b1; if (ones < 4'd7) ones <= ones + 1'b1; end
      else begin if (ones == 4'd4) flag <= 1'b1; if (ones == 4'd5) disc <= 1'b1; ones <= 4'd0; end
    end
  end
endmodule
"""),
        "no_error_after_seven": _sv("""
module TopModule(input logic clk, input logic reset, input logic in, output logic disc, output logic flag, output logic err);
  logic [3:0] ones;
  always_ff @(posedge clk) begin
    if (reset) begin ones <= 4'd0; disc <= 1'b0; flag <= 1'b0; err <= 1'b0; end
    else begin
      disc <= 1'b0; flag <= 1'b0; err <= 1'b0;
      if (in) begin if (ones < 4'd7) ones <= ones + 1'b1; end
      else begin if (ones == 4'd5) disc <= 1'b1; if (ones == 4'd6) flag <= 1'b1; ones <= 4'd0; end
    end
  end
endmodule
"""),
    },
    "Prob143_fsm_onehot": {
        "positive": _sv("""
module TopModule(input logic in, input logic [9:0] state, output logic [9:0] next_state, output logic out1, output logic out2);
  always_comb begin
    next_state = 10'b0;
    if (state[0]) next_state[in ? 1 : 0] = 1'b1;
    if (state[1]) next_state[in ? 2 : 0] = 1'b1;
    if (state[2]) next_state[in ? 3 : 0] = 1'b1;
    if (state[3]) next_state[in ? 4 : 0] = 1'b1;
    if (state[4]) next_state[in ? 5 : 0] = 1'b1;
    if (state[5]) next_state[in ? 6 : 8] = 1'b1;
    if (state[6]) next_state[in ? 7 : 9] = 1'b1;
    if (state[7]) next_state[7] = in;
    if (state[8]) next_state[in ? 1 : 0] = 1'b1;
    if (state[9]) next_state[in ? 1 : 0] = 1'b1;
    out1 = state[8] | state[9];
    out2 = state[7] | state[9];
  end
endmodule
"""),
        "wrong_transition": _sv("""
module TopModule(input logic in, input logic [9:0] state, output logic [9:0] next_state, output logic out1, output logic out2);
  always_comb begin
    next_state = 10'b0;
    if (state[0]) next_state[in ? 1 : 0] = 1'b1;
    if (state[1]) next_state[in ? 2 : 0] = 1'b1;
    if (state[2]) next_state[in ? 3 : 0] = 1'b1;
    if (state[3]) next_state[in ? 4 : 0] = 1'b1;
    if (state[4]) next_state[in ? 5 : 0] = 1'b1;
    if (state[5]) next_state[in ? 6 : 0] = 1'b1;
    if (state[6]) next_state[in ? 7 : 9] = 1'b1;
    if (state[7]) next_state[7] = in;
    if (state[8]) next_state[in ? 1 : 0] = 1'b1;
    if (state[9]) next_state[in ? 1 : 0] = 1'b1;
    out1 = state[8] | state[9]; out2 = state[7] | state[9];
  end
endmodule
"""),
        "outputs_zero": _sv("""
module TopModule(input logic in, input logic [9:0] state, output logic [9:0] next_state, output logic out1, output logic out2);
  always_comb begin
    next_state = 10'b0;
    if (state[0]) next_state[in ? 1 : 0] = 1'b1;
    if (state[1]) next_state[in ? 2 : 0] = 1'b1;
    if (state[2]) next_state[in ? 3 : 0] = 1'b1;
    if (state[3]) next_state[in ? 4 : 0] = 1'b1;
    if (state[4]) next_state[in ? 5 : 0] = 1'b1;
    if (state[5]) next_state[in ? 6 : 8] = 1'b1;
    if (state[6]) next_state[in ? 7 : 9] = 1'b1;
    if (state[7]) next_state[7] = in;
    if (state[8]) next_state[in ? 1 : 0] = 1'b1;
    if (state[9]) next_state[in ? 1 : 0] = 1'b1;
    out1 = 1'b0; out2 = 1'b0;
  end
endmodule
"""),
    },
    "Prob149_ece241_2013_q4": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic [2:0] s, output logic fr2, output logic fr1, output logic fr0, output logic dfr);
  logic [2:0] previous_s;
  function automatic logic [1:0] level(input logic [2:0] value);
    case (value)
      3'b111: level = 2'd3;
      3'b011: level = 2'd2;
      3'b001: level = 2'd1;
      default: level = 2'd0;
    endcase
  endfunction
  always_ff @(posedge clk) begin
    if (reset) begin previous_s <= 3'b000; dfr <= 1'b1; end
    else begin dfr <= level(s) > level(previous_s); previous_s <= s; end
  end
  always_comb begin
    case (s)
      3'b111: {fr2,fr1,fr0} = 3'b000;
      3'b011: {fr2,fr1,fr0} = 3'b001;
      3'b001: {fr2,fr1,fr0} = 3'b011;
      default: {fr2,fr1,fr0} = 3'b111;
    endcase
  end
endmodule
"""),
        "no_dfr": _sv("""
module TopModule(input logic clk, input logic reset, input logic [2:0] s, output logic fr2, output logic fr1, output logic fr0, output logic dfr);
  logic [2:0] previous_s;
  function automatic logic [1:0] level(input logic [2:0] value);
    case (value)
      3'b111: level = 2'd3;
      3'b011: level = 2'd2;
      3'b001: level = 2'd1;
      default: level = 2'd0;
    endcase
  endfunction
  always_ff @(posedge clk) begin
    if (reset) previous_s <= 3'b000; else previous_s <= s;
  end
  always_comb begin
    case (s)
      3'b111: {fr2,fr1,fr0} = 3'b000;
      3'b011: {fr2,fr1,fr0} = 3'b001;
      3'b001: {fr2,fr1,fr0} = 3'b011;
      default: {fr2,fr1,fr0} = 3'b111;
    endcase
  end
  assign dfr = 1'b0;
endmodule
"""),
        "wrong_flow_levels": _sv("""
module TopModule(input logic clk, input logic reset, input logic [2:0] s, output logic fr2, output logic fr1, output logic fr0, output logic dfr);
  logic [2:0] previous_s;
  function automatic logic [1:0] level(input logic [2:0] value);
    case (value)
      3'b111: level = 2'd3;
      3'b011: level = 2'd2;
      3'b001: level = 2'd1;
      default: level = 2'd0;
    endcase
  endfunction
  always_ff @(posedge clk) begin
    if (reset) begin previous_s <= 3'b000; dfr <= 1'b1; end
    else begin dfr <= level(s) > level(previous_s); previous_s <= s; end
  end
  always_comb begin
    {fr2,fr1,fr0} = 3'b111;
    if (s == 3'b111) {fr2,fr1,fr0} = 3'b111;
    else if (s == 3'b011) {fr2,fr1,fr0} = 3'b011;
    else if (s == 3'b001) {fr2,fr1,fr0} = 3'b001;
  end
endmodule
"""),
    },
    "Prob150_review2015_fsmonehot": {
        "positive": _sv("""
module TopModule(input logic d, input logic done_counting, input logic ack, input logic [9:0] state, output logic B3_next, output logic S_next, output logic S1_next, output logic Count_next, output logic Wait_next, output logic done, output logic counting, output logic shift_ena);
  always_comb begin
    logic [9:0] n;
    n = 10'b0;
    if (state[0]) n[d ? 1 : 0] = 1'b1;
    if (state[1]) n[d ? 2 : 0] = 1'b1;
    if (state[2]) n[d ? 2 : 3] = 1'b1;
    if (state[3]) n[d ? 4 : 0] = 1'b1;
    if (state[4]) n[5] = 1'b1;
    if (state[5]) n[6] = 1'b1;
    if (state[6]) n[7] = 1'b1;
    if (state[7]) n[8] = 1'b1;
    if (state[8]) n[done_counting ? 9 : 8] = 1'b1;
    if (state[9]) n[ack ? 0 : 9] = 1'b1;
    B3_next = n[7]; S_next = n[0]; S1_next = n[1]; Count_next = n[8]; Wait_next = n[9];
    shift_ena = state[4] | state[5] | state[6] | state[7];
    counting = state[8]; done = state[9];
  end
endmodule
"""),
        "wrong_next_state": _sv("""
module TopModule(input logic d, input logic done_counting, input logic ack, input logic [9:0] state, output logic B3_next, output logic S_next, output logic S1_next, output logic Count_next, output logic Wait_next, output logic done, output logic counting, output logic shift_ena);
  always_comb begin
    logic [9:0] n; n = 10'b0;
    if (state[0]) n[d ? 1 : 0] = 1'b1; if (state[1]) n[d ? 2 : 0] = 1'b1;
    if (state[2]) n[d ? 2 : 3] = 1'b1; if (state[3]) n[d ? 4 : 0] = 1'b1;
    if (state[4]) n[5] = 1'b1; if (state[5]) n[6] = 1'b1; if (state[6]) n[7] = 1'b1;
    if (state[7]) n[8] = 1'b1; if (state[8]) n[done_counting ? 9 : 8] = 1'b1; if (state[9]) n[ack ? 0 : 9] = 1'b1;
    B3_next = n[8]; S_next = n[0]; S1_next = n[1]; Count_next = n[8]; Wait_next = n[9];
    shift_ena = state[4] | state[5] | state[6] | state[7]; counting = state[8]; done = state[9];
  end
endmodule
"""),
        "outputs_zero": _sv("""
module TopModule(input logic d, input logic done_counting, input logic ack, input logic [9:0] state, output logic B3_next, output logic S_next, output logic S1_next, output logic Count_next, output logic Wait_next, output logic done, output logic counting, output logic shift_ena);
  always_comb begin
    logic [9:0] n; n = 10'b0;
    if (state[0]) n[d ? 1 : 0] = 1'b1; if (state[1]) n[d ? 2 : 0] = 1'b1; if (state[2]) n[d ? 2 : 3] = 1'b1;
    if (state[3]) n[d ? 4 : 0] = 1'b1; if (state[4]) n[5] = 1'b1; if (state[5]) n[6] = 1'b1;
    if (state[6]) n[7] = 1'b1; if (state[7]) n[8] = 1'b1; if (state[8]) n[done_counting ? 9 : 8] = 1'b1; if (state[9]) n[ack ? 0 : 9] = 1'b1;
    B3_next=n[7]; S_next=n[0]; S1_next=n[1]; Count_next=n[8]; Wait_next=n[9]; done=1'b0; counting=1'b0; shift_ena=1'b0;
  end
endmodule
"""),
    },
    "Prob151_review2015_fsm": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, input logic done_counting, input logic ack, output logic shift_ena, output logic counting, output logic done);
  typedef enum logic [3:0] {S=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,B0=4'd4,B1=4'd5,B2=4'd6,B3=4'd7,COUNT=4'd8,WAIT=4'd9} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) state <= S;
    else case (state)
      S: state <= data ? S1 : S;
      S1: state <= data ? S11 : S;
      S11: state <= data ? S11 : S110;
      S110: state <= data ? B0 : S;
      B0: state <= B1; B1: state <= B2; B2: state <= B3; B3: state <= COUNT;
      COUNT: state <= done_counting ? WAIT : COUNT;
      default: state <= ack ? S : WAIT;
    endcase
  end
  always_comb begin
    shift_ena = (state >= B0) && (state <= B3);
    counting = (state == COUNT);
    done = (state == WAIT);
  end
endmodule
"""),
        "shift_three_cycles": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, input logic done_counting, input logic ack, output logic shift_ena, output logic counting, output logic done);
  typedef enum logic [3:0] {S=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,B0=4'd4,B1=4'd5,B2=4'd6,B3=4'd7,COUNT=4'd8,WAIT=4'd9} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) state <= S;
    else case (state)
      S: state <= data ? S1 : S; S1: state <= data ? S11 : S; S11: state <= data ? S11 : S110; S110: state <= data ? B0 : S;
      B0: state <= B1; B1: state <= B2; B2: state <= COUNT; B3: state <= COUNT;
      COUNT: state <= done_counting ? WAIT : COUNT; default: state <= ack ? S : WAIT;
    endcase
  end
  assign shift_ena = (state >= B0) && (state <= B3); assign counting = state == COUNT; assign done = state == WAIT;
endmodule
"""),
        "ignore_ack": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, input logic done_counting, input logic ack, output logic shift_ena, output logic counting, output logic done);
  typedef enum logic [3:0] {S=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,B0=4'd4,B1=4'd5,B2=4'd6,B3=4'd7,COUNT=4'd8,WAIT=4'd9} state_t;
  state_t state;
  always_ff @(posedge clk) begin
    if (reset) state <= S;
    else case (state)
      S: state <= data ? S1 : S; S1: state <= data ? S11 : S; S11: state <= data ? S11 : S110; S110: state <= data ? B0 : S;
      B0: state <= B1; B1: state <= B2; B2: state <= B3; B3: state <= COUNT;
      COUNT: state <= done_counting ? WAIT : COUNT; default: state <= S;
    endcase
  end
  assign shift_ena = (state >= B0) && (state <= B3); assign counting = state == COUNT; assign done = state == WAIT;
endmodule
"""),
    },
    "Prob156_review2015_fancytimer": {
        "positive": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, output logic [3:0] count, output logic counting, output logic done, input logic ack);
  typedef enum logic [3:0] {SEARCH=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,SHIFT0=4'd4,SHIFT1=4'd5,SHIFT2=4'd6,SHIFT3=4'd7,COUNT=4'd8,DONE=4'd9} state_t;
  state_t state;
  logic [3:0] delay_value;
  logic [3:0] remaining;
  logic [9:0] cycle_index;
  always_ff @(posedge clk) begin
    if (reset) begin state<=SEARCH; delay_value<=0; remaining<=0; cycle_index<=0; end
    else case (state)
      SEARCH: if (data) state<=S1;
      S1: state <= data ? S11 : SEARCH;
      S11: state <= data ? S11 : S110;
      S110: state <= data ? SHIFT0 : SEARCH;
      SHIFT0: begin delay_value[3]<=data; state<=SHIFT1; end
      SHIFT1: begin delay_value[2]<=data; state<=SHIFT2; end
      SHIFT2: begin delay_value[1]<=data; state<=SHIFT3; end
      SHIFT3: begin delay_value[0]<=data; remaining<={delay_value[2:0],data}; cycle_index<=0; state<=COUNT; end
      COUNT: if (cycle_index==10'd999) begin cycle_index<=0; if (remaining==0) state<=DONE; else remaining<=remaining-1'b1; end
             else cycle_index<=cycle_index+1'b1;
      default: if (ack) state<=SEARCH;
    endcase
  end
  assign count = remaining;
  assign counting = state == COUNT;
  assign done = state == DONE;
endmodule
"""),
        "off_by_one_count": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, output logic [3:0] count, output logic counting, output logic done, input logic ack);
  typedef enum logic [3:0] {SEARCH=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,SHIFT0=4'd4,SHIFT1=4'd5,SHIFT2=4'd6,SHIFT3=4'd7,COUNT=4'd8,DONE=4'd9} state_t;
  state_t state; logic [3:0] delay_value, remaining; logic [9:0] cycle_index;
  always_ff @(posedge clk) begin
    if (reset) begin state<=SEARCH; delay_value<=0; remaining<=0; cycle_index<=0; end
    else case (state)
      SEARCH: if (data) state<=S1; S1: state<=data?S11:SEARCH; S11: state<=data?S11:S110; S110: state<=data?SHIFT0:SEARCH;
      SHIFT0: begin delay_value[3]<=data; state<=SHIFT1; end SHIFT1: begin delay_value[2]<=data; state<=SHIFT2; end
      SHIFT2: begin delay_value[1]<=data; state<=SHIFT3; end SHIFT3: begin delay_value[0]<=data; remaining<={delay_value[2:0],data}; cycle_index<=0; state<=COUNT; end
      COUNT: if (cycle_index==10'd998) begin cycle_index<=0; if (remaining==0) state<=DONE; else remaining<=remaining-1'b1; end else cycle_index<=cycle_index+1'b1;
      default: if (ack) state<=SEARCH;
    endcase
  end
  assign count=remaining; assign counting=state==COUNT; assign done=state==DONE;
endmodule
"""),
        "no_reset": _sv("""
module TopModule(input logic clk, input logic reset, input logic data, output logic [3:0] count, output logic counting, output logic done, input logic ack);
  typedef enum logic [3:0] {SEARCH=4'd0,S1=4'd1,S11=4'd2,S110=4'd3,SHIFT0=4'd4,SHIFT1=4'd5,SHIFT2=4'd6,SHIFT3=4'd7,COUNT=4'd8,DONE=4'd9} state_t;
  state_t state; logic [3:0] delay_value, remaining; logic [9:0] cycle_index;
  always_ff @(posedge clk) begin
    case (state)
      SEARCH: if (data) state<=S1; S1: state<=data?S11:SEARCH; S11: state<=data?S11:S110; S110: state<=data?SHIFT0:SEARCH;
      SHIFT0: begin delay_value[3]<=data; state<=SHIFT1; end SHIFT1: begin delay_value[2]<=data; state<=SHIFT2; end
      SHIFT2: begin delay_value[1]<=data; state<=SHIFT3; end SHIFT3: begin delay_value[0]<=data; remaining<={delay_value[2:0],data}; cycle_index<=0; state<=COUNT; end
      COUNT: if (cycle_index==10'd999) begin cycle_index<=0; if (remaining==0) state<=DONE; else remaining<=remaining-1'b1; end else cycle_index<=cycle_index+1'b1;
      default: if (ack) state<=SEARCH;
    endcase
  end
  assign count=remaining; assign counting=state==COUNT; assign done=state==DONE;
endmodule
"""),
    },
}


PUBLIC_TESTBENCHES: dict[str, str] = {
    "Prob002_m2014_q4i": _sv("""
module tb;
  logic out; integer mismatch_count;
  TopModule dut(.out(out));
  initial begin mismatch_count=0; #1; if (out !== 1'b0) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob003_step_one": _sv("""
module tb;
  logic one; integer mismatch_count;
  TopModule dut(.one(one));
  initial begin mismatch_count=0; #1; if (one !== 1'b1) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob005_notgate": _sv("""
module tb;
  logic in, out; integer mismatch_count; integer v;
  TopModule dut(.in(in), .out(out));
  initial begin mismatch_count=0; for(v=0;v<2;v=v+1) begin in=v; #1; if(out !== ~in) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob008_m2014_q4h": _sv("""
module tb;
  logic in, out; integer mismatch_count; integer v;
  TopModule dut(.in(in), .out(out));
  initial begin mismatch_count=0; for(v=0;v<2;v=v+1) begin in=v; #1; if(out !== in) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob011_norgate": _sv("""
module tb;
  logic a,b,out; integer mismatch_count; integer v;
  TopModule dut(.a(a),.b(b),.out(out));
  initial begin mismatch_count=0; for(v=0;v<4;v=v+1) begin {a,b}=v; #1; if(out !== ~(a|b)) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob013_m2014_q4e": _sv("""
module tb;
  logic in1,in2,out; integer mismatch_count; integer v;
  TopModule dut(.in1(in1),.in2(in2),.out(out));
  initial begin mismatch_count=0; for(v=0;v<4;v=v+1) begin {in1,in2}=v; #1; if(out !== ~(in1|in2)) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob014_andgate": _sv("""
module tb;
  logic a,b,out; integer mismatch_count; integer v;
  TopModule dut(.a(a),.b(b),.out(out));
  initial begin mismatch_count=0; for(v=0;v<4;v=v+1) begin {a,b}=v; #1; if(out !== (a&b)) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob023_vector100r": _sv("""
module tb;
  logic [99:0] in,out; integer mismatch_count; integer i;
  TopModule dut(.in(in),.out(out));
  task automatic check; begin #1; for(i=0;i<100;i=i+1) if(out[99-i] !== in[i]) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; in=100'h0123456789abcdef0123456789a; check; in=100'hfedcba98765432100123456789; check; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob028_m2014_q4a": _sv("""
module tb;
  logic d,ena,q; integer mismatch_count;
  TopModule dut(.d(d),.ena(ena),.q(q));
  initial begin mismatch_count=0; d=0; ena=1; #1; if(q!==0) mismatch_count=mismatch_count+1; d=1; #1; if(q!==1) mismatch_count=mismatch_count+1; ena=0; d=0; #1; if(q!==1) mismatch_count=mismatch_count+1; ena=1; #1; if(q!==0) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob032_vector0": _sv("""
module tb;
  logic [2:0] vec,outv; logic o2,o1,o0; integer mismatch_count; integer v;
  TopModule dut(.vec(vec),.outv(outv),.o2(o2),.o1(o1),.o0(o0));
  initial begin mismatch_count=0; for(v=0;v<8;v=v+1) begin vec=v; #1; if(outv!==vec || o2!==vec[2] || o1!==vec[1] || o0!==vec[0]) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob043_vector5": _sv("""
module tb;
  logic a,b,c,d,e; logic [24:0] out; logic [4:0] values; integer mismatch_count,i,j;
  TopModule dut(.a(a),.b(b),.c(c),.d(d),.e(e),.out(out));
  task automatic check(input logic [4:0] v); begin values=v; a=v[0]; b=v[1]; c=v[2]; d=v[3]; e=v[4]; #1; for(i=0;i<5;i=i+1) for(j=0;j<5;j=j+1) if(out[24-(i*5+j)] !== ((~v[i])^v[j])) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; check(5'b00000); check(5'b10101); check(5'b11010); check(5'b11111); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob044_vectorgates": _sv("""
module tb;
  logic [2:0] a,b; logic [2:0] out_or_bitwise; logic out_or_logical; logic [5:0] out_not; integer mismatch_count; integer v;
  TopModule dut(.a(a),.b(b),.out_or_bitwise(out_or_bitwise),.out_or_logical(out_or_logical),.out_not(out_not));
  initial begin mismatch_count=0; for(v=0;v<16;v=v+1) begin {a,b}=v; #1; if(out_or_bitwise!==(a|b) || out_or_logical!==((|a)||(|b)) || out_not!={~b,~a}) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob050_kmap1": _sv("""
module tb;
  logic a,b,c,out; integer mismatch_count,v;
  TopModule dut(.a(a),.b(b),.c(c),.out(out));
  initial begin mismatch_count=0; for(v=0;v<8;v=v+1) begin {a,b,c}=v; #1; if(out!==(a|b|c)) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob057_kmap2": _sv("""
module tb;
  logic a,b,c,d,out; integer mismatch_count,v;
  function automatic logic expected(input logic [3:0] x); case(x)
    4'b0000,4'b0001,4'b0010,4'b0100,4'b0110,4'b0111,4'b1000,4'b1001,4'b1011,4'b1110: expected=1'b1;
    default: expected=1'b0; endcase endfunction
  TopModule dut(.a(a),.b(b),.c(c),.d(d),.out(out));
  initial begin mismatch_count=0; for(v=0;v<16;v=v+1) begin {a,b,c,d}=v; #1; if(out!==expected(v[3:0])) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob113_2012_q1g": _sv("""
module tb;
  logic [3:0] x; logic f; integer mismatch_count,v;
  function automatic logic expected(input logic [3:0] value); case({value[2],value[3],value[0],value[1]})
    4'b0000,4'b0010,4'b1100,4'b1101,4'b1111,4'b1000,4'b1001,4'b1010: expected=1'b1;
    default: expected=1'b0; endcase endfunction
  TopModule dut(.x(x),.f(f));
  initial begin mismatch_count=0; for(v=0;v<16;v=v+1) begin x=v; #1; if(f!==expected(x)) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob089_ece241_2014_q5a": _sv("""
module tb;
  logic clk,areset,x,z; integer mismatch_count,k; logic seen; logic [4:0] bits;
  TopModule dut(.clk(clk),.areset(areset),.x(x),.z(z));
  always #5 clk=~clk;
  initial begin
    mismatch_count=0; clk=0; areset=1; x=0; #1; if(z!==0) mismatch_count=mismatch_count+1; areset=0; bits=5'b01011; seen=0;
    for(k=0;k<5;k=k+1) begin x=bits[4-k]; @(posedge clk); #1; if(z !== (seen ? ~x : x)) mismatch_count=mismatch_count+1; seen=seen|x; end
    areset=1; #1; if(z!==0) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish;
  end
endmodule
"""),
    "Prob091_2012_q2b": _sv("""
module tb;
  logic [5:0] y; logic w,Y1,Y3; integer mismatch_count,v;
  TopModule dut(.y(y),.w(w),.Y1(Y1),.Y3(Y3));
  initial begin mismatch_count=0; for(v=0;v<12;v=v+1) begin y=6'b000001<<v%6; w=v%2; #1; if(Y1!==(y[0]&w) || Y3!==((y[1]|y[2]|y[4])&~w)) mismatch_count=mismatch_count+1; end y=6'b010010; w=0; #1; if(Y1!==0 || Y3!==1) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob119_fsm3": _sv("""
module tb;
  logic clk,areset,in,out; logic [1:0] state_expected; integer mismatch_count,k; logic [7:0] bits;
  TopModule dut(.clk(clk),.areset(areset),.in(in),.out(out)); always #5 clk=~clk;
  function automatic [1:0] next_state(input [1:0] s,input logic v); case(s) 0:next_state=v?1:0;1:next_state=v?1:2;2:next_state=v?3:0;default:next_state=v?1:2; endcase endfunction
  initial begin mismatch_count=0; clk=0; areset=1; in=0; #1; if(out!==0) mismatch_count=mismatch_count+1; areset=0; state_expected=0; bits=8'b10100110;
    for(k=0;k<8;k=k+1) begin in=bits[7-k]; @(posedge clk); #1; state_expected=next_state(state_expected,in); if(out!==(state_expected==3)) mismatch_count=mismatch_count+1; end
    $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob120_fsm3s": _sv("""
module tb;
  logic clk,reset,in,out; logic [1:0] state_expected; integer mismatch_count,k; logic [7:0] bits;
  TopModule dut(.clk(clk),.reset(reset),.in(in),.out(out)); always #5 clk=~clk;
  function automatic [1:0] next_state(input [1:0] s,input logic v); case(s) 0:next_state=v?1:0;1:next_state=v?1:2;2:next_state=v?3:0;default:next_state=v?1:2; endcase endfunction
  initial begin mismatch_count=0; clk=0; reset=1; in=0; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1; reset=0; state_expected=0; bits=8'b10100110;
    for(k=0;k<8;k=k+1) begin in=bits[7-k]; @(posedge clk); #1; state_expected=next_state(state_expected,in); if(out!==(state_expected==3)) mismatch_count=mismatch_count+1; end
    $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob121_2014_q3bfsm": _sv("""
module tb;
  logic clk,reset,x,z; logic [2:0] state_expected; integer mismatch_count,k; logic [7:0] bits;
  TopModule dut(.clk(clk),.reset(reset),.x(x),.z(z)); always #5 clk=~clk;
  function automatic [2:0] next_state(input [2:0] s,input logic v); case(s) 3'b000:next_state=v?3'b001:3'b000;3'b001:next_state=v?3'b100:3'b001;3'b010:next_state=v?3'b001:3'b010;3'b011:next_state=v?3'b010:3'b001;default:next_state=v?3'b100:3'b011; endcase endfunction
  initial begin mismatch_count=0; clk=0; reset=1; x=0; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1; reset=0; state_expected=0; bits=8'b11010101;
    for(k=0;k<8;k=k+1) begin x=bits[7-k]; @(posedge clk); #1; state_expected=next_state(state_expected,x); if(z!==((state_expected==3'b011)||(state_expected==3'b100))) mismatch_count=mismatch_count+1; end
    $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob128_fsm_ps2": _sv("""
module tb;
  logic clk,reset; logic [7:0] in; logic done; integer mismatch_count;
  TopModule dut(.clk(clk),.reset(reset),.in(in),.done(done)); always #5 clk=~clk;
  task automatic tick(input logic [7:0] value,input logic expected); begin in=value; @(posedge clk); #1; if(done!==expected) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; clk=0; reset=1; tick(8'h00,0); reset=0; tick(8'h00,0); tick(8'h08,0); tick(8'h22,0); tick(8'h10,1); tick(8'h08,0); tick(8'h11,0); tick(8'h22,0); tick(8'h33,1); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob129_ece241_2013_q8": _sv("""
module tb;
  logic clk,aresetn,x,z; integer mismatch_count,k; logic [4:0] bits;
  TopModule dut(.clk(clk),.aresetn(aresetn),.x(x),.z(z)); always #5 clk=~clk;
  initial begin mismatch_count=0; clk=0; aresetn=0; x=0; #1; if(z!==0) mismatch_count=mismatch_count+1; aresetn=1; bits=5'b10101;
    for(k=0;k<5;k=k+1) begin x=bits[4-k]; @(posedge clk); #1; if(z !== ((k==2)||(k==4))) mismatch_count=mismatch_count+1; end
    $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob134_2014_q3c": _sv("""
module tb;
  logic clk,x; logic [2:0] y,next_expected; logic Y0,z; integer mismatch_count,v;
  TopModule dut(.clk(clk),.x(x),.y(y),.Y0(Y0),.z(z));
  function automatic [2:0] next_state(input [2:0] s,input logic b); case(s) 0:next_state=b?1:0;1:next_state=b?4:1;2:next_state=b?1:2;3:next_state=b?2:1;default:next_state=b?4:3; endcase endfunction
  initial begin mismatch_count=0; for(v=0;v<10;v=v+1) begin y=v/2; x=v%2; #1; next_expected=next_state(y,x); if(Y0!==next_expected[0] || z!==((y==3)||(y==4))) mismatch_count=mismatch_count+1; end $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob137_fsm_serial": _sv("""
module tb;
  logic clk,reset,in,done; integer mismatch_count,k; logic [7:0] data_bits;
  TopModule dut(.clk(clk),.reset(reset),.in(in),.done(done)); always #5 clk=~clk;
  task automatic tick(input logic value,input logic expected); begin in=value; @(posedge clk); #1; if(done!==expected) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; clk=0; reset=1; tick(1,0); reset=0; tick(0,0); data_bits=8'b10100110; for(k=0;k<8;k=k+1) tick(data_bits[k],0); tick(1,1); tick(1,0); tick(0,0); tick(0,0); tick(1,1); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob140_fsm_hdlc": _sv("""
module tb;
  logic clk,reset,in,disc,flag,err; integer mismatch_count,k;
  TopModule dut(.clk(clk),.reset(reset),.in(in),.disc(disc),.flag(flag),.err(err)); always #5 clk=~clk;
  task automatic tick(input logic value,input logic expected_disc,input logic expected_flag,input logic expected_err); begin in=value; @(posedge clk); #1; if(disc!==expected_disc || flag!==expected_flag || err!==expected_err) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; clk=0; reset=1; tick(0,0,0,0); reset=0; for(k=0;k<5;k=k+1) tick(1,0,0,0); tick(0,1,0,0); for(k=0;k<6;k=k+1) tick(1,0,0,0); tick(0,0,1,0); for(k=0;k<7;k=k+1) tick(1,0,0, k==6); tick(0,0,0,0); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob143_fsm_onehot": _sv("""
module tb;
  logic in; logic [9:0] state,next_state; logic out1,out2,expected1,expected2; integer mismatch_count,v;
  TopModule dut(.in(in),.state(state),.next_state(next_state),.out1(out1),.out2(out2));
  task automatic check(input logic [9:0] s,input logic b); begin state=s; in=b; #1; expected1=s[8]|s[9]; expected2=s[7]|s[9]; if(out1!==expected1 || out2!==expected2) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; for(v=0;v<10;v=v+1) begin check(10'b1<<v,0); check(10'b1<<v,1); end check(10'b0000010100,1); check(10'b1000000100,0); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob149_ece241_2013_q4": _sv("""
module tb;
  logic clk,reset; logic [2:0] s; logic fr2,fr1,fr0,dfr; integer mismatch_count;
  TopModule dut(.clk(clk),.reset(reset),.s(s),.fr2(fr2),.fr1(fr1),.fr0(fr0),.dfr(dfr)); always #5 clk=~clk;
  task automatic tick(input logic [2:0] value,input logic [2:0] flow,input logic expected_dfr); begin s=value; @(posedge clk); #1; if({fr2,fr1,fr0}!==flow || dfr!==expected_dfr) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; clk=0; reset=1; tick(3'b000,3'b111,1); reset=0; tick(3'b001,3'b011,1); tick(3'b011,3'b001,1); tick(3'b111,3'b000,1); tick(3'b011,3'b001,0); tick(3'b000,3'b111,0); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob150_review2015_fsmonehot": _sv("""
module tb;
  logic d,done_counting,ack; logic [9:0] state; logic B3_next,S_next,S1_next,Count_next,Wait_next,done,counting,shift_ena; integer mismatch_count;
  TopModule dut(.d(d),.done_counting(done_counting),.ack(ack),.state(state),.B3_next(B3_next),.S_next(S_next),.S1_next(S1_next),.Count_next(Count_next),.Wait_next(Wait_next),.done(done),.counting(counting),.shift_ena(shift_ena));
  task automatic check(input logic [9:0] s,input logic b,input logic dc,input logic a,input logic [4:0] expected_next); begin state=s; d=b; done_counting=dc; ack=a; #1; if({B3_next,Count_next,Wait_next,S1_next,S_next}!==expected_next) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; check(10'b0000000001,0,0,0,5'b00001); check(10'b0000000001,1,0,0,5'b00100); check(10'b0000000010,1,0,0,5'b00000); check(10'b0000010000,0,0,0,5'b00000); state=10'b0000010000; d=0; done_counting=0; ack=0; #1; if(!shift_ena) mismatch_count=mismatch_count+1; state=10'b0100000000; done_counting=1; #1; if(!done || done!==1'b0) mismatch_count=mismatch_count+1; state=10'b1000000000; ack=1; #1; if(!S_next) mismatch_count=mismatch_count+1; $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob151_review2015_fsm": _sv("""
module tb;
  logic clk,reset,data,done_counting,ack,shift_ena,counting,done; integer mismatch_count,k; logic [3:0] bits;
  TopModule dut(.clk(clk),.reset(reset),.data(data),.done_counting(done_counting),.ack(ack),.shift_ena(shift_ena),.counting(counting),.done(done)); always #5 clk=~clk;
  task automatic tick(input logic value,input logic dc,input logic a,input logic es,input logic ec,input logic ed); begin data=value; done_counting=dc; ack=a; @(posedge clk); #1; if(shift_ena!==es || counting!==ec || done!==ed) mismatch_count=mismatch_count+1; end endtask
  initial begin mismatch_count=0; clk=0; reset=1; tick(0,0,0,0,0,0); reset=0; bits=4'b1101; for(k=0;k<4;k=k+1) tick(bits[3-k],0,0,k==3,0,0); tick(0,0,0,1,0,0); tick(0,0,0,1,0,0); tick(0,0,0,1,0,0); tick(0,0,0,0,1,0); tick(0,1,0,0,0,1); tick(0,0,0,0,0,1); tick(0,0,1,0,0,0); $display("Mismatches: %0d", mismatch_count); $finish; end
endmodule
"""),
    "Prob156_review2015_fancytimer": _sv("""
module tb;
  logic clk,reset,data,ack; logic [3:0] count; logic counting,done; integer mismatch_count,k;
  TopModule dut(.clk(clk),.reset(reset),.data(data),.count(count),.counting(counting),.done(done),.ack(ack)); always #5 clk=~clk;
  task automatic tick(input logic value); begin data=value; @(posedge clk); #1; end endtask
  initial begin
    mismatch_count=0; clk=0; reset=1; ack=0; tick(0); reset=0;
    tick(1); tick(1); tick(0); tick(1); tick(0); tick(0); tick(0); tick(1);
    if(!counting || count!==1) mismatch_count=mismatch_count+1;
    for(k=0;k<999;k=k+1) begin @(posedge clk); #1; if(!counting || count!==1) mismatch_count=mismatch_count+1; end
    @(posedge clk); #1; if(!counting || count!==0) mismatch_count=mismatch_count+1;
    for(k=0;k<999;k=k+1) begin @(posedge clk); #1; if(!counting || count!==0) mismatch_count=mismatch_count+1; end
    @(posedge clk); #1; if(!done || counting) mismatch_count=mismatch_count+1;
    ack=1; @(posedge clk); #1; if(done) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count); $finish;
  end
endmodule
"""),
}


def _validate_inputs(
    *,
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    inventory_rows = _load_jsonl(inventory_path)
    inventory = {row["source_id"]: row for row in inventory_rows if isinstance(row.get("source_id"), str)}
    selection = _load_json(selection_path)
    selected = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if tuple(selected) != SOURCE_IDS:
        raise ValueError("source IDs do not match the pinned v005 order")
    if selection.get("ok") is not True or selection.get("selected_count") != 30:
        raise ValueError("selection report is not the successful 30-task selection")
    if selection.get("source_commit") != SOURCE_COMMIT or selection.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise ValueError("selection source binding mismatch")
    if selection.get("base_split_sha256") != SPLIT_SHA256 or selection.get("correction_version") != CORRECTION_VERSION:
        raise ValueError("selection split or correction binding mismatch")
    if _sha256_file(selection_path) != SELECTION_REPORT_SHA256:
        raise ValueError("selection report hash mismatch")
    if _sha256_file(ids_path) != SELECTION_IDS_SHA256:
        raise ValueError("selection IDs hash mismatch")
    if _sha256_file(inventory_path) != INVENTORY_SHA256:
        raise ValueError("inventory hash mismatch")
    if [row.get("source_id") for row in selection.get("rows", [])] != selected:
        raise ValueError("selection row order mismatch")
    for source_id in selected:
        source = inventory.get(source_id)
        if source is None:
            raise ValueError(f"source ID missing from inventory: {source_id}")
        if source.get("verification_readiness") != "needs_testbench":
            raise ValueError(f"source is not needs_testbench: {source_id}")
        if source.get("source_commit") != SOURCE_COMMIT or source.get("top_module") != "TopModule":
            raise ValueError(f"source identity mismatch: {source_id}")
    if set(PUBLIC_TESTBENCHES) != set(SOURCE_IDS) or set(PUBLIC_FIXTURES) != set(SOURCE_IDS):
        raise ValueError("v005 catalog is incomplete")
    for source_id in SOURCE_IDS:
        expected = ("positive", *MUTATION_NAMES[source_id])
        if tuple(PUBLIC_FIXTURES[source_id]) != expected:
            raise ValueError(f"fixture catalog order mismatch: {source_id}")
    return inventory, selection, selected


def author(
    *,
    correction_root: Path,
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
) -> dict[str, Any]:
    if correction_root.exists() or correction_root.is_symlink():
        raise ValueError(f"refusing to replace existing overlay: {correction_root}")
    inventory, selection, selected = _validate_inputs(
        inventory_path=inventory_path,
        selection_path=selection_path,
        ids_path=ids_path,
    )
    correction_root.mkdir(mode=0o700, parents=True)
    tasks_root = correction_root / "tasks"
    qualification_root = correction_root / "qualification"
    tasks_root.mkdir(mode=0o700)
    qualification_root.mkdir(mode=0o700)
    authoring_rows: list[dict[str, Any]] = []
    testbench_hashes: dict[str, str] = {}
    fixture_hashes: dict[str, dict[str, str]] = {}
    for source_id in selected:
        testbench = PUBLIC_TESTBENCHES[source_id]
        audit, audit_errors = static_testbench_audit(testbench.encode("utf-8"))
        if audit_errors:
            raise ValueError(f"testbench static audit failed before writing {source_id}: {audit_errors}")
        source_dir = tasks_root / source_id
        testbench_path = source_dir / "testbench.sv"
        _write_exclusive(testbench_path, testbench.encode("utf-8"))
        testbench_hashes[source_id] = _sha256_file(testbench_path)
        fixture_dir = qualification_root / source_id
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            errors = _public_rtl_errors(content)
            if errors:
                raise ValueError(f"fixture static privacy/shape audit failed: {source_id}:{name}: {errors}")
            fixture_path = fixture_dir / f"{name}.sv"
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
    _write_exclusive(
        authoring_manifest,
        b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in authoring_rows),
    )
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": INVENTORY_SHA256,
        "frozen_split_sha256": SPLIT_SHA256,
        "selection_ids_sha256": _sha256_file(ids_path),
        "selection_report_sha256": _sha256_file(selection_path),
        "selected_source_ids": selected,
        "selected_count": len(selected),
        "public_specification_hashes": {
            source_id: inventory[source_id]["source_prompt_sha256"] for source_id in selected
        },
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": len(selected) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "dependency_closure": "pending_static_validation",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    _write_exclusive(attestation_path, (json.dumps(attestation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "authoring_manifest": authoring_manifest.as_posix(),
        "authoring_attestation": attestation_path.as_posix(),
        "selected_source_ids": selected,
        "selected_count": len(selected),
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": len(selected) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "selection_ids_sha256": _sha256_file(ids_path),
        "selection_report_sha256": _sha256_file(selection_path),
        "testbench_hashes": testbench_hashes,
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
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
