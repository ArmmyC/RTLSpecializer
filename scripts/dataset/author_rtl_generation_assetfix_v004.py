#!/usr/bin/env python3
"""Author the v004 public-spec qualification overlay without executing HDL.

This command reads only the pinned public normalization rows and inventory
metadata.  It writes standalone ``tb`` sources plus independently authored
positive and negative candidate fixtures.  It never reads reference RTL or
the upstream testbench files, and it refuses to replace an existing overlay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import MUTATION_NAMES
from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    sha256_file,
)
from scripts.dataset.rtl_generation_asset_qualification import (
    AUTHORING_ROW_SCHEMA_VERSION,
    QualificationError,
)


CORRECTION_VERSION = "assetfix_v004"
SELECTION_REPORT_SHA256 = "cf41966a3bfc347474cbf5c69bdaea5189d9733f263b6fa12fd351d77b756482"
SOURCE_IDS = (
    "Prob004_vector2",
    "Prob006_vectorr",
    "Prob010_mt2015_q4a",
    "Prob015_vector1",
    "Prob026_alwaysblock1",
    "Prob036_ringer",
    "Prob042_vector4",
    "Prob051_gates4",
    "Prob064_vector3",
    "Prob069_truthtable1",
    "Prob070_ece241_2013_q2",
    "Prob087_gates",
    "Prob045_edgedetect2",
    "Prob049_m2014_q4b",
    "Prob054_edgedetect",
    "Prob058_alwaysblock2",
    "Prob074_ece241_2014_q4",
    "Prob088_ece241_2014_q5b",
    "Prob095_review2015_fsmshift",
    "Prob096_review2015_fsmseq",
)


def _module(body: str) -> str:
    return body.strip() + "\n"


PUBLIC_FIXTURES: dict[str, dict[str, str]] = {
    "Prob004_vector2": {
        "positive": _module("""
module TopModule(input logic [31:0] in, output logic [31:0] out);
  assign out = {in[7:0], in[15:8], in[23:16], in[31:24]};
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic [31:0] in, output logic [31:0] out);
  assign out = 32'b0;
endmodule
"""),
        "wrong_byte_order": _module("""
module TopModule(input logic [31:0] in, output logic [31:0] out);
  assign out = {in[31:24], in[23:16], in[15:8], in[7:0]};
endmodule
"""),
    },
    "Prob006_vectorr": {
        "positive": _module("""
module TopModule(input logic [7:0] in, output logic [7:0] out);
  assign out = {in[0], in[1], in[2], in[3], in[4], in[5], in[6], in[7]};
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic [7:0] in, output logic [7:0] out);
  assign out = 8'b0;
endmodule
"""),
        "wrong_bit_order": _module("""
module TopModule(input logic [7:0] in, output logic [7:0] out);
  assign out = in;
endmodule
"""),
    },
    "Prob010_mt2015_q4a": {
        "positive": _module("""
module TopModule(input logic x, input logic y, output logic z);
  assign z = (x ^ y) & x;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic x, input logic y, output logic z);
  assign z = 1'b0;
endmodule
"""),
        "wrong_boolean_expression": _module("""
module TopModule(input logic x, input logic y, output logic z);
  assign z = x ^ y;
endmodule
"""),
    },
    "Prob015_vector1": {
        "positive": _module("""
module TopModule(
  input logic [15:0] in,
  output logic [7:0] out_hi,
  output logic [7:0] out_lo
);
  assign out_hi = in[15:8];
  assign out_lo = in[7:0];
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic [15:0] in,
  output logic [7:0] out_hi,
  output logic [7:0] out_lo
);
  assign out_hi = 8'b0;
  assign out_lo = 8'b0;
endmodule
"""),
        "swapped_halves": _module("""
module TopModule(
  input logic [15:0] in,
  output logic [7:0] out_hi,
  output logic [7:0] out_lo
);
  assign out_hi = in[7:0];
  assign out_lo = in[15:8];
endmodule
"""),
    },
    "Prob026_alwaysblock1": {
        "positive": _module("""
module TopModule(
  input logic a,
  input logic b,
  output logic out_assign,
  output logic out_alwaysblock
);
  assign out_assign = a & b;
  always_comb out_alwaysblock = a & b;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic a,
  input logic b,
  output logic out_assign,
  output logic out_alwaysblock
);
  assign out_assign = 1'b0;
  always_comb out_alwaysblock = 1'b0;
endmodule
"""),
        "always_output_mismatch": _module("""
module TopModule(
  input logic a,
  input logic b,
  output logic out_assign,
  output logic out_alwaysblock
);
  assign out_assign = a & b;
  always_comb out_alwaysblock = a | b;
