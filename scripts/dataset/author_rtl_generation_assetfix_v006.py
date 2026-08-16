#!/usr/bin/env python3
"""Author the batch-A v006 verification overlay from public task records.

This command is intentionally limited to control-plane preparation.  The
positive fixtures, negative mutations, and standalone testbenches below are
authored from the public prompt/interface records for the pinned 24-task
selection.  The command never opens reference RTL or an upstream testbench,
never invokes an HDL tool, and refuses to replace an existing overlay.
"""

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
    static_testbench_audit,
)


CORRECTION_VERSION = "assetfix_v006"
AUTHORING_ROW_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SELECTION_IDS_SHA256 = "cf0b03de6b5c59c481a939d58c780d10995562285850cc2501f0b0c67bc941f0"
SELECTION_REPORT_SHA256 = "e7aaec010b1f11d3d1d13578e70926340f3e65ff340e2f93ca6f85bf0ba6bdc6"

SOURCE_IDS = (
    "Prob007_wire",
    "Prob012_xnorgate",
    "Prob019_m2014_q4f",
    "Prob052_gates100",
    "Prob057_kmap2",
    "Prob059_wire4",
    "Prob065_7420",
    "Prob081_7458",
    "Prob083_mt2015_q4b",
    "Prob090_circuit1",
    "Prob092_gatesv100",
    "Prob094_gatesv",
    "Prob098_circuit7",
    "Prob101_circuit4",
    "Prob102_circuit3",
    "Prob103_circuit2",
    "Prob108_rule90",
    "Prob112_always_case2",
    "Prob116_m2014_q3",
    "Prob117_circuit9",
    "Prob124_rule110",
    "Prob125_kmap3",
    "Prob126_circuit6",
    "Prob130_circuit5",
)


