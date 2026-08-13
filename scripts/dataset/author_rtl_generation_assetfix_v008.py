#!/usr/bin/env python3
"""Author the final train-remainder verification overlay from public prompts.

This is a control-plane preparation command.  The authored testbenches and
qualification fixtures below are derived from the public task specifications
only.  The command never reads reference RTL or an upstream testbench and
never invokes an HDL tool.
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

from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


CORRECTION_VERSION = "assetfix_v008"
AUTHORING_ROW_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SELECTION_IDS_SHA256 = "8fc4bd300d5b921730dd20919518588ba29a26553daaaa1abee00dd95e7e21a5"
SELECTION_REPORT_SHA256 = "50b3d14f130a0402ba3d47f41695b00ea84afc4a265f1a770936feeca1a38ff9"

SOURCE_IDS = (
    "Prob117_circuit9",
    "Prob131_mt2015_q4",
    "Prob144_conwaylife",
    "Prob145_circuit8",
    "Prob149_ece241_2013_q4",
    "Prob099_m2014_q6c",
    "Prob120_fsm3s",
    "Prob127_lemmings1",
    "Prob135_m2014_q6b",
    "Prob136_m2014_q6",
    "Prob137_fsm_serial",
    "Prob138_2012_q2fsm",
    "Prob139_2013_q2bfsm",
    "Prob142_lemmings2",
    "Prob146_fsm_serialdata",
    "Prob148_2013_q2afsm",
    "Prob150_review2015_fsmonehot",
    "Prob152_lemmings3",
    "Prob154_fsm_ps2data",
    "Prob155_lemmings4",
)

RETRY_SOURCE_IDS = frozenset({
    "Prob117_circuit9",
    "Prob120_fsm3s",
    "Prob137_fsm_serial",
    "Prob150_review2015_fsmonehot",
})


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


def _tb(declarations: str, ports: str, body: str, clock: str = "") -> str:
    return _sv(
        f"""
        module tb;
          integer mismatch_count;
          {textwrap.dedent(declarations).strip()}
          TopModule dut ({ports});
          {clock}
          initial begin
            mismatch_count = 0;
        {textwrap.indent(textwrap.dedent(body).strip(), "    ")}
            $display("Mismatches: %0d", mismatch_count);
            $finish;
          end
        endmodule
        """
    )


def _fsm_binary_tb(transitions: str, output: str, ports: str) -> str:
    return _tb(
        "reg clk, reset, w; reg [2:0] expected_state; wire z; integer i;",
        ports,
        f"""
        clk=0; reset=1; w=0; @(posedge clk); #1;
        if (z !== 1'b0) mismatch_count=mismatch_count+1;
        reset=0;
        for (i=0; i<12; i=i+1) begin
          w = (i % 3) == 1;
          @(posedge clk); #1;
          if (z !== {output}) mismatch_count=mismatch_count+1;
        end
        """,
        "always #5 clk=~clk;",
    )


PUBLIC_TESTBENCHES: dict[str, str] = {
    "Prob117_circuit9": _tb(
        "reg clk, a; wire [2:0] q;",
        ".clk(clk), .a(a), .q(q)",
        """
        clk=0; a=1; @(posedge clk); #1; if(q!==3'd4) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(q!==3'd4) mismatch_count=mismatch_count+1;
        a=0; @(posedge clk); #1; if(q!==3'd5) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(q!==3'd6) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(q!==3'd7) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(q!==3'd0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob131_mt2015_q4": _tb(
        "reg x, y; wire z; integer i; reg a_value, b_value, expected;",
        ".x(x), .y(y), .z(z)",
        """
        for (i=0; i<4; i=i+1) begin
          x=i[1]; y=i[0]; #1;
          a_value=(x^y)&x; b_value=~(x^y); expected=(a_value|b_value)^(a_value&b_value);
          if (z!==expected) mismatch_count=mismatch_count+1;
        end
        """,
    ),
    "Prob144_conwaylife": _tb(
        "reg clk, load; reg [255:0] data; wire [255:0] q;",
        ".clk(clk), .load(load), .data(data), .q(q)",
        """
        clk=0; data=0; load=1; @(posedge clk); #1;
        if (q!==data) mismatch_count=mismatch_count+1;
        load=0; data=0; data[119]=1; @(posedge clk); #1;
        if (q!==0) mismatch_count=mismatch_count+1;
        data=0; data[17]=1; data[18]=1; data[33]=1; data[34]=1; load=1;
        @(posedge clk); #1; load=0; @(posedge clk); #1;
        if (q!==data) mismatch_count=mismatch_count+1;
        data=0; data[17]=1; data[18]=1; data[33]=1; data[34]=1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob145_circuit8": _tb(
        "reg clock, a; wire p, q;",
        ".clock(clock), .a(a), .p(p), .q(q)",
        """
        clock=0; a=0; #1; clock=1; #1; if(p!==0) mismatch_count=mismatch_count+1;
        a=1; #1; if(p!==1) mismatch_count=mismatch_count+1;
        clock=0; #1; if(q!==1) mismatch_count=mismatch_count+1;
        a=0; #1; if(q!==1) mismatch_count=mismatch_count+1;
        clock=1; #1; if(p!==0) mismatch_count=mismatch_count+1;
        """,
    ),
    "Prob149_ece241_2013_q4": _tb(
        "reg clk, reset; reg [2:0] s; wire fr2, fr1, fr0, dfr;\n"
        "task automatic sample; input [2:0] value; input [2:0] expected_flow; input expected_dfr;\n"
        "begin s=value; @(posedge clk); #1; if({fr2,fr1,fr0}!==expected_flow || dfr!==expected_dfr) mismatch_count=mismatch_count+1; end endtask",
        ".clk(clk), .reset(reset), .s(s), .fr2(fr2), .fr1(fr1), .fr0(fr0), .dfr(dfr)",
        """
        clk=0; reset=1; s=0; sample(3'b000,3'b111,1'b1); reset=0;
        sample(3'b111,3'b000,1'b1); sample(3'b011,3'b001,1'b0);
        sample(3'b001,3'b011,1'b0); sample(3'b000,3'b111,1'b0);
        sample(3'b001,3'b011,1'b1); sample(3'b011,3'b001,1'b1);
        """,
        "always #5 clk=~clk;",
    ),
    "Prob099_m2014_q6c": _tb(
        "reg [5:0] y; reg w; wire Y1, Y3; integer i;",
        ".y(y), .w(w), .Y1(Y1), .Y3(Y3)",
        """
        for (i=0; i<6; i=i+1) begin
          y=6'b1<<i; w=0; #1;
          if (Y1 !== (i==0) || Y3 !== 1'b0) mismatch_count=mismatch_count+1;
          w=1; #1;
          if (Y1 !== 1'b0 || Y3 !== ((i==1)||(i==2)||(i==4)||(i==5))) mismatch_count=mismatch_count+1;
        end
        """,
    ),
    "Prob120_fsm3s": _tb(
        "reg clk, reset, in; wire out;",
        ".clk(clk), .reset(reset), .in(in), .out(out)",
        """
        clk=0; reset=1; in=0; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1;
        reset=0; in=1; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1;
        in=0; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1;
        in=1; @(posedge clk); #1; if(out!==1) mismatch_count=mismatch_count+1;
        in=0; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1;
        in=0; @(posedge clk); #1; if(out!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob127_lemmings1": _tb(
        "reg clk, areset, bump_left, bump_right; wire walk_left, walk_right;",
        ".clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .walk_left(walk_left), .walk_right(walk_right)",
        """
        clk=0; areset=1; bump_left=0; bump_right=0; #1;
        if(walk_left!==1 || walk_right!==0) mismatch_count=mismatch_count+1;
        areset=0; bump_left=1; @(posedge clk); #1;
        if(walk_left!==0 || walk_right!==1) mismatch_count=mismatch_count+1;
        bump_left=0; bump_right=1; @(posedge clk); #1;
        if(walk_left!==1 || walk_right!==0) mismatch_count=mismatch_count+1;
        bump_left=1; bump_right=1; @(posedge clk); #1;
        if(walk_left!==0 || walk_right!==1) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob135_m2014_q6b": _tb(
        "reg [2:0] y; reg w; wire Y1; integer i;",
        ".y(y), .w(w), .Y1(Y1)",
        """
        for (i=0; i<6; i=i+1) begin
          y=i; w=0; #1;
          if (Y1 !== ((i==4)||(i==5))) mismatch_count=mismatch_count+1;
          w=1; #1;
          if (Y1 !== ((i==2)||(i==4))) mismatch_count=mismatch_count+1;
        end
        """,
    ),
    "Prob136_m2014_q6": _tb(
        "reg clk, reset, w; wire z;",
        ".clk(clk), .reset(reset), .w(w), .z(z)",
        """
        clk=0; reset=1; w=0; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        reset=0; w=0; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(z!==1) mismatch_count=mismatch_count+1;
        w=1; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob137_fsm_serial": _tb(
        "reg clk, reset, in; wire done; reg [7:0] bits; integer k;\n"
        "task automatic tick; input value; begin in=value; @(posedge clk); #1; end endtask",
        ".clk(clk), .reset(reset), .in(in), .done(done)",
        """
        clk=0; reset=1; in=1; tick(1); reset=0; bits=8'b10100110;
        tick(0); tick(1); for(k=0;k<8;k=k+1) tick(bits[k]); tick(1);
        if(done!==1) mismatch_count=mismatch_count+1; tick(1); if(done!==0) mismatch_count=mismatch_count+1;
        tick(0); tick(1); for(k=0;k<8;k=k+1) tick(bits[k]); tick(0);
        if(done!==0) mismatch_count=mismatch_count+1; tick(0); if(done!==0) mismatch_count=mismatch_count+1;
        tick(1); if(done!==1) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob138_2012_q2fsm": _tb(
        "reg clk, reset, w; wire z;",
        ".clk(clk), .reset(reset), .w(w), .z(z)",
        """
        clk=0; reset=1; w=0; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        reset=0; w=1; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(z!==1) mismatch_count=mismatch_count+1;
        w=0; @(posedge clk); #1; if(z!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob139_2013_q2bfsm": _tb(
        "reg clk, resetn, x, y; wire f, g;",
        ".clk(clk), .resetn(resetn), .x(x), .y(y), .f(f), .g(g)",
        """
        clk=0; resetn=0; x=0; y=0; @(posedge clk); #1; if(f!==0 || g!==0) mismatch_count=mismatch_count+1;
        resetn=1; @(posedge clk); #1; if(f!==1 || g!==0) mismatch_count=mismatch_count+1;
        @(posedge clk); #1; if(f!==0 || g!==0) mismatch_count=mismatch_count+1;
        x=1; @(posedge clk); #1; x=0; @(posedge clk); #1; x=1; @(posedge clk); #1;
        if(g!==1) mismatch_count=mismatch_count+1;
        y=1; @(posedge clk); #1; if(g!==1) mismatch_count=mismatch_count+1;
        y=0; @(posedge clk); #1; if(g!==1) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob142_lemmings2": _tb(
        "reg clk, areset, bump_left, bump_right, ground; wire walk_left, walk_right, aaah;",
        ".clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .ground(ground), .walk_left(walk_left), .walk_right(walk_right), .aaah(aaah)",
        """
        clk=0; areset=1; bump_left=0; bump_right=0; ground=1; #1;
        if(walk_left!==1 || walk_right!==0 || aaah!==0) mismatch_count=mismatch_count+1;
        areset=0; bump_left=1; @(posedge clk); #1;
        if(walk_left!==0 || walk_right!==1) mismatch_count=mismatch_count+1;
        bump_left=0; ground=0; @(posedge clk); #1;
        if(aaah!==1 || walk_left!==0 || walk_right!==0) mismatch_count=mismatch_count+1;
        bump_right=1; ground=1; @(posedge clk); #1;
        if(walk_right!==1 || aaah!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob146_fsm_serialdata": _tb(
        "reg clk, reset, in; wire [7:0] out_byte; wire done; reg [7:0] bits; integer k;\n"
        "task automatic tick; input value; begin in=value; @(posedge clk); #1; end endtask",
        ".clk(clk), .in(in), .reset(reset), .out_byte(out_byte), .done(done)",
        """
        clk=0; reset=1; in=1; tick(1); reset=0; bits=8'ha6;
        tick(0); tick(1); for(k=0;k<8;k=k+1) tick(bits[k]); tick(1);
        if(done!==1 || out_byte!==bits) mismatch_count=mismatch_count+1;
        tick(0); tick(1); for(k=0;k<8;k=k+1) tick(bits[k]); tick(0); if(done!==0) mismatch_count=mismatch_count+1;
        tick(1); if(done!==1 || out_byte!==bits) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob148_2013_q2afsm": _tb(
        "reg clk, resetn; reg [2:0] r; wire [2:0] g;",
        ".clk(clk), .resetn(resetn), .r(r), .g(g)",
        """
        clk=0; resetn=0; r=0; @(posedge clk); #1; if(g!==3'b000) mismatch_count=mismatch_count+1;
        resetn=1; r=3'b111; @(posedge clk); #1; if(g!==3'b001) mismatch_count=mismatch_count+1;
        r=0; @(posedge clk); #1; if(g!==0) mismatch_count=mismatch_count+1;
        r=3'b010; @(posedge clk); #1; if(g!==3'b010) mismatch_count=mismatch_count+1;
        r=3'b100; @(posedge clk); #1; if(g!==3'b100) mismatch_count=mismatch_count+1;
        r=3'b000; @(posedge clk); #1; if(g!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob150_review2015_fsmonehot": _tb(
        "reg d, done_counting, ack; reg [9:0] state; wire B3_next,S_next,S1_next,Count_next,Wait_next,done,counting,shift_ena;",
        ".d(d), .done_counting(done_counting), .ack(ack), .state(state), .B3_next(B3_next), .S_next(S_next), .S1_next(S1_next), .Count_next(Count_next), .Wait_next(Wait_next), .done(done), .counting(counting), .shift_ena(shift_ena)",
        """
        d=0; done_counting=0; ack=0; state=10'b0000000001; #1;
        if(!S_next || S1_next || done || counting || shift_ena) mismatch_count=mismatch_count+1;
        d=1; #1; if(!S1_next || S_next) mismatch_count=mismatch_count+1;
        state=10'b0000010000; #1; if(!shift_ena) mismatch_count=mismatch_count+1;
        state=10'b0001000000; #1; if(!B3_next) mismatch_count=mismatch_count+1;
        state=10'b0100000000; done_counting=1; #1; if(!Wait_next || !done) mismatch_count=mismatch_count+1;
        """,
    ),
    "Prob152_lemmings3": _tb(
        "reg clk, areset, bump_left, bump_right, ground, dig; wire walk_left, walk_right, aaah, digging;",
        ".clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .ground(ground), .dig(dig), .walk_left(walk_left), .walk_right(walk_right), .aaah(aaah), .digging(digging)",
        """
        clk=0; areset=1; bump_left=0; bump_right=0; ground=1; dig=0; #1;
        if(walk_left!==1 || digging!==0) mismatch_count=mismatch_count+1;
        areset=0; dig=1; @(posedge clk); #1; if(digging!==1) mismatch_count=mismatch_count+1;
        dig=0; ground=0; @(posedge clk); #1; if(aaah!==1 || digging!==0) mismatch_count=mismatch_count+1;
        ground=1; @(posedge clk); #1; if(walk_left!==1 || aaah!==0) mismatch_count=mismatch_count+1;
        bump_left=1; @(posedge clk); #1; if(walk_right!==1) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob154_fsm_ps2data": _tb(
        "reg clk, reset; reg [7:0] in; wire [23:0] out_bytes; wire done;",
        ".clk(clk), .reset(reset), .in(in), .out_bytes(out_bytes), .done(done)",
        """
        clk=0; reset=1; in=0; @(posedge clk); #1; reset=0;
        in=8'h00; @(posedge clk); #1; if(done) mismatch_count=mismatch_count+1;
        in=8'h81; @(posedge clk); #1; in=8'h12; @(posedge clk); #1; in=8'h34; @(posedge clk); #1;
        if(done!==1 || out_bytes!==24'h811234) mismatch_count=mismatch_count+1;
        in=8'h00; @(posedge clk); #1; if(done) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
    "Prob155_lemmings4": _tb(
        "reg clk, areset, bump_left, bump_right, ground, dig; wire walk_left, walk_right, aaah, digging; integer k;",
        ".clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .ground(ground), .dig(dig), .walk_left(walk_left), .walk_right(walk_right), .aaah(aaah), .digging(digging)",
        """
        clk=0; areset=1; bump_left=0; bump_right=0; ground=1; dig=0; #1;
        if(walk_left!==1 || aaah!==0 || digging!==0) mismatch_count=mismatch_count+1;
        areset=0; ground=0; @(posedge clk); #1;
        for(k=0;k<20;k=k+1) begin @(posedge clk); #1; if(!aaah) mismatch_count=mismatch_count+1; end
        ground=1; @(posedge clk); #1; if(walk_left!==0 || walk_right!==0 || aaah!==0 || digging!==0) mismatch_count=mismatch_count+1;
        """,
        "always #5 clk=~clk;",
    ),
}


PUBLIC_FIXTURES: dict[str, dict[str, str]] = {
    "Prob117_circuit9": {
        "positive": _sv("module TopModule(input clk,input a,output logic [2:0] q); always @(posedge clk) if(a) q<=3'd4; else q<=q+3'd1; endmodule"),
        "constant_zero": _sv("module TopModule(input clk,input a,output logic [2:0] q); always @(posedge clk) q<=3'd0; endmodule"),
        "increments_when_held": _sv("module TopModule(input clk,input a,output logic [2:0] q); always @(posedge clk) q<=q+3'd1; endmodule"),
    },
    "Prob131_mt2015_q4": {
        "positive": _sv("module TopModule(input logic x,input logic y,output logic z); logic a1,b1,a2,b2; assign a1=(x^y)&x; assign b1=~(x^y); assign a2=a1; assign b2=b1; assign z=(a1|b1)^(a2&b2); endmodule"),
        "constant_zero": _sv("module TopModule(input logic x,input logic y,output logic z); assign z=1'b0; endmodule"),
        "wrong_composition": _sv("module TopModule(input logic x,input logic y,output logic z); logic a,b; assign a=(x^y)&x; assign b=~(x^y); assign z=(a|b)|(a&b); endmodule"),
    },
    "Prob144_conwaylife": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic load,input logic [255:0] data,output logic [255:0] q);
          integer r,c,rr,cc,dr,dc,n;
          always @(posedge clk) begin
            if (load) q<=data;
            else begin
              for (r=0;r<16;r=r+1) for (c=0;c<16;c=c+1) begin
                n=0;
                for (dr=-1;dr<=1;dr=dr+1) for (dc=-1;dc<=1;dc=dc+1) if (dr!=0 || dc!=0) begin
                  rr=(r+dr+16)%16; cc=(c+dc+16)%16; n=n+q[rr*16+cc];
                end
                if (q[r*16+c] && (n==2 || n==3)) q[r*16+c]<=1'b1;
                else if (!q[r*16+c] && n==3) q[r*16+c]<=1'b1;
                else q[r*16+c]<=1'b0;
              end
            end
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input logic clk,input logic load,input logic [255:0] data,output logic [255:0] q); always @(posedge clk) q<=256'b0; endmodule"),
        "wrong_neighbor_rule": _sv("module TopModule(input logic clk,input logic load,input logic [255:0] data,output logic [255:0] q); always @(posedge clk) if(load) q<=data; else q<=q<<1; endmodule"),
    },
    "Prob145_circuit8": {
        "positive": _sv("module TopModule(input clock,input a,output logic p,output logic q); always @ (clock or a) if(clock) p<=a; always @ (clock or p) if(!clock) q<=p; endmodule"),
        "constant_zero": _sv("module TopModule(input clock,input a,output logic p,output logic q); always @* begin p=1'b0; q=1'b0; end endmodule"),
        "wrong_edge_register": _sv("module TopModule(input clock,input a,output logic p,output logic q); always @(posedge clock) p<=a; always @(negedge clock) q<=p; endmodule"),
    },
    "Prob149_ece241_2013_q4": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic reset,input logic [2:0] s,output logic fr2,output logic fr1,output logic fr0,output logic dfr);
          logic [2:0] previous_s;
          always @* begin
            case(s)
              3'b111: {fr2,fr1,fr0}=3'b000;
              3'b011: {fr2,fr1,fr0}=3'b001;
              3'b001: {fr2,fr1,fr0}=3'b011;
              default: {fr2,fr1,fr0}=3'b111;
            endcase
          end
          always @(posedge clk) begin
            if(reset) begin previous_s<=3'b000; dfr<=1'b1; end
            else begin dfr <= (s>previous_s); previous_s<=s; end
          end
        endmodule
        """),
        "no_dfr": _sv("module TopModule(input logic clk,input logic reset,input logic [2:0] s,output logic fr2,output logic fr1,output logic fr0,output logic dfr); assign fr2=~s[2]; assign fr1=~s[2]&~s[1]; assign fr0=~s[2]; assign dfr=1'b0; endmodule"),
        "wrong_flow_levels": _sv("module TopModule(input logic clk,input logic reset,input logic [2:0] s,output logic fr2,output logic fr1,output logic fr0,output logic dfr); assign fr2=s[2]; assign fr1=s[1]; assign fr0=s[0]; assign dfr=1'b0; endmodule"),
    },
    "Prob099_m2014_q6c": {
        "positive": _sv("module TopModule(input logic [5:0] y,input logic w,output logic Y1,output logic Y3); assign Y1=y[0]&~w; assign Y3=(y[1]|y[2]|y[4]|y[5])&w; endmodule"),
        "constant_zero": _sv("module TopModule(input logic [5:0] y,input logic w,output logic Y1,output logic Y3); assign Y1=1'b0; assign Y3=1'b0; endmodule"),
        "wrong_y3": _sv("module TopModule(input logic [5:0] y,input logic w,output logic Y1,output logic Y3); assign Y1=y[0]&~w; assign Y3=(y[1]|y[2]|y[4]|y[5])&~w; endmodule"),
    },
    "Prob120_fsm3s": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic reset,input logic in,output logic out);
          logic [1:0] state,next_state;
          always @* case(state) 2'd0:next_state=in?2'd1:2'd0; 2'd1:next_state=in?2'd1:2'd2; 2'd2:next_state=in?2'd3:2'd0; default:next_state=in?2'd1:2'd2; endcase
          always @(posedge clk) if(reset) state<=2'd0; else state<=next_state;
          assign out=(state==2'd3);
        endmodule
        """),
        "wrong_transition": _sv("module TopModule(input logic clk,input logic reset,input logic in,output logic out); logic [1:0] state; always @(posedge clk) if(reset) state<=0; else state<=in?1:0; assign out=(state==3); endmodule"),
        "missing_reset": _sv("module TopModule(input logic clk,input logic reset,input logic in,output logic out); logic [1:0] state; always @(posedge clk) state<=in?1:0; assign out=(state==3); endmodule"),
    },
    "Prob127_lemmings1": {
        "positive": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,output logic walk_left,output logic walk_right); logic state; always @(posedge clk or posedge areset) if(areset) state<=1'b0; else if(bump_left||bump_right) state<=~state; assign walk_left=~state; assign walk_right=state; endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,output logic walk_left,output logic walk_right); assign walk_left=1'b0; assign walk_right=1'b0; endmodule"),
        "ignore_bump": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,output logic walk_left,output logic walk_right); logic state; always @(posedge clk or posedge areset) if(areset) state<=1'b0; assign walk_left=~state; assign walk_right=state; endmodule"),
    },
    "Prob135_m2014_q6b": {
        "positive": _sv("module TopModule(input logic [2:0] y,input logic w,output logic Y1); always @* case(y) 3'd0:Y1=1'b0; 3'd1:Y1=1'b0; 3'd2:Y1=w; 3'd3:Y1=1'b0; 3'd4:Y1=1'b1; 3'd5:Y1=1'b1; default:Y1=1'b0; endcase endmodule"),
        "constant_zero": _sv("module TopModule(input logic [2:0] y,input logic w,output logic Y1); assign Y1=1'b0; endmodule"),
        "wrong_transition": _sv("module TopModule(input logic [2:0] y,input logic w,output logic Y1); assign Y1=(y==3'd4)||(y==3'd5)&&~w; endmodule"),
    },
    "Prob136_m2014_q6": {
        "positive": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); logic [2:0] state,next_state; always @* case(state) 0:next_state=w?0:1; 1:next_state=w?3:2; 2:next_state=w?3:4; 3:next_state=w?0:5; 4:next_state=w?3:4; 5:next_state=w?3:2; default:next_state=0; endcase always @(posedge clk) if(reset) state<=0; else state<=next_state; assign z=(state==4)||(state==5); endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); assign z=1'b0; endmodule"),
        "wrong_transition": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); logic [2:0] state; always @(posedge clk) if(reset) state<=0; else state<=w?0:1; assign z=(state==4)||(state==5); endmodule"),
    },
    "Prob137_fsm_serial": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic reset,input logic in,output logic done);
          logic [1:0] state; logic [3:0] count;
          always @(posedge clk) begin
            done<=1'b0;
            if(reset) begin state<=0; count<=0; end
            else case(state)
              0: if(!in) state<=1;
              1: begin state<=2; count<=0; end
              2: if(count==4'd7) state<=3; else count<=count+1'b1;
              default: if(in) begin done<=1'b1; state<=0; end
            endcase
          end
        endmodule
        """),
        "seven_data_bits": _sv("module TopModule(input logic clk,input logic reset,input logic in,output logic done); logic [1:0] state; logic [2:0] count; always @(posedge clk) begin done<=0; if(reset) begin state<=0; count<=0; end else if(state==0&&!in) state<=1; else if(state==1) begin state<=2; count<=0; end else if(state==2&&count==6) state<=3; else if(state==2) count<=count+1; else if(state==3&&in) begin done<=1; state<=0; end end endmodule"),
        "accept_zero_stop": _sv("module TopModule(input logic clk,input logic reset,input logic in,output logic done); logic [1:0] state; logic [3:0] count; always @(posedge clk) begin done<=0; if(reset) begin state<=0; count<=0; end else case(state) 0:if(!in)state<=1; 1:begin state<=2;count<=0;end 2:if(count==7)state<=3;else count<=count+1; default:begin done<=1;state<=0;end endcase end endmodule"),
    },
    "Prob138_2012_q2fsm": {
        "positive": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); logic [2:0] state,next_state; always @* case(state) 0:next_state=w?1:0; 1:next_state=w?2:3; 2:next_state=w?4:3; 3:next_state=w?5:0; 4:next_state=w?4:3; 5:next_state=w?2:3; default:next_state=0; endcase always @(posedge clk) if(reset) state<=0; else state<=next_state; assign z=(state==4); endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); assign z=1'b0; endmodule"),
        "wrong_transition": _sv("module TopModule(input logic clk,input logic reset,input logic w,output logic z); logic [2:0] state; always @(posedge clk) if(reset) state<=0; else state<=w?1:0; assign z=(state==4); endmodule"),
    },
    "Prob139_2013_q2bfsm": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic resetn,input logic x,input logic y,output logic f,output logic g);
          logic [3:0] state;
          localparam A=0,FP=1,X1=2,X10=3,X101=4,G1=5,G2=6,ON=7,OFF=8;
          always @(posedge clk) begin
            if(!resetn) state<=A;
            else case(state)
              A:state<=FP; FP:state<=X1; X1:state<=x?X10:X1; X10:state<=x?X101:X1; X101:state<=G1;
              G1:state<=y?ON:G2; G2:state<=y?ON:OFF; ON:state<=ON; default:state<=OFF;
            endcase
          end
          assign f=(state==FP); assign g=(state==G1)||(state==G2)||(state==ON);
        endmodule
        """),
        "missing_reset": _sv("module TopModule(input logic clk,input logic resetn,input logic x,input logic y,output logic f,output logic g); logic [3:0] state; always @(posedge clk) state<=state+1'b1; assign f=0; assign g=0; endmodule"),
        "wrong_sequence_timing": _sv("module TopModule(input logic clk,input logic resetn,input logic x,input logic y,output logic f,output logic g); logic [1:0] count; always @(posedge clk) if(!resetn) count<=0; else count<=count+1; assign f=0; assign g=(count==2); endmodule"),
    },
    "Prob142_lemmings2": {
        "positive": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,output logic walk_left,output logic walk_right,output logic aaah); logic [1:0] state; localparam L=0,R=1,FL=2,FR=3; always @(posedge clk or posedge areset) if(areset) state<=L; else case(state) L:if(!ground)state<=FL;else if(bump_left||bump_right)state<=R; R:if(!ground)state<=FR;else if(bump_left||bump_right)state<=L; FL:if(ground)state<=L; FR:if(ground)state<=R; endcase assign walk_left=(state==L); assign walk_right=(state==R); assign aaah=(state==FL)||(state==FR); endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,output logic walk_left,output logic walk_right,output logic aaah); assign walk_left=0; assign walk_right=0; assign aaah=0; endmodule"),
        "bump_while_falling": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,output logic walk_left,output logic walk_right,output logic aaah); logic state; always @(posedge clk or posedge areset) if(areset)state<=0; else if(bump_left||bump_right)state<=~state; assign walk_left=~state&ground; assign walk_right=state&ground; assign aaah=~ground; endmodule"),
    },
    "Prob146_fsm_serialdata": {
        "positive": _sv("""
        module TopModule(input logic clk,input logic in,input logic reset,output logic [7:0] out_byte,output logic done);
          logic [1:0] state; logic [3:0] count; logic [7:0] shift;
          always @(posedge clk) begin
            done<=0;
            if(reset) begin state<=0; count<=0; shift<=0; end
            else case(state)
              0: if(!in) begin state<=1; count<=0; end
              1: begin shift[count]<=in; if(count==7) state<=2; else count<=count+1; end
              default: if(in) begin out_byte<=shift; done<=1; state<=0; end
            endcase
          end
        endmodule
        """),
        "constant_zero": _sv("module TopModule(input logic clk,input logic in,input logic reset,output logic [7:0] out_byte,output logic done); assign out_byte=0; assign done=0; endmodule"),
        "wrong_data_bit_order": _sv("module TopModule(input logic clk,input logic in,input logic reset,output logic [7:0] out_byte,output logic done); logic [3:0] count; logic [7:0] shift; always @(posedge clk) if(reset) begin count<=0;done<=0; end else begin done<=0; shift[7-count]<=in; if(count==7) begin out_byte<=shift; done<=1; count<=0; end else count<=count+1; end endmodule"),
    },
    "Prob148_2013_q2afsm": {
        "positive": _sv("module TopModule(input logic clk,input logic resetn,input logic [2:0] r,output logic [2:0] g); logic [1:0] state; localparam A=0,B=1,C=2,D=3; always @(posedge clk) if(!resetn)state<=A; else case(state) A:if(r[0])state<=B;else if(r[1])state<=C;else if(r[2])state<=D;else state<=A; B:if(!r[0])state<=A; C:if(!r[1])state<=A; D:if(!r[2])state<=A; endcase assign g=(state==B)?3'b001:(state==C)?3'b010:(state==D)?3'b100:3'b000; endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic resetn,input logic [2:0] r,output logic [2:0] g); assign g=3'b000; endmodule"),
        "wrong_priority": _sv("module TopModule(input logic clk,input logic resetn,input logic [2:0] r,output logic [2:0] g); logic [1:0] state; always @(posedge clk) if(!resetn)state<=0; else if(r[2])state<=3; else if(r[1])state<=2; else if(r[0])state<=1; else state<=0; assign g=(state==1)?1:(state==2)?2:(state==3)?4:0; endmodule"),
    },
    "Prob150_review2015_fsmonehot": {
        "positive": _sv("module TopModule(input logic d,input logic done_counting,input logic ack,input logic [9:0] state,output logic B3_next,output logic S_next,output logic S1_next,output logic Count_next,output logic Wait_next,output logic done,output logic counting,output logic shift_ena); assign B3_next=state[6]; assign S_next=(state[0]&~d)|(state[3]&~d)|(state[9]&ack); assign S1_next=state[0]&d; assign Count_next=state[7]; assign Wait_next=(state[8]&done_counting)|(state[9]&~ack); assign done=state[9]; assign counting=state[8]; assign shift_ena=|state[7:4]; endmodule"),
        "wrong_next_state": _sv("module TopModule(input logic d,input logic done_counting,input logic ack,input logic [9:0] state,output logic B3_next,output logic S_next,output logic S1_next,output logic Count_next,output logic Wait_next,output logic done,output logic counting,output logic shift_ena); assign B3_next=state[5]; assign S_next=state[0]&~d; assign S1_next=state[0]&d; assign Count_next=state[7]; assign Wait_next=state[8]&done_counting; assign done=state[9]; assign counting=state[8]; assign shift_ena=|state[7:4]; endmodule"),
        "outputs_zero": _sv("module TopModule(input logic d,input logic done_counting,input logic ack,input logic [9:0] state,output logic B3_next,output logic S_next,output logic S1_next,output logic Count_next,output logic Wait_next,output logic done,output logic counting,output logic shift_ena); assign B3_next=0; assign S_next=0; assign S1_next=0; assign Count_next=0; assign Wait_next=0; assign done=0; assign counting=0; assign shift_ena=0; endmodule"),
    },
    "Prob152_lemmings3": {
        "positive": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); logic [2:0] state; localparam L=0,R=1,DL=2,DR=3,FL=4,FR=5; always @(posedge clk or posedge areset) if(areset)state<=L; else case(state) L:if(!ground)state<=FL;else if(dig)state<=DL;else if(bump_left||bump_right)state<=R; R:if(!ground)state<=FR;else if(dig)state<=DR;else if(bump_left||bump_right)state<=L; DL:if(!ground)state<=FL; DR:if(!ground)state<=FR; FL:if(ground)state<=L; FR:if(ground)state<=R; endcase assign walk_left=(state==L); assign walk_right=(state==R); assign aaah=(state==FL)||(state==FR); assign digging=(state==DL)||(state==DR); endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); assign walk_left=0; assign walk_right=0; assign aaah=0; assign digging=0; endmodule"),
        "ignore_dig": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); logic state; always @(posedge clk or posedge areset) if(areset)state<=0; else if(!ground)state<=1; else if(bump_left||bump_right)state<=~state; assign walk_left=~state; assign walk_right=state; assign aaah=0; assign digging=0; endmodule"),
    },
    "Prob154_fsm_ps2data": {
        "positive": _sv("module TopModule(input logic clk,input logic reset,input logic [7:0] in,output logic [23:0] out_bytes,output logic done); logic [1:0] count; logic [23:0] buffer; always @(posedge clk) begin done<=0; if(reset) begin count<=0; buffer<=0; end else if(count==0) begin if(in[3]) begin buffer[23:16]<=in; count<=1; end end else if(count==1) begin buffer[15:8]<=in; count<=2; end else begin buffer[7:0]<=in; out_bytes<={buffer[23:8],in}; done<=1; count<=0; end end endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic reset,input logic [7:0] in,output logic [23:0] out_bytes,output logic done); assign out_bytes=0; assign done=0; endmodule"),
        "done_too_early": _sv("module TopModule(input logic clk,input logic reset,input logic [7:0] in,output logic [23:0] out_bytes,output logic done); logic [1:0] count; always @(posedge clk) begin if(reset)count<=0; else begin done<=in[3]; count<=count+1; end end endmodule"),
    },
    "Prob155_lemmings4": {
        "positive": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); logic [2:0] state; logic [4:0] fall_count; localparam L=0,R=1,DL=2,DR=3,FL=4,FR=5,S=6; always @(posedge clk or posedge areset) if(areset) begin state<=L;fall_count<=0;end else case(state) L:if(!ground)begin state<=FL;fall_count<=0;end else if(dig)state<=DL;else if(bump_left||bump_right)state<=R; R:if(!ground)begin state<=FR;fall_count<=0;end else if(dig)state<=DR;else if(bump_left||bump_right)state<=L; DL:if(!ground)begin state<=FL;fall_count<=0;end DR:if(!ground)begin state<=FR;fall_count<=0;end FL:if(ground)state<=(fall_count>=20)?S:L;else fall_count<=fall_count+1; FR:if(ground)state<=(fall_count>=20)?S:R;else fall_count<=fall_count+1; default:state<=S; endcase assign walk_left=(state==L); assign walk_right=(state==R); assign aaah=(state==FL)||(state==FR); assign digging=(state==DL)||(state==DR); endmodule"),
        "constant_zero": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); assign walk_left=0; assign walk_right=0; assign aaah=0; assign digging=0; endmodule"),
        "splatter_threshold": _sv("module TopModule(input logic clk,input logic areset,input logic bump_left,input logic bump_right,input logic ground,input logic dig,output logic walk_left,output logic walk_right,output logic aaah,output logic digging); logic state; always @(posedge clk or posedge areset) if(areset)state<=0; else if(!ground)state<=1; else state<=0; assign walk_left=~state; assign walk_right=0; assign aaah=state; assign digging=0; endmodule"),
    },
}