endmodule
"""),
    },
    "Prob036_ringer": {
        "positive": _module("""
module TopModule(
  input logic ring,
  input logic vibrate_mode,
  output logic ringer,
  output logic motor
);
  assign ringer = ring & ~vibrate_mode;
  assign motor = ring & vibrate_mode;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic ring,
  input logic vibrate_mode,
  output logic ringer,
  output logic motor
);
  assign ringer = 1'b0;
  assign motor = 1'b0;
endmodule
"""),
        "wrong_vibrate_selection": _module("""
module TopModule(
  input logic ring,
  input logic vibrate_mode,
  output logic ringer,
  output logic motor
);
  assign ringer = ring & vibrate_mode;
  assign motor = ring & ~vibrate_mode;
endmodule
"""),
    },
    "Prob042_vector4": {
        "positive": _module("""
module TopModule(input logic [7:0] in, output logic [31:0] out);
  assign out = {{24{in[7]}}, in};
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic [7:0] in, output logic [31:0] out);
  assign out = 32'b0;
endmodule
"""),
        "zero_extend": _module("""
module TopModule(input logic [7:0] in, output logic [31:0] out);
  assign out = {24'b0, in};
endmodule
"""),
    },
    "Prob051_gates4": {
        "positive": _module("""
module TopModule(
  input logic [3:0] in,
  output logic out_and,
  output logic out_or,
  output logic out_xor
);
  assign out_and = &in;
  assign out_or = |in;
  assign out_xor = ^in;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic [3:0] in,
  output logic out_and,
  output logic out_or,
  output logic out_xor
);
  assign out_and = 1'b0;
  assign out_or = 1'b0;
  assign out_xor = 1'b0;
endmodule
"""),
        "wrong_xor": _module("""
module TopModule(
  input logic [3:0] in,
  output logic out_and,
  output logic out_or,
  output logic out_xor
);
  assign out_and = &in;
  assign out_or = |in;
  assign out_xor = ^~in;
endmodule
"""),
    },
    "Prob064_vector3": {
        "positive": _module("""
module TopModule(
  input logic [4:0] a, input logic [4:0] b, input logic [4:0] c,
  input logic [4:0] d, input logic [4:0] e, input logic [4:0] f,
  output logic [7:0] w, output logic [7:0] x,
  output logic [7:0] y, output logic [7:0] z
);
  logic [31:0] packed_value;
  assign packed_value = {a, b, c, d, e, f, 2'b11};
  assign {w, x, y, z} = packed_value;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic [4:0] a, input logic [4:0] b, input logic [4:0] c,
  input logic [4:0] d, input logic [4:0] e, input logic [4:0] f,
  output logic [7:0] w, output logic [7:0] x,
  output logic [7:0] y, output logic [7:0] z
);
  assign {w, x, y, z} = 32'b0;
endmodule
"""),
        "wrong_concat_tail": _module("""
module TopModule(
  input logic [4:0] a, input logic [4:0] b, input logic [4:0] c,
  input logic [4:0] d, input logic [4:0] e, input logic [4:0] f,
  output logic [7:0] w, output logic [7:0] x,
  output logic [7:0] y, output logic [7:0] z
);
  assign {w, x, y, z} = {a, b, c, d, e, f, 2'b00};
endmodule
"""),
    },
    "Prob069_truthtable1": {
        "positive": _module("""
module TopModule(input logic x3, input logic x2, input logic x1, output logic f);
  assign f = (~x3 & x2) | (x3 & x1);
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic x3, input logic x2, input logic x1, output logic f);
  assign f = 1'b0;
endmodule
"""),
        "wrong_truth_table": _module("""
module TopModule(input logic x3, input logic x2, input logic x1, output logic f);
  assign f = (~x3 & x2) | x1;