def _sv(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


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


def _tb(declarations: str, ports: str, body: str, prelude: str = "") -> str:
    return _sv(
        f"""
        module tb;
          integer mismatch_count;
          {textwrap.dedent(declarations).strip()}
          {textwrap.dedent(prelude).strip()}
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


def _kmap_057_tb() -> str:
    return _tb(
        "reg [3:0] v; wire out; integer i;\n"
        "function expected; input [3:0] value; begin\n"
        "  case ({value[1],value[0],value[3],value[2]})\n"
        "    4'b0000: expected=1; 4'b0001: expected=1; 4'b0011: expected=0; 4'b0010: expected=1;\n"
        "    4'b0100: expected=1; 4'b0101: expected=0; 4'b0111: expected=0; 4'b0110: expected=1;\n"
        "    4'b1100: expected=0; 4'b1101: expected=1; 4'b1111: expected=1; 4'b1110: expected=1;\n"
        "    4'b1000: expected=1; 4'b1001: expected=1; 4'b1011: expected=0; 4'b1010: expected=0;\n"
        "    default: expected=1'bx;\n"
        "  endcase\nend endfunction",
        ".a(v[3]), .b(v[2]), .c(v[1]), .d(v[0]), .out(out)",
        "for (i=0; i<16; i=i+1) begin v=i; #1; if (out !== expected(v)) mismatch_count=mismatch_count+1; end",
    )


def _kmap_116_tb() -> str:
    return _tb(
        "reg [4:1] x; wire f; integer i;\n"
        "function expected; input [4:1] value; begin\n"
        "  case ({value[3],value[4],value[1],value[2]})\n"
        "    4'b0000: expected=0; 4'b0001: expected=0; 4'b0011: expected=0; 4'b0010: expected=0;\n"
        "    4'b0100: expected=0; 4'b0101: expected=0; 4'b0111: expected=1; 4'b0110: expected=0;\n"
        "    4'b1100: expected=1; 4'b1101: expected=1; 4'b1111: expected=0; 4'b1110: expected=0;\n"
        "    4'b1000: expected=1; 4'b1001: expected=1; 4'b1011: expected=0; 4'b1010: expected=0;\n"
        "    default: expected=1'bx;\n"
        "  endcase\nend endfunction",
        ".x(x), .f(f)",
        "for (i=0; i<16; i=i+1) begin x=i; #1; if (f !== expected(x)) mismatch_count=mismatch_count+1; end",
    )


def _kmap_125_tb() -> str:
    return _tb(
        "reg [3:0] v; wire out; integer i;\n"
        "function expected; input [3:0] value; begin\n"
        "  case ({value[1],value[0],value[3],value[2]})\n"
        "    4'b0001: expected=0; 4'b0000: expected=0; 4'b0010: expected=1; 4'b0011: expected=1;\n"
        "    4'b0101: expected=0; 4'b0100: expected=0; 4'b0110: expected=0; 4'b0111: expected=0;\n"
        "    4'b1101: expected=0; 4'b1100: expected=1; 4'b1110: expected=1; 4'b1111: expected=1;\n"
        "    4'b1001: expected=0; 4'b1000: expected=1; 4'b1010: expected=1; 4'b1011: expected=1;\n"
        "    default: expected=1'bx;\n"
        "  endcase\nend endfunction",
        ".a(v[3]), .b(v[2]), .c(v[1]), .d(v[0]), .out(out)",
        "for (i=0; i<16; i=i+1) begin v=i; #1; if (out !== expected(v)) mismatch_count=mismatch_count+1; end",
    )


def _wide_neighbor_tb(width: int) -> str:
    if width == 100:
        declarations = (
            "reg [99:0] in; wire [98:0] out_both; wire [99:1] out_any; "
            "wire [99:0] out_different; integer i;\n"
            "task check; input [99:0] value; reg [98:0] eb; reg [99:1] ea; "
            "reg [99:0] ed; integer j; begin\n"
            "  in=value; #1; eb=0; ea=0; ed=0;\n"
            "  for (j=0; j<99; j=j+1) eb[j]=value[j]&value[j+1];\n"
            "  for (j=1; j<100; j=j+1) ea[j]=value[j]|value[j-1];\n"
            "  for (j=0; j<100; j=j+1) if (j==99) ed[j]=value[j]^value[0]; else ed[j]=value[j]^value[j+1];\n"
            "  if (out_both !== eb) mismatch_count=mismatch_count+1;\n"
            "  if (out_any !== ea) mismatch_count=mismatch_count+1;\n"
            "  if (out_different !== ed) mismatch_count=mismatch_count+1;\n"
            "end endtask"
        )
        body = (
            "check(100'b0); check({100{1'b1}});\n"
            "for (i=0; i<100; i=i+1) begin in=0; in[i]=1'b1; check(in); end"
        )
        return _tb(declarations, ".in(in), .out_both(out_both), .out_any(out_any), .out_different(out_different)", body)
    declarations = (
        "reg [3:0] in; wire [2:0] out_both; wire [3:1] out_any; "
        "wire [3:0] out_different; integer i;\n"
        "task check; input [3:0] value; reg [2:0] eb; reg [3:1] ea; "
        "reg [3:0] ed; integer j; begin\n"
        "  in=value; #1; eb=0; ea=0; ed=0;\n"
        "  for (j=0; j<3; j=j+1) eb[j]=value[j]&value[j+1];\n"
        "  for (j=1; j<4; j=j+1) ea[j]=value[j]|value[j-1];\n"
        "  for (j=0; j<4; j=j+1) if (j==3) ed[j]=value[j]^value[0]; else ed[j]=value[j]^value[j+1];\n"
        "  if (out_both !== eb) mismatch_count=mismatch_count+1;\n"
        "  if (out_any !== ea) mismatch_count=mismatch_count+1;\n"
        "  if (out_different !== ed) mismatch_count=mismatch_count+1;\n"
        "end endtask"
    )
    return _tb(declarations, ".in(in), .out_both(out_both), .out_any(out_any), .out_different(out_different)", "check(4'b0000); check(4'b1111); check(4'b0101); check(4'b1010); for (i=0; i<4; i=i+1) begin in=0; in[i]=1'b1; check(in); end")


PUBLIC_FIXTURES: dict[str, dict[str, str]] = {
    "Prob007_wire": {
        "positive": _sv("module TopModule(input in, output out); assign out=in; endmodule"),
        "constant_zero": _sv("module TopModule(input in, output out); assign out=1'b0; endmodule"),
        "inverted_wire": _sv("module TopModule(input in, output out); assign out=~in; endmodule"),
    },
    "Prob012_xnorgate": {
        "positive": _sv("module TopModule(input a, input b, output out); assign out=~(a^b); endmodule"),
        "constant_zero": _sv("module TopModule(input a, input b, output out); assign out=1'b0; endmodule"),
        "xor_instead_of_xnor": _sv("module TopModule(input a, input b, output out); assign out=a^b; endmodule"),
    },
    "Prob019_m2014_q4f": {
        "positive": _sv("module TopModule(input in1, input in2, output logic out); assign out=in1 & ~in2; endmodule"),
        "constant_zero": _sv("module TopModule(input in1, input in2, output logic out); assign out=1'b0; endmodule"),
        "missing_input_bubble": _sv("module TopModule(input in1, input in2, output logic out); assign out=in1 & in2; endmodule"),
    },
    "Prob052_gates100": {
        "positive": _sv("module TopModule(input [99:0] in, output out_and, output out_or, output out_xor); assign out_and=&in; assign out_or=|in; assign out_xor=^in; endmodule"),
        "constant_zero": _sv("module TopModule(input [99:0] in, output out_and, output out_or, output out_xor); assign out_and=0; assign out_or=0; assign out_xor=0; endmodule"),
        "wrong_reductions": _sv("module TopModule(input [99:0] in, output out_and, output out_or, output out_xor); assign out_and=|in; assign out_or=&in; assign out_xor=0; endmodule"),
    },
    "Prob057_kmap2": {
        "positive": _sv("""
        module TopModule(input a, input b, input c, input d, output logic out);
          always @* begin
            case ({c,d,a,b})
              4'b0000,4'b0001,4'b0010,4'b0100,4'b0110,4'b1000,4'b1001,4'b1010,4'b1011,4'b1101: out=1'b1;
              default: out=1'b0;
            endcase
          end
        endmodule
        """),
        "wrong_truth_table": _sv("module TopModule(input a, input b, input c, input d, output out); assign out=a^b^c^d; endmodule"),
        "constant_zero": _sv("module TopModule(input a, input b, input c, input d, output out); assign out=1'b0; endmodule"),
    },
    "Prob059_wire4": {
        "positive": _sv("module TopModule(input a, input b, input c, output w, output x, output y, output z); assign w=a; assign x=b; assign y=b; assign z=c; endmodule"),
        "constant_zero": _sv("module TopModule(input a, input b, input c, output w, output x, output y, output z); assign w=0; assign x=0; assign y=0; assign z=0; endmodule"),
        "swapped_wire": _sv("module TopModule(input a, input b, input c, output w, output x, output y, output z); assign w=b; assign x=a; assign y=c; assign z=b; endmodule"),
    },
    "Prob065_7420": {
        "positive": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=~(p1a&p1b&p1c&p1d); assign p2y=~(p2a&p2b&p2c&p2d); endmodule"),
        "constant_zero": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=0; assign p2y=0; endmodule"),
        "nand_as_and": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=p1a&p1b&p1c&p1d; assign p2y=p2a&p2b&p2c&p2d; endmodule"),
    },
    "Prob081_7458": {
        "positive": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,input p1e,input p1f,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=(p1a&p1b&p1c)|(p1d&p1e&p1f); assign p2y=(p2a&p2b)|(p2c&p2d); endmodule"),
        "constant_zero": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,input p1e,input p1f,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=0; assign p2y=0; endmodule"),
        "wrong_grouping": _sv("module TopModule(input p1a,input p1b,input p1c,input p1d,input p1e,input p1f,output p1y,input p2a,input p2b,input p2c,input p2d,output p2y); assign p1y=p1a&p1b&p1c&p1d&p1e&p1f; assign p2y=p2a&p2b&p2c&p2d; endmodule"),
    },
    "Prob083_mt2015_q4b": {
        "positive": _sv("module TopModule(input x, input y, output z); assign z=~(x^y); endmodule"),
        "constant_zero": _sv("module TopModule(input x, input y, output z); assign z=0; endmodule"),
        "xor_instead_of_xnor": _sv("module TopModule(input x, input y, output z); assign z=x^y; endmodule"),
    },
    "Prob090_circuit1": {
        "positive": _sv("module TopModule(input a, input b, output q); assign q=a&b; endmodule"),
        "constant_zero": _sv("module TopModule(input a, input b, output q); assign q=0; endmodule"),
        "or_instead_of_and": _sv("module TopModule(input a, input b, output q); assign q=a|b; endmodule"),
    },
    "Prob092_gatesv100": {
        "positive": _sv("module TopModule(input [99:0] in, output [98:0] out_both, output [99:1] out_any, output [99:0] out_different); assign out_both=in[98:0]&in[99:1]; assign out_any=in[99:1]|in[98:0]; assign out_different=in^{in[0],in[99:1]}; endmodule"),
        "constant_zero": _sv("module TopModule(input [99:0] in, output [98:0] out_both, output [99:1] out_any, output [99:0] out_different); assign out_both=0; assign out_any=0; assign out_different=0; endmodule"),
        "wrong_neighbor_direction": _sv("module TopModule(input [99:0] in, output [98:0] out_both, output [99:1] out_any, output [99:0] out_different); assign out_both=in[98:0]&in[99:1]; assign out_any=in[99:1]; assign out_different=in^{in[0],in[99:1]}; endmodule"),
    },
    "Prob094_gatesv": {
        "positive": _sv("module TopModule(input [3:0] in, output [2:0] out_both, output [3:1] out_any, output [3:0] out_different); assign out_both=in[2:0]&in[3:1]; assign out_any=in[3:1]|in[2:0]; assign out_different=in^{in[0],in[3:1]}; endmodule"),
        "constant_zero": _sv("module TopModule(input [3:0] in, output [2:0] out_both, output [3:1] out_any, output [3:0] out_different); assign out_both=0; assign out_any=0; assign out_different=0; endmodule"),
        "wrong_neighbor_direction": _sv("module TopModule(input [3:0] in, output [2:0] out_both, output [3:1] out_any, output [3:0] out_different); assign out_both=in[2:0]&in[3:1]; assign out_any=in[3:1]; assign out_different=in^{in[0],in[3:1]}; endmodule"),
    },
    "Prob098_circuit7": {
        "positive": _sv("module TopModule(input clk, input a, output reg q); always @(posedge clk) q<=~a; endmodule"),
        "constant_zero": _sv("module TopModule(input clk, input a, output reg q); always @(posedge clk) q<=1'b0; endmodule"),
        "hold_state": _sv("module TopModule(input clk, input a, output reg q); always @(posedge clk) q<=q; endmodule"),
    },
    "Prob101_circuit4": {
        "positive": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=c; endmodule"),
        "constant_zero": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=0; endmodule"),
        "wrong_signal": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=d; endmodule"),
    },
    "Prob102_circuit3": {
        "positive": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=(a|b)&(c|d); endmodule"),
        "constant_zero": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=0; endmodule"),
        "missing_d_term": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=(a|b)&c; endmodule"),
    },
    "Prob103_circuit2": {
        "positive": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=~^(a,b,c,d); endmodule"),
        "constant_zero": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=0; endmodule"),
        "parity_instead_of_even": _sv("module TopModule(input a,input b,input c,input d,output q); assign q=^(a,b,c,d); endmodule"),
    },
    "Prob108_rule90": {
        "positive": _sv("""
        module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q);
          integer i;
          always @(posedge clk) begin
            if (load) q<=data;
            else begin
              q[0]<=q[1];
              for (i=1;i<511;i=i+1) q[i]<=q[i-1]^q[i+1];
              q[511]<=q[510];
            end
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q); always @(posedge clk) q<=0; endmodule"),
        "wrong_neighbor_xor": _sv("module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q); always @(posedge clk) if(load) q<=data; else q<={q[510:0],1'b0}; endmodule"),
    },
    "Prob112_always_case2": {
        "positive": _sv("module TopModule(input [3:0] in, output reg [1:0] pos); always @* begin if(in[3]) pos=2'd3; else if(in[2]) pos=2'd2; else if(in[1]) pos=2'd1; else pos=2'd0; end endmodule"),
        "constant_zero": _sv("module TopModule(input [3:0] in, output reg [1:0] pos); always @* pos=0; endmodule"),
        "highest_bit_priority": _sv("module TopModule(input [3:0] in, output reg [1:0] pos); always @* begin if(in[0]) pos=0; else if(in[1]) pos=1; else if(in[2]) pos=2; else pos=3; end endmodule"),
    },
    "Prob116_m2014_q3": {
        "positive": _sv("""
        module TopModule(input [4:1] x, output logic f);
          always @* begin
            case ({x[3],x[4],x[1],x[2]})
              4'b0111,4'b1100,4'b1101,4'b1000,4'b1001: f=1'b1;
              default: f=1'b0;
            endcase
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input [4:1] x, output logic f); assign f=0; endmodule"),
        "defined_minterm_flip": _sv("module TopModule(input [4:1] x, output logic f); assign f=(x[3]&x[4]); endmodule"),
    },
    "Prob117_circuit9": {
        "positive": _sv("module TopModule(input clk,input a,output reg [2:0] q); always @(posedge clk) if(a) q<=3'd4; else q<=q+1'b1; endmodule"),
        "constant_zero": _sv("module TopModule(input clk,input a,output reg [2:0] q); always @(posedge clk) q<=0; endmodule"),
        "increments_when_held": _sv("module TopModule(input clk,input a,output reg [2:0] q); always @(posedge clk) q<=q+1'b1; endmodule"),
    },
    "Prob124_rule110": {
        "positive": _sv("""
        module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q);
          integer i;
          function rule110; input l; input c; input r; begin case ({l,c,r}) 3'b111:rule110=0; 3'b110:rule110=1; 3'b101:rule110=1; 3'b100:rule110=0; 3'b011:rule110=1; 3'b010:rule110=1; 3'b001:rule110=1; default:rule110=0; endcase end endfunction
          always @(posedge clk) begin
            if (load) q<=data;
            else begin
              q[0]<=rule110(q[1],q[0],1'b0);
              for (i=1;i<511;i=i+1) q[i]<=rule110(q[i+1],q[i],q[i-1]);
              q[511]<=rule110(1'b0,q[511],q[510]);
            end
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q); always @(posedge clk) q<=0; endmodule"),
        "rule90_instead_of_rule110": _sv("module TopModule(input clk,input load,input [511:0] data,output reg [511:0] q); integer i; always @(posedge clk) if(load) q<=data; else begin q[0]<=q[1]; for(i=1;i<511;i=i+1) q[i]<=q[i-1]^q[i+1]; q[511]<=q[510]; end endmodule"),
    },
    "Prob125_kmap3": {
        "positive": _sv("""
        module TopModule(input a,input b,input c,input d,output reg out);
          always @* begin
            case ({c,d,a,b})
              4'b0010,4'b0011,4'b1100,4'b1110,4'b1111,4'b1000,4'b1010,4'b1011: out=1'b1;
              default: out=1'b0;
            endcase
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input a,input b,input c,input d,output reg out); always @* out=0; endmodule"),
        "defined_minterm_flip": _sv("module TopModule(input a,input b,input c,input d,output reg out); always @* out=a|b; endmodule"),
    },
    "Prob126_circuit6": {
        "positive": _sv("""
        module TopModule(input [2:0] a, output reg [15:0] q);
          always @* case(a) 3'd0:q=16'h1232; 3'd1:q=16'haee0; 3'd2:q=16'h27d4; 3'd3:q=16'h5a0e; 3'd4:q=16'h2066; 3'd5:q=16'h64ce; 3'd6:q=16'hc526; default:q=16'h2f19; endcase
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input [2:0] a, output reg [15:0] q); always @* q=0; endmodule"),
        "wrong_case_value": _sv("module TopModule(input [2:0] a, output reg [15:0] q); always @* case(a) 3'd0:q=16'h1232; 3'd1:q=16'haee0; 3'd2:q=16'h27d4; default:q=16'h0; endcase endmodule"),
    },
    "Prob130_circuit5": {
        "positive": _sv("module TopModule(input [3:0] a,input [3:0] b,input [3:0] c,input [3:0] d,input [3:0] e,output reg [3:0] q); always @* case(c) 0:q=b; 1:q=e; 2:q=a; 3:q=d; default:q=4'hf; endcase endmodule"),
        "constant_zero": _sv("module TopModule(input [3:0] a,input [3:0] b,input [3:0] c,input [3:0] d,input [3:0] e,output reg [3:0] q); always @* q=0; endmodule"),
        "wrong_c_selector": _sv("module TopModule(input [3:0] a,input [3:0] b,input [3:0] c,input [3:0] d,input [3:0] e,output reg [3:0] q); always @* case(c) 0:q=a; 1:q=e; 2:q=b; 3:q=d; default:q=4'hf; endcase endmodule"),
    },
}