def _fixture_errors(text: str) -> list[str]:
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
    if len(re.findall(r"\bmodule\s+", text, flags=re.IGNORECASE)) != 1:
        errors.append("fixture must declare exactly one module")
    if len(re.findall(r"\bendmodule\b", text, flags=re.IGNORECASE)) != 1:
        errors.append("fixture must contain exactly one endmodule")
    if not re.search(r"\bmodule\s+TopModule\b", text, flags=re.IGNORECASE):
        errors.append("fixture does not declare TopModule")
    return errors


def _validate_inputs(
    *,
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    inventory = {
        row["source_id"]: row
        for row in _load_jsonl(inventory_path)
        if isinstance(row.get("source_id"), str)
    }
    selection = _load_json(selection_path)
    selected = [
        line.strip()
        for line in ids_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if tuple(selected) != SOURCE_IDS:
        raise ValueError("source IDs do not match the pinned v008 order")
    if selection.get("ok") is not True or selection.get("selected_count") != 20:
        raise ValueError("selection report is not the successful 20-task selection")
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
    if set(PUBLIC_TESTBENCHES) != set(SOURCE_IDS) or set(PUBLIC_FIXTURES) != set(SOURCE_IDS):
        raise ValueError("v008 catalog is incomplete")
    for source_id in selected:
        source = inventory.get(source_id)
        if source is None:
            raise ValueError(f"source ID missing from inventory: {source_id}")
        if source.get("verification_readiness") != "needs_testbench" or source.get("top_module") != "TopModule":
            raise ValueError(f"source readiness or identity mismatch: {source_id}")
        expected_names = ("positive", *MUTATION_NAMES[source_id])
        if tuple(PUBLIC_FIXTURES[source_id]) != expected_names:
            raise ValueError(f"fixture catalog order mismatch: {source_id}")
        audit, audit_errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        if audit_errors or audit.get("support_file_count") != 0:
            raise ValueError(f"testbench static audit failed: {source_id}: {audit_errors}")
        for name, content in PUBLIC_FIXTURES[source_id].items():
            errors = _fixture_errors(content)
            if errors:
                raise ValueError(f"fixture privacy/shape audit failed: {source_id}:{name}: {errors}")
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
    selection_rows = {row["source_id"]: row for row in selection["rows"]}
    for source_id in selected:
        testbench_path = tasks_root / source_id / "testbench.sv"
        _write_exclusive(testbench_path, PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        testbench_hashes[source_id] = _sha256_file(testbench_path)
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            fixture_path = qualification_root / source_id / f"{name}.sv"
            _write_exclusive(fixture_path, content.encode("utf-8"))
            fixture_hashes[source_id][name] = _sha256_file(fixture_path)
        retry = source_id in RETRY_SOURCE_IDS
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
            "lineage": "qualification_retry_overlay" if retry else "new_uncovered_train",
            "prior_assetfix_versions": ["assetfix_v005", "assetfix_v006", "assetfix_v007"] if retry else [],
            "selection_role": selection_rows[source_id].get("selection_role"),
        })
    authoring_manifest = qualification_root / "authoring_manifest.jsonl"
    _write_exclusive(
        authoring_manifest,
        b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for row in authoring_rows
        ),
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
        "retry_source_ids": [source_id for source_id in selected if source_id in RETRY_SOURCE_IDS],
        "new_uncovered_source_ids": [source_id for source_id in selected if source_id not in RETRY_SOURCE_IDS],
        "public_specification_hashes": {
            source_id: inventory[source_id]["source_prompt_sha256"]
            for source_id in selected
        },
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": len(selected) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "correction_reason": "bounded final train-remainder overlay with explicit retry lineage",
        "dependency_closure": "pending_static_validation",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    _write_exclusive(
        attestation_path,
        (json.dumps(attestation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "authoring_manifest": authoring_manifest.as_posix(),
        "authoring_attestation": attestation_path.as_posix(),
        "selected_source_ids": selected,
        "selected_count": len(selected),
        "retry_source_ids": sorted(RETRY_SOURCE_IDS, key=selected.index),
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": len(selected) * 2,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "selection_ids_sha256": _sha256_file(ids_path),
        "selection_report_sha256": _sha256_file(selection_path),
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
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