endmodule
"""),
    },
    "Prob070_ece241_2013_q2": {
        "positive": _module("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d,
  output logic out_sop, output logic out_pos
);
  assign out_sop = (c & d) | (~a & ~b & c);
  assign out_pos = (a | c) & (~b | d) & (~a | c) & (~a | d);
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d,
  output logic out_sop, output logic out_pos
);
  assign out_sop = 1'b0;
  assign out_pos = 1'b0;
endmodule
"""),
        "wrong_boolean_form": _module("""
module TopModule(
  input logic a, input logic b, input logic c, input logic d,
  output logic out_sop, output logic out_pos
);
  assign out_sop = (c & d) | (~a & ~b & c);
  assign out_pos = out_sop;
endmodule
"""),
    },
    "Prob087_gates": {
        "positive": _module("""
module TopModule(
  input logic a, input logic b,
  output logic out_and, output logic out_or, output logic out_xor,
  output logic out_nand, output logic out_nor, output logic out_xnor,
  output logic out_anotb
);
  assign out_and = a & b;
  assign out_or = a | b;
  assign out_xor = a ^ b;
  assign out_nand = ~(a & b);
  assign out_nor = ~(a | b);
  assign out_xnor = ~(a ^ b);
  assign out_anotb = a & ~b;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic a, input logic b,
  output logic out_and, output logic out_or, output logic out_xor,
  output logic out_nand, output logic out_nor, output logic out_xnor,
  output logic out_anotb
);
  assign out_and = 1'b0; assign out_or = 1'b0; assign out_xor = 1'b0;
  assign out_nand = 1'b0; assign out_nor = 1'b0; assign out_xnor = 1'b0;
  assign out_anotb = 1'b0;
endmodule
"""),
        "wrong_nand": _module("""
module TopModule(
  input logic a, input logic b,
  output logic out_and, output logic out_or, output logic out_xor,
  output logic out_nand, output logic out_nor, output logic out_xnor,
  output logic out_anotb
);
  assign out_and = a & b; assign out_or = a | b; assign out_xor = a ^ b;
  assign out_nand = a & b;
  assign out_nor = ~(a | b); assign out_xnor = ~(a ^ b);
  assign out_anotb = a & ~b;
endmodule
"""),
    },
    "Prob045_edgedetect2": {
        "positive": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] anyedge);
  logic [7:0] previous;
  always_ff @(posedge clk) begin
    anyedge <= in ^ previous;
    previous <= in;
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] anyedge);
  always_ff @(posedge clk) anyedge <= 8'b0;
endmodule
"""),
        "same_cycle_edge": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] anyedge);
  logic [7:0] previous;
  always_ff @(posedge clk) begin
    previous <= in;
    anyedge <= in;
  end
endmodule
"""),
    },
    "Prob049_m2014_q4b": {
        "positive": _module("""
module TopModule(input logic clk, input logic ar, input logic d, output logic q);
  always_ff @(posedge clk or posedge ar) begin
    if (ar) q <= 1'b0;
    else q <= d;
  end
endmodule
"""),
        "synchronous_reset": _module("""
module TopModule(input logic clk, input logic ar, input logic d, output logic q);
  always_ff @(posedge clk) begin
    if (ar) q <= 1'b0;
    else q <= d;
  end
endmodule
"""),
        "wrong_data_capture": _module("""
module TopModule(input logic clk, input logic ar, input logic d, output logic q);
  always_ff @(posedge clk or posedge ar) begin
    if (ar) q <= 1'b0;
    else q <= ~d;
  end
endmodule
"""),
    },
    "Prob054_edgedetect": {
        "positive": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] pedge);
  logic [7:0] previous;
  always_ff @(posedge clk) begin
    pedge <= in & ~previous;
    previous <= in;
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] pedge);
  always_ff @(posedge clk) pedge <= 8'b0;
endmodule
"""),
        "same_cycle_pedge": _module("""
module TopModule(input logic clk, input logic [7:0] in, output logic [7:0] pedge);
  logic [7:0] previous;
  always_ff @(posedge clk) begin
    pedge <= in;
    previous <= in;
  end
endmodule
"""),
    },
    "Prob058_alwaysblock2": {
        "positive": _module("""
module TopModule(
  input logic clk, input logic a, input logic b,
  output logic out_assign, output logic out_always_comb,
  output logic out_always_ff
);
  assign out_assign = a ^ b;
  always_comb out_always_comb = a ^ b;
  always_ff @(posedge clk) out_always_ff <= a ^ b;
endmodule
"""),
        "constant_zero": _module("""
module TopModule(
  input logic clk, input logic a, input logic b,
  output logic out_assign, output logic out_always_comb,
  output logic out_always_ff
);
  assign out_assign = 1'b0;
  always_comb out_always_comb = 1'b0;
  always_ff @(posedge clk) out_always_ff <= 1'b0;
endmodule
"""),
        "no_ff_delay": _module("""
module TopModule(
  input logic clk, input logic a, input logic b,
  output logic out_assign, output logic out_always_comb,
  output logic out_always_ff
);
  assign out_assign = a ^ b;
  always_comb out_always_comb = a ^ b;
  assign out_always_ff = a ^ b;
endmodule
"""),
    },
    "Prob074_ece241_2014_q4": {
        "positive": _module("""