PUBLIC_TESTBENCHES: dict[str, str] = {
    "Prob007_wire": _tb("reg in; wire out;", ".in(in), .out(out)", "in=0; #1; if(out!==0) mismatch_count=mismatch_count+1; in=1; #1; if(out!==1) mismatch_count=mismatch_count+1;"),
    "Prob012_xnorgate": _tb("reg a,b; wire out; integer i; reg [1:0] v;", ".a(a), .b(b), .out(out)", "for(i=0;i<4;i=i+1) begin v=i; a=v[1]; b=v[0]; #1; if(out !== ~(a^b)) mismatch_count=mismatch_count+1; end"),
    "Prob019_m2014_q4f": _tb("reg in1,in2; wire out; integer i; reg [1:0] v;", ".in1(in1), .in2(in2), .out(out)", "for(i=0;i<4;i=i+1) begin v=i; in1=v[1]; in2=v[0]; #1; if(out !== (in1 & ~in2)) mismatch_count=mismatch_count+1; end"),
    "Prob052_gates100": _tb("reg [99:0] in; wire out_and,out_or,out_xor; integer i;", ".in(in), .out_and(out_and), .out_or(out_or), .out_xor(out_xor)", "in=0; #1; if(out_and!==0 || out_or!==0 || out_xor!==0) mismatch_count=mismatch_count+1; in={100{1'b1}}; #1; if(out_and!==1 || out_or!==1 || out_xor!==0) mismatch_count=mismatch_count+1; for(i=0;i<100;i=i+1) begin in=0; in[i]=1; #1; if(out_and!==0 || out_or!==1 || out_xor!==1) mismatch_count=mismatch_count+1; end"),
    "Prob057_kmap2": _kmap_057_tb(),
    "Prob059_wire4": _tb("reg a,b,c; wire w,x,y,z; integer i; reg [2:0] v;", ".a(a), .b(b), .c(c), .w(w), .x(x), .y(y), .z(z)", "for(i=0;i<8;i=i+1) begin v=i; a=v[2]; b=v[1]; c=v[0]; #1; if(w!==a || x!==b || y!==b || z!==c) mismatch_count=mismatch_count+1; end"),
    "Prob065_7420": _tb("reg p1a,p1b,p1c,p1d,p2a,p2b,p2c,p2d; wire p1y,p2y; integer i; reg [3:0] v;", ".p1a(p1a),.p1b(p1b),.p1c(p1c),.p1d(p1d),.p1y(p1y),.p2a(p2a),.p2b(p2b),.p2c(p2c),.p2d(p2d),.p2y(p2y)", "p2a=1;p2b=1;p2c=1;p2d=1; for(i=0;i<16;i=i+1) begin v=i; p1a=v[3];p1b=v[2];p1c=v[1];p1d=v[0]; #1; if(p1y !== ~(p1a&p1b&p1c&p1d) || p2y!==0) mismatch_count=mismatch_count+1; end p1a=1;p1b=1;p1c=1;p1d=1; for(i=0;i<16;i=i+1) begin v=i; p2a=v[3];p2b=v[2];p2c=v[1];p2d=v[0]; #1; if(p1y!==0 || p2y !== ~(p2a&p2b&p2c&p2d)) mismatch_count=mismatch_count+1; end"),
    "Prob081_7458": _tb("reg [5:0] p1; reg [3:0] p2; wire p1y,p2y; integer i;", ".p1a(p1[5]),.p1b(p1[4]),.p1c(p1[3]),.p1d(p1[2]),.p1e(p1[1]),.p1f(p1[0]),.p1y(p1y),.p2a(p2[3]),.p2b(p2[2]),.p2c(p2[1]),.p2d(p2[0]),.p2y(p2y)", "p2=0; for(i=0;i<64;i=i+1) begin p1=i; #1; if(p1y !== ((p1[5]&p1[4]&p1[3])|(p1[2]&p1[1]&p1[0]))) mismatch_count=mismatch_count+1; end p1=0; for(i=0;i<16;i=i+1) begin p2=i; #1; if(p2y !== ((p2[3]&p2[2])|(p2[1]&p2[0]))) mismatch_count=mismatch_count+1; end"),
    "Prob083_mt2015_q4b": _tb("reg x,y; wire z; integer i; reg [1:0] v;", ".x(x), .y(y), .z(z)", "for(i=0;i<4;i=i+1) begin v=i; x=v[1]; y=v[0]; #1; if(z !== ~(x^y)) mismatch_count=mismatch_count+1; end"),
    "Prob090_circuit1": _tb("reg a,b; wire q; integer i; reg [1:0] v;", ".a(a), .b(b), .q(q)", "for(i=0;i<4;i=i+1) begin v=i; a=v[1]; b=v[0]; #1; if(q !== (a&b)) mismatch_count=mismatch_count+1; end"),
    "Prob092_gatesv100": _wide_neighbor_tb(100),
    "Prob094_gatesv": _wide_neighbor_tb(4),
    "Prob098_circuit7": _tb("reg clk,a; wire q;", ".clk(clk), .a(a), .q(q)", "clk=0; a=0; @(posedge clk); #1; if(q!==1) mismatch_count=mismatch_count+1; a=1; @(posedge clk); #1; if(q!==0) mismatch_count=mismatch_count+1; a=0; @(posedge clk); #1; if(q!==1) mismatch_count=mismatch_count+1;", "always #5 clk=~clk;"),
    "Prob101_circuit4": _tb("reg [3:0] v; wire q; integer i;", ".a(v[3]), .b(v[2]), .c(v[1]), .d(v[0]), .q(q)", "for(i=0;i<16;i=i+1) begin v=i; #1; if(q!==v[1]) mismatch_count=mismatch_count+1; end"),
    "Prob102_circuit3": _tb("reg [3:0] v; wire q; integer i;", ".a(v[3]), .b(v[2]), .c(v[1]), .d(v[0]), .q(q)", "for(i=0;i<16;i=i+1) begin v=i; #1; if(q!==((v[3]|v[2])&(v[1]|v[0]))) mismatch_count=mismatch_count+1; end"),
    "Prob103_circuit2": _tb("reg [3:0] v; wire q; integer i;", ".a(v[3]), .b(v[2]), .c(v[1]), .d(v[0]), .q(q)", "for(i=0;i<16;i=i+1) begin v=i; #1; if(q!==(~^v)) mismatch_count=mismatch_count+1; end"),
    "Prob108_rule90": _tb("reg clk,load; reg [511:0] data,expected,next_expected; wire [511:0] q; integer i,cycle;", ".clk(clk), .load(load), .data(data), .q(q)", "clk=0; data=0; data[0]=1; data[17]=1; data[511]=1; load=1; @(posedge clk); #1; if(q!==data) mismatch_count=mismatch_count+1; load=0; expected=data; for(cycle=0;cycle<3;cycle=cycle+1) begin next_expected=0; next_expected[0]=expected[1]; for(i=1;i<511;i=i+1) next_expected[i]=expected[i-1]^expected[i+1]; next_expected[511]=expected[510]; @(posedge clk); #1; if(q!==next_expected) mismatch_count=mismatch_count+1; expected=next_expected; end", "always #5 clk=~clk;"),
    "Prob112_always_case2": _tb("reg [3:0] in; wire [1:0] pos; integer i; reg [3:0] v;", ".in(in), .pos(pos)", "for(i=0;i<16;i=i+1) begin v=i; in=v; #1; if(in[3] && pos!==3 || !in[3] && in[2] && pos!==2 || !in[3] && !in[2] && in[1] && pos!==1 || !in[3] && !in[2] && !in[1] && pos!==0) mismatch_count=mismatch_count+1; end"),
    "Prob116_m2014_q3": _kmap_116_tb(),
    "Prob117_circuit9": _tb("reg clk,a; wire [2:0] q;", ".clk(clk), .a(a), .q(q)", "clk=0; a=1; @(posedge clk); #1; if(q!==3'd4) mismatch_count=mismatch_count+1; @(posedge clk); #1; if(q!==3'd4) mismatch_count=mismatch_count+1; a=0; @(posedge clk); #1; if(q!==3'd5) mismatch_count=mismatch_count+1; @(posedge clk); #1; if(q!==3'd6) mismatch_count=mismatch_count+1; @(posedge clk); #1; if(q!==3'd0) mismatch_count=mismatch_count+1;", "always #5 clk=~clk;"),
    "Prob124_rule110": _tb("reg clk,load; reg [511:0] data,expected,next_expected; wire [511:0] q; integer i,cycle;\nfunction rule110; input l; input c; input r; begin case ({l,c,r}) 3'b111:rule110=0;3'b110:rule110=1;3'b101:rule110=1;3'b100:rule110=0;3'b011:rule110=1;3'b010:rule110=1;3'b001:rule110=1;default:rule110=0; endcase end endfunction", ".clk(clk), .load(load), .data(data), .q(q)", "clk=0; data=0; data[0]=1; data[5]=1; data[17]=1; data[511]=1; load=1; @(posedge clk); #1; if(q!==data) mismatch_count=mismatch_count+1; load=0; expected=data; for(cycle=0;cycle<3;cycle=cycle+1) begin next_expected=0; next_expected[0]=rule110(expected[1],expected[0],1'b0); for(i=1;i<511;i=i+1) next_expected[i]=rule110(expected[i+1],expected[i],expected[i-1]); next_expected[511]=rule110(1'b0,expected[511],expected[510]); @(posedge clk); #1; if(q!==next_expected) mismatch_count=mismatch_count+1; expected=next_expected; end", "always #5 clk=~clk;"),
    "Prob125_kmap3": _kmap_125_tb(),
    "Prob126_circuit6": _tb("reg [2:0] a; wire [15:0] q; integer i;", ".a(a), .q(q)", "for(i=0;i<8;i=i+1) begin a=i; #1; case(a) 0:if(q!==16'h1232) mismatch_count=mismatch_count+1; 1:if(q!==16'haee0) mismatch_count=mismatch_count+1; 2:if(q!==16'h27d4) mismatch_count=mismatch_count+1; 3:if(q!==16'h5a0e) mismatch_count=mismatch_count+1; 4:if(q!==16'h2066) mismatch_count=mismatch_count+1; 5:if(q!==16'h64ce) mismatch_count=mismatch_count+1; 6:if(q!==16'hc526) mismatch_count=mismatch_count+1; default:if(q!==16'h2f19) mismatch_count=mismatch_count+1; endcase end"),
    "Prob130_circuit5": _tb("reg [3:0] a,b,c,d,e; wire [3:0] q; integer i;", ".a(a), .b(b), .c(c), .d(d), .e(e), .q(q)", "a=4'ha; b=4'hb; d=4'hd; e=4'he; for(i=0;i<16;i=i+1) begin c=i; #1; case(c) 0:if(q!==b) mismatch_count=mismatch_count+1; 1:if(q!==e) mismatch_count=mismatch_count+1; 2:if(q!==a) mismatch_count=mismatch_count+1; 3:if(q!==d) mismatch_count=mismatch_count+1; default:if(q!==4'hf) mismatch_count=mismatch_count+1; endcase end"),
}


def _validate_inputs(*, inventory_path: Path, selection_path: Path, ids_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    inventory_rows = _load_jsonl(inventory_path)
    inventory = {row["source_id"]: row for row in inventory_rows if isinstance(row.get("source_id"), str)}
    selection = _load_json(selection_path)
    selected = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if tuple(selected) != SOURCE_IDS:
        raise ValueError("source IDs do not match pinned assetfix_v006 batch-A order")
    if selection.get("ok") is not True or selection.get("selected_count") != len(SOURCE_IDS):
        raise ValueError("selection report is not the successful 24-task selection")
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
        if source.get("verification_readiness") != "needs_testbench" or source.get("source_commit") != SOURCE_COMMIT or source.get("top_module") != "TopModule":
            raise ValueError(f"source readiness or identity mismatch: {source_id}")
    if set(PUBLIC_TESTBENCHES) != set(SOURCE_IDS) or set(PUBLIC_FIXTURES) != set(SOURCE_IDS):
        raise ValueError("assetfix_v006 catalog is incomplete")
    for source_id in SOURCE_IDS:
        expected = ("positive", *MUTATION_NAMES[source_id])
        if tuple(PUBLIC_FIXTURES[source_id]) != expected:
            raise ValueError(f"fixture catalog order mismatch: {source_id}")
        audit, audit_errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        if audit_errors:
            raise ValueError(f"testbench static audit failed: {source_id}: {audit_errors}")
        if audit.get("support_file_count") != 0:
            raise ValueError(f"support files are not empty: {source_id}")
    return inventory, selection, selected


def author(*, correction_root: Path, inventory_path: Path, selection_path: Path, ids_path: Path) -> dict[str, Any]:
    if correction_root.exists() or correction_root.is_symlink():
        raise ValueError(f"refusing to replace existing overlay: {correction_root}")
    inventory, selection, selected = _validate_inputs(inventory_path=inventory_path, selection_path=selection_path, ids_path=ids_path)
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
        testbench_path = tasks_root / source_id / "testbench.sv"
        _write_exclusive(testbench_path, testbench.encode("utf-8"))
        testbench_hashes[source_id] = _sha256_file(testbench_path)
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            errors = _public_rtl_errors(content)
            if errors:
                raise ValueError(f"fixture privacy/shape audit failed: {source_id}:{name}: {errors}")
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
    _write_exclusive(authoring_manifest, b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in authoring_rows))
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
        "public_specification_hashes": {source_id: inventory[source_id]["source_prompt_sha256"] for source_id in selected},
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
        result = author(correction_root=args.correction_root, inventory_path=args.inventory, selection_path=args.selection, ids_path=args.ids)
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