module TopModule(input logic clk, input logic x, output logic z);
  logic qx, qa, qo;
  initial begin qx = 1'b0; qa = 1'b0; qo = 1'b0; end
  always_ff @(posedge clk) begin
    qx <= x ^ qx;
    qa <= x & ~qa;
    qo <= x | ~qo;
  end
  assign z = ~(qx | qa | qo);
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic clk, input logic x, output logic z);
  assign z = 1'b0;
endmodule
"""),
        "wrong_gate_feedback": _module("""
module TopModule(input logic clk, input logic x, output logic z);
  logic qx, qa, qo;
  initial begin qx = 1'b0; qa = 1'b0; qo = 1'b0; end
  always_ff @(posedge clk) begin
    qx <= x ^ qx;
    qa <= x & qa;
    qo <= x | ~qo;
  end
  assign z = ~(qx | qa | qo);
endmodule
"""),
    },
    "Prob088_ece241_2014_q5b": {
        "positive": _module("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic [1:0] state;
  always_ff @(posedge clk or posedge areset) begin
    if (areset) state <= 2'b01;
    else if (state == 2'b01 && x) state <= 2'b10;
    else if (state == 2'b01) state <= 2'b01;
    else state <= 2'b10;
  end
  assign z = (state == 2'b01) ? x : ~x;
endmodule
"""),
        "synchronous_reset": _module("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic [1:0] state;
  always_ff @(posedge clk) begin
    if (areset) state <= 2'b01;
    else if (state == 2'b01 && x) state <= 2'b10;
    else if (state == 2'b01) state <= 2'b01;
    else state <= 2'b10;
  end
  assign z = (state == 2'b01) ? x : ~x;
endmodule
"""),
        "wrong_mealy_output": _module("""
module TopModule(input logic clk, input logic areset, input logic x, output logic z);
  logic [1:0] state;
  always_ff @(posedge clk or posedge areset) begin
    if (areset) state <= 2'b01;
    else if (state == 2'b01 && x) state <= 2'b10;
    else if (state == 2'b01) state <= 2'b01;
    else state <= 2'b10;
  end
  assign z = (state == 2'b01) ? x : x;
endmodule
"""),
    },
    "Prob095_review2015_fsmshift": {
        "positive": _module("""
module TopModule(input logic clk, input logic reset, output logic shift_ena);
  logic [2:0] count;
  always_ff @(posedge clk) begin
    if (reset) begin
      count <= 3'd0;
      shift_ena <= 1'b1;
    end else if (count < 3'd3) begin
      count <= count + 3'd1;
      shift_ena <= 1'b1;
    end else begin
      shift_ena <= 1'b0;
    end
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic clk, input logic reset, output logic shift_ena);
  always_ff @(posedge clk) shift_ena <= 1'b0;
endmodule
"""),
        "three_cycles_only": _module("""
module TopModule(input logic clk, input logic reset, output logic shift_ena);
  logic [2:0] count;
  always_ff @(posedge clk) begin
    if (reset) begin count <= 0; shift_ena <= 1; end
    else if (count < 3'd2) begin count <= count + 1; shift_ena <= 1; end
    else shift_ena <= 0;
  end
endmodule
"""),
    },
    "Prob096_review2015_fsmseq": {
        "positive": _module("""
module TopModule(input logic clk, input logic reset, input logic data, output logic start_shifting);
  logic [3:0] history;
  always_ff @(posedge clk) begin
    if (reset) begin
      history <= 4'b0;
      start_shifting <= 1'b0;
    end else begin
      history <= {history[2:0], data};
      if ({history[2:0], data} == 4'b1101) start_shifting <= 1'b1;
    end
  end
endmodule
"""),
        "constant_zero": _module("""
module TopModule(input logic clk, input logic reset, input logic data, output logic start_shifting);
  always_ff @(posedge clk) start_shifting <= 1'b0;
endmodule
"""),
        "clear_on_nonmatch": _module("""
module TopModule(input logic clk, input logic reset, input logic data, output logic start_shifting);
  logic [3:0] history;
  always_ff @(posedge clk) begin
    if (reset) begin history <= 0; start_shifting <= 0; end
    else begin
      history <= {history[2:0], data};
      start_shifting <= ({history[2:0], data} == 4'b1101);
    end
  end
endmodule
"""),
    },
}


PUBLIC_TESTBENCHES: dict[str, str] = {
    "Prob004_vector2": _module("""
module tb;
  logic [31:0] in;
  logic [31:0] out;
  integer mismatch_count;
  TopModule dut(.in(in), .out(out));
  task automatic check(input logic [31:0] value, input logic [31:0] expected);
    begin in = value; #1; if (out !== expected) mismatch_count = mismatch_count + 1; end
  endtask
  initial begin
    mismatch_count = 0;
    check(32'h11223344, 32'h44332211);
    check(32'hdeadbeef, 32'hefbeadde);
    check(32'h00000001, 32'h01000000);
    check(32'h80010002, 32'h02000180);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob006_vectorr": _module("""
module tb;
  logic [7:0] in;
  logic [7:0] out;
  integer mismatch_count;
  TopModule dut(.in(in), .out(out));
  task automatic check(input logic [7:0] value, input logic [7:0] expected);
    begin in = value; #1; if (out !== expected) mismatch_count = mismatch_count + 1; end
  endtask
  initial begin
    mismatch_count = 0;
    check(8'b00000001, 8'b10000000);
    check(8'b10110010, 8'b01001101);
    check(8'b11110000, 8'b00001111);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob010_mt2015_q4a": _module("""
module tb;
  logic x, y, z;
  integer mismatch_count;
  TopModule dut(.x(x), .y(y), .z(z));
  task automatic check(input logic xv, input logic yv, input logic expected);
    begin x = xv; y = yv; #1; if (z !== expected) mismatch_count = mismatch_count + 1; end
  endtask
  initial begin
    mismatch_count = 0;
    check(0, 0, 0); check(0, 1, 0); check(1, 0, 1); check(1, 1, 0);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob015_vector1": _module("""
module tb;
  logic [15:0] in;
  logic [7:0] out_hi, out_lo;
  integer mismatch_count;
  TopModule dut(.in(in), .out_hi(out_hi), .out_lo(out_lo));
  task automatic check(input logic [15:0] value);
    begin
      in = value; #1;
      if (out_hi !== value[15:8] || out_lo !== value[7:0]) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(16'h1234); check(16'habcd); check(16'h00ff); check(16'hff00);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob026_alwaysblock1": _module("""
module tb;
  logic a, b, out_assign, out_alwaysblock;
  integer mismatch_count;
  TopModule dut(.a(a), .b(b), .out_assign(out_assign), .out_alwaysblock(out_alwaysblock));
  task automatic check(input logic av, input logic bv);
    begin
      a = av; b = bv; #1;
      if (out_assign !== (av & bv) || out_alwaysblock !== (av & bv)) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(0, 0); check(0, 1); check(1, 0); check(1, 1);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob036_ringer": _module("""
module tb;
  logic ring, vibrate_mode, ringer, motor;
  integer mismatch_count;
  TopModule dut(.ring(ring), .vibrate_mode(vibrate_mode), .ringer(ringer), .motor(motor));
  task automatic check(input logic rv, input logic vv, input logic er, input logic em);
    begin
      ring = rv; vibrate_mode = vv; #1;
      if (ringer !== er || motor !== em) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(0, 0, 0, 0); check(0, 1, 0, 0); check(1, 0, 1, 0); check(1, 1, 0, 1);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob042_vector4": _module("""
module tb;
  logic [7:0] in;
  logic [31:0] out;
  integer mismatch_count;
  TopModule dut(.in(in), .out(out));
  task automatic check(input logic [7:0] value, input logic [31:0] expected);
    begin in = value; #1; if (out !== expected) mismatch_count = mismatch_count + 1; end
  endtask
  initial begin
    mismatch_count = 0;
    check(8'h05, 32'h00000005); check(8'h80, 32'hffffff80);
    check(8'h7f, 32'h0000007f); check(8'ha5, 32'hffffffa5);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob051_gates4": _module("""
module tb;
  logic [3:0] in;
  logic out_and, out_or, out_xor;
  integer mismatch_count;
  TopModule dut(.in(in), .out_and(out_and), .out_or(out_or), .out_xor(out_xor));
  task automatic check(input logic [3:0] value);
    begin
      in = value; #1;
      if (out_and !== &value || out_or !== |value || out_xor !== ^value) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(4'b0000); check(4'b0001); check(4'b0111); check(4'b1111); check(4'b1010);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob064_vector3": _module("""
module tb;
  logic [4:0] a, b, c, d, e, f;
  logic [7:0] w, x, y, z;
  logic [31:0] expected;
  integer mismatch_count;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .e(e), .f(f), .w(w), .x(x), .y(y), .z(z));
  task automatic check(input logic [4:0] av, input logic [4:0] bv, input logic [4:0] cv,
                       input logic [4:0] dv, input logic [4:0] ev, input logic [4:0] fv);
    begin
      a=av; b=bv; c=cv; d=dv; e=ev; f=fv; expected={av,bv,cv,dv,ev,fv,2'b11}; #1;
      if ({w,x,y,z} !== expected) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(1,2,3,4,5,6); check(31,0,17,9,4,28); check(0,0,0,0,0,0);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob069_truthtable1": _module("""
module tb;
  logic x3, x2, x1, f;
  integer mismatch_count;
  TopModule dut(.x3(x3), .x2(x2), .x1(x1), .f(f));
  task automatic check(input logic [2:0] value, input logic expected);
    begin
      {x3,x2,x1}=value; #1; if (f !== expected) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(3'b000,0); check(3'b001,0); check(3'b010,1); check(3'b101,1);
    check(3'b100,0); check(3'b110,0); check(3'b111,1);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob070_ece241_2013_q2": _module("""
module tb;
  logic a, b, c, d, out_sop, out_pos;
  integer mismatch_count;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .out_sop(out_sop), .out_pos(out_pos));
  task automatic check(input logic [3:0] value, input logic expected);
    begin
      {a,b,c,d}=value; #1;
      if (out_sop !== expected || out_pos !== expected) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0;
    check(4'd0,0); check(4'd1,0); check(4'd2,1); check(4'd4,0);
    check(4'd5,0); check(4'd6,0); check(4'd7,1); check(4'd9,0);
    check(4'd10,0); check(4'd13,0); check(4'd14,0); check(4'd15,1);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob087_gates": _module("""
module tb;
  logic a, b, out_and, out_or, out_xor, out_nand, out_nor, out_xnor, out_anotb;
  integer mismatch_count;
  TopModule dut(.a(a), .b(b), .out_and(out_and), .out_or(out_or), .out_xor(out_xor),
                .out_nand(out_nand), .out_nor(out_nor), .out_xnor(out_xnor), .out_anotb(out_anotb));
  task automatic check(input logic av, input logic bv);
    begin
      a=av; b=bv; #1;
      if (out_and !== (av&bv) || out_or !== (av|bv) || out_xor !== (av^bv) ||
          out_nand !== ~(av&bv) || out_nor !== ~(av|bv) || out_xnor !== ~(av^bv) ||
          out_anotb !== (av&~bv)) mismatch_count = mismatch_count + 1;
    end
  endtask
  initial begin
    mismatch_count = 0; check(0,0); check(0,1); check(1,0); check(1,1);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob045_edgedetect2": _module("""
module tb;
  logic clk;
  logic [7:0] in, anyedge;
  integer mismatch_count;
  TopModule dut(.clk(clk), .in(in), .anyedge(anyedge));
  always #5 clk = ~clk;
  task automatic check(input logic [7:0] value, input logic [7:0] expected);
    begin in=value; @(posedge clk); #1; if (anyedge !== expected) mismatch_count=mismatch_count+1; end
  endtask
  initial begin
    mismatch_count=0; clk=0; in=0;
    @(posedge clk); #1;
    check(8'h00,8'h00); check(8'h01,8'h01); check(8'h03,8'h02); check(8'h03,8'h00); check(8'h80,8'h83);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob049_m2014_q4b": _module("""
module tb;
  logic clk, ar, d, q;
  integer mismatch_count;
  TopModule dut(.clk(clk), .ar(ar), .d(d), .q(q));
  always #5 clk = ~clk;
  initial begin
    mismatch_count=0; clk=0; ar=0; d=0;
    #1 ar=1; #1; if (q !== 0) mismatch_count=mismatch_count+1;
    #1 ar=0; d=1; @(posedge clk); #1; if (q !== 1) mismatch_count=mismatch_count+1;
    d=0; @(posedge clk); #1; if (q !== 0) mismatch_count=mismatch_count+1;
    d=1; #1; ar=1; #1; if (q !== 0) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob054_edgedetect": _module("""
module tb;
  logic clk;
  logic [7:0] in, pedge;
  integer mismatch_count;
  TopModule dut(.clk(clk), .in(in), .pedge(pedge));
  always #5 clk = ~clk;
  task automatic check(input logic [7:0] value, input logic [7:0] expected);
    begin in=value; @(posedge clk); #1; if (pedge !== expected) mismatch_count=mismatch_count+1; end
  endtask
  initial begin
    mismatch_count=0; clk=0; in=0;
    @(posedge clk); #1;
    check(8'h00,8'h00); check(8'h01,8'h01); check(8'h03,8'h02); check(8'h01,8'h00); check(8'h81,8'h80);
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob058_alwaysblock2": _module("""
module tb;
  logic clk, a, b, out_assign, out_always_comb, out_always_ff;
  integer mismatch_count;
  TopModule dut(.clk(clk), .a(a), .b(b), .out_assign(out_assign), .out_always_comb(out_always_comb), .out_always_ff(out_always_ff));
  always #5 clk = ~clk;
  initial begin
    mismatch_count=0; clk=0; a=0; b=0;
    @(posedge clk); #1;
    a=1; b=0; #1;
    if (out_assign !== 1 || out_always_comb !== 1 || out_always_ff !== 0) mismatch_count=mismatch_count+1;
    @(posedge clk); #1; if (out_always_ff !== 1) mismatch_count=mismatch_count+1;
    a=1; b=1; #1; if (out_assign !== 0 || out_always_comb !== 0 || out_always_ff !== 1) mismatch_count=mismatch_count+1;
    @(posedge clk); #1; if (out_always_ff !== 0) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob074_ece241_2014_q4": _module("""
module tb;
  logic clk, x, z;
  integer mismatch_count;
  TopModule dut(.clk(clk), .x(x), .z(z));
  always #5 clk = ~clk;
  initial begin
    mismatch_count=0; clk=0; x=0; #1; if (z !== 1) mismatch_count=mismatch_count+1;
    @(posedge clk); #1; if (z !== 0) mismatch_count=mismatch_count+1;
    x=1; @(posedge clk); #1; if (z !== 0) mismatch_count=mismatch_count+1;
    x=0; @(posedge clk); #1; if (z !== 0) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob088_ece241_2014_q5b": _module("""
module tb;
  logic clk, areset, x, z;
  integer mismatch_count;
  TopModule dut(.clk(clk), .areset(areset), .x(x), .z(z));
  always #5 clk = ~clk;
  initial begin
    mismatch_count=0; clk=0; areset=1; x=0; #1; if (z !== 0) mismatch_count=mismatch_count+1;
    x=1; #1; if (z !== 1) mismatch_count=mismatch_count+1;
    areset=0; x=1; @(posedge clk); #1; if (z !== 0) mismatch_count=mismatch_count+1;
    x=0; #1; if (z !== 1) mismatch_count=mismatch_count+1;
    @(posedge clk); #1; if (z !== 1) mismatch_count=mismatch_count+1;
    x=1; #1; if (z !== 0) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob095_review2015_fsmshift": _module("""
module tb;
  logic clk, reset, shift_ena;
  integer mismatch_count;
  TopModule dut(.clk(clk), .reset(reset), .shift_ena(shift_ena));
  always #5 clk = ~clk;
  task automatic expect_one;
    begin @(posedge clk); #1; if (shift_ena !== 1) mismatch_count=mismatch_count+1; end
  endtask
  initial begin
    mismatch_count=0; clk=0; reset=1; expect_one; reset=0;
    expect_one; expect_one; expect_one;
    @(posedge clk); #1; if (shift_ena !== 0) mismatch_count=mismatch_count+1;
    reset=1; expect_one;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
    "Prob096_review2015_fsmseq": _module("""
module tb;
  logic clk, reset, data, start_shifting;
  integer mismatch_count;
  TopModule dut(.clk(clk), .reset(reset), .data(data), .start_shifting(start_shifting));
  always #5 clk = ~clk;
  task automatic step(input logic value, input logic expected);
    begin data=value; @(posedge clk); #1; if (start_shifting !== expected) mismatch_count=mismatch_count+1; end
  endtask
  initial begin
    mismatch_count=0; clk=0; reset=1; data=0; @(posedge clk); #1; if (start_shifting !== 0) mismatch_count=mismatch_count+1;
    reset=0; step(1,0); step(1,0); step(0,0); step(1,1); step(0,1);
    reset=1; @(posedge clk); #1; if (start_shifting !== 0) mismatch_count=mismatch_count+1;
    $display("Mismatches: %0d", mismatch_count);
    $finish;
  end
endmodule
"""),
}


def _write_exclusive(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"refusing to replace existing fixture: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_ids(path: Path) -> list[str]:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if values != list(SOURCE_IDS):
        raise QualificationError("v004 source IDs do not match the pinned selection order")
    return values


def _validate_public_inputs(
    *,
    inventory_path: Path,
    public_tasks_path: Path,
    selection_path: Path,
    ids_path: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    inventory_rows = _load_jsonl(inventory_path)
    inventory = {row["source_id"]: row for row in inventory_rows if isinstance(row.get("source_id"), str)}
    selected = _load_ids(ids_path)
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if sha256_file(selection_path) != SELECTION_REPORT_SHA256:
        raise QualificationError("selection report hash mismatch")
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION or selection.get("ok") is not True:
        raise QualificationError("selection report is not a successful v004 selection")
    if selection.get("source_commit") != SOURCE_COMMIT or selection.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise QualificationError("selection source binding mismatch")
    if selection.get("base_split_sha256") != BASE_SPLIT_SHA256 or selection.get("correction_version") != CORRECTION_VERSION:
        raise QualificationError("selection split or correction binding mismatch")
    if selection.get("selected_count") != len(selected) or selection.get("selected_count") != 20:
        raise QualificationError("selection count mismatch")
    if selection.get("selection_ids_sha256") != sha256_file(ids_path):
        raise QualificationError("selection ID hash mismatch")
    if [row.get("source_id") for row in selection.get("rows", [])] != selected:
        raise QualificationError("selection row order mismatch")
    tasks = {row["source_id"]: row for row in _load_jsonl(public_tasks_path) if isinstance(row.get("source_id"), str)}
    for source_id in selected:
        source = inventory.get(source_id)
        task = tasks.get(source_id)
        if source is None or task is None:
            raise QualificationError(f"missing public row: {source_id}")
        if source.get("source_prompt_sha256") != hashlib.sha256(str(task.get("prompt", "")).encode("utf-8")).hexdigest():
            raise QualificationError(f"public specification hash mismatch: {source_id}")
        if source.get("task_id") is None or source.get("top_module") != "TopModule":
            raise QualificationError(f"public identity metadata mismatch: {source_id}")
    return {source_id: inventory[source_id] for source_id in selected}, selection


def author(
    *,
    correction_root: Path,
    inventory_path: Path,
    public_tasks_path: Path,
    selection_path: Path,
    ids_path: Path,
) -> dict[str, object]:
    if correction_root.exists() or correction_root.is_symlink():
        raise QualificationError(f"refusing to replace existing overlay: {correction_root}")
    inventory, selection = _validate_public_inputs(
        inventory_path=inventory_path,
        public_tasks_path=public_tasks_path,
        selection_path=selection_path,
        ids_path=ids_path,
    )
    if set(PUBLIC_FIXTURES) != set(SOURCE_IDS) or set(PUBLIC_TESTBENCHES) != set(SOURCE_IDS):
        raise QualificationError("v004 public fixture catalog is incomplete")
    correction_root.mkdir(mode=0o700, parents=True)
    os.chmod(correction_root, 0o700)
    tasks_root = correction_root / "tasks"
    tasks_root.mkdir(mode=0o700)
    os.chmod(tasks_root, 0o700)
    qualification_root = correction_root / "qualification"
    qualification_root.mkdir(mode=0o700)
    os.chmod(qualification_root, 0o700)
    rows: list[dict[str, object]] = []
    selection_hash = sha256_file(selection_path)
    ids_hash = sha256_file(ids_path)
    for source_id in SOURCE_IDS:
        source_dir = tasks_root / source_id
        _write_exclusive(source_dir / "testbench.sv", PUBLIC_TESTBENCHES[source_id])
        fixture_dir = qualification_root / source_id
        fixtures = PUBLIC_FIXTURES[source_id]
        expected_names = ["positive", *MUTATION_NAMES[source_id]]
        if set(fixtures) != set(expected_names):
            raise QualificationError(f"fixture mutation set mismatch: {source_id}")
        for name in expected_names:
            _write_exclusive(fixture_dir / f"{name}.sv", fixtures[name])
        rows.append({
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
    _write_exclusive(authoring_manifest, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_report_sha256": selection_hash,
        "selection_ids_sha256": ids_hash,
        "selected_source_ids": list(SOURCE_IDS),
        "public_specification_hashes": {
            source_id: inventory[source_id]["source_prompt_sha256"] for source_id in SOURCE_IDS
        },
        "testbench_count": 20,
        "positive_case_count": 20,
        "negative_case_count": 40,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "dependency_closure": "pending_static_validation",
        "errors": [],
    }
    _write_exclusive(
        correction_root / "authoring_attestation.json",
        json.dumps(attestation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "authoring_manifest": authoring_manifest.as_posix(),
        "selected_source_ids": list(SOURCE_IDS),
        "testbench_count": 20,
        "positive_case_count": 20,
        "negative_case_count": 40,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "selection_report_sha256": selection_hash,
        "selection_ids_sha256": ids_hash,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--public-tasks", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(
            correction_root=args.correction_root,
            inventory_path=args.inventory,
            public_tasks_path=args.public_tasks,
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
