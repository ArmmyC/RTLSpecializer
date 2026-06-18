#!/usr/bin/env python3
"""Build the deterministic, concrete, synthetic v0.1 golden dataset."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import write_jsonl

SYSTEM = "You are an RTL review assistant. Be conservative, preserve behavior, distinguish suggestions from tool evidence, and never invent measurements."
REQUIRED_OUTPUT = ["issue_summary", "time_reasoning", "space_reasoning", "safe_optimization", "functional_risk", "verification_plan", "claim_levels"]


@dataclass(frozen=True)
class Example:
    family: str
    short: str
    task_type: str
    goal: str
    module: str | None
    signals: tuple[str, ...]
    block: str
    issue: str
    reason: str
    artifact_field: str
    artifact: str
    second_artifact: str | None = None
    timing: str = "Preserve the visible cycle and reset behavior."
    resources: str = "The affected logic and registers require tool comparison for quantitative conclusions."
    recommendation: str = "Make only a minimal source-level change after confirming the intended behavior."
    risk: str = "The change could alter cycle, reset, state, or interface behavior."


EXAMPLES = [
    Example("counter", "bug", "rtl_bug_review", "find_correctness_bug", "terminal_counter", ("count", "done"), "always_ff",
        "The counter asserts done and wraps at count 8, one cycle before the documented terminal count 9.",
        "The always_ff comparison is count == 4'd8, so the 0-through-9 sequence never presents count 9.", "rtl_code",
        """module terminal_counter(input logic clk, rst_n, en, output logic [3:0] count, output logic done);\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin count <= 4'd0; done <= 1'b0; end\n    else if (en) begin\n      done <= (count == 4'd8);\n      if (count == 4'd8) count <= 4'd0; else count <= count + 4'd1;\n    end\n  end\nendmodule""",
        timing="Changing the terminal compare moves done and wrap by one enabled clock.", resources="The 4-bit incrementer, equality compare, count, and done registers are involved.", recommendation="Confirm the 0-through-9 requirement, then change both terminal comparisons to 4'd9."),
    Example("fsm", "bug", "rtl_bug_review", "find_correctness_bug", "request_fsm", ("state", "next_state", "req"), "always_comb",
        "next_state is not assigned when state is an unlisted value, so combinational state memory can be inferred.",
        "The always_comb case has only IDLE and BUSY branches and no default assignment or default case.", "rtl_code",
        """module request_fsm(input logic clk, rst_n, req, output logic busy);\n  typedef enum logic {IDLE, BUSY} state_t; state_t state, next_state;\n  always_comb begin\n    case (state)\n      IDLE: next_state = req ? BUSY : IDLE;\n      BUSY: next_state = req ? BUSY : IDLE;\n    endcase\n  end\n  always_ff @(posedge clk or negedge rst_n) if (!rst_n) state <= IDLE; else state <= next_state;\n  assign busy = (state == BUSY);\nendmodule""",
        timing="A default assignment affects recovery from illegal state encodings but not normal IDLE/BUSY transitions.", resources="The next-state mux and state register are involved.", recommendation="Assign next_state = IDLE before the case, subject to the required illegal-state policy."),
    Example("shift_register", "bug", "rtl_bug_review", "find_correctness_bug", "lsb_serializer", ("shift_q", "serial_out"), "always_ff",
        "serial_out selects bit 0 while shift_q shifts toward higher indices, so the next transmitted bit is not moved into bit 0.",
        "The assignment {shift_q[6:0], 1'b0} is a left shift, while serial_out reads the LSB.", "rtl_code",
        """module lsb_serializer(input logic clk, rst_n, load, shift_en, input logic [7:0] data, output logic serial_out);\n  logic [7:0] shift_q;\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) shift_q <= 8'd0;\n    else if (load) shift_q <= data;\n    else if (shift_en) shift_q <= {shift_q[6:0], 1'b0};\n  end\n  assign serial_out = shift_q[0];\nendmodule""",
        timing="Correcting the direction changes which bit appears after each shift_en edge without adding latency.", resources="The 8-bit shift register and output tap are involved.", recommendation="For LSB-first operation, shift right with {1'b0, shift_q[7:1]} after confirming bit order."),
    Example("handshake", "bug", "rtl_bug_review", "find_correctness_bug", "stream_source", ("out_valid", "out_ready", "out_data", "next_data"), "always_ff",
        "out_data changes every valid cycle even when out_ready is low, violating payload stability during backpressure.",
        "The out_valid branch assigns out_data <= next_data without checking out_ready.", "rtl_code",
        """module stream_source(input logic clk, rst_n, start, out_ready, input logic [7:0] next_data, output logic out_valid, output logic [7:0] out_data);\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin out_valid <= 1'b0; out_data <= 8'd0; end\n    else if (start) begin out_valid <= 1'b1; out_data <= next_data; end\n    else if (out_valid) begin\n      out_data <= next_data;\n      if (out_ready) out_valid <= 1'b0;\n    end\n  end\nendmodule""",
        timing="The payload must remain stable across every stalled cycle until the ready/valid transfer edge.", resources="The valid and 8-bit payload registers are involved.", recommendation="Update out_data only when accepting a new item or completing the current transfer."),
    Example("timer", "bug", "rtl_bug_review", "find_correctness_bug", "down_timer", ("remaining", "start", "done"), "always_ff",
        "Starting with cycles equal to zero loads zero, then decrements it to 8'hff before done can assert.",
        "done is tested only in the running branch and remaining <= remaining - 1 executes for remaining == 0.", "rtl_code",
        """module down_timer(input logic clk, rst_n, start, input logic [7:0] cycles, output logic done);\n  logic [7:0] remaining; logic running;\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin remaining <= 8'd0; running <= 1'b0; done <= 1'b0; end\n    else if (start) begin remaining <= cycles; running <= 1'b1; done <= 1'b0; end\n    else if (running) begin\n      remaining <= remaining - 8'd1;\n      if (remaining == 8'd1) begin done <= 1'b1; running <= 1'b0; end\n    end\n  end\nendmodule""",
        timing="The zero-duration policy determines whether done asserts on start or the following clock.", resources="The 8-bit down-counter, zero compare, running, and done registers are involved.", recommendation="Handle cycles == 0 explicitly in the start branch after confirming the required done timing."),

    Example("counter", "activity", "rtl_area_activity_review", "reduce_switching_activity", "event_counter", ("count", "active"), "always_ff",
        "count increments on every clock even when active is low and the count value is not used.",
        "The always_ff increment has no active qualification, while only sampled_count capture is gated by active.", "rtl_code",
        """module event_counter(input logic clk, rst_n, active, output logic [15:0] sampled_count);\n  logic [15:0] count;\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin count <= 16'd0; sampled_count <= 16'd0; end\n    else begin\n      count <= count + 16'd1;\n      if (active) sampled_count <= count;\n    end\n  end\nendmodule""",
        timing="Adding an enable changes the count sequence unless inactive cycles are specified as irrelevant.", resources="The 16-bit incrementer and count register toggle each clock.", recommendation="If the specification permits pausing time while inactive, qualify the count update with active."),
    Example("mux", "activity", "rtl_area_activity_review", "reduce_area", "duplicated_mux", ("sel", "a", "b", "y0", "y1"), "assign",
        "Two identical select expressions independently drive y0 and y1.",
        "Both continuous assignments implement sel ? a : b with the same inputs.", "rtl_code",
        """module duplicated_mux(input logic sel, input logic [7:0] a, b, output logic [7:0] y0, y1);\n  assign y0 = sel ? a : b;\n  assign y1 = sel ? a : b;\nendmodule""",
        timing="Sharing a combinational result must not add registers or change output timing.", resources="Two source-level 8-bit mux expressions are present; synthesis may already merge them.", recommendation="Use one intermediate mux result for both outputs, then compare synthesis area."),
    Example("decoder", "activity", "rtl_area_activity_review", "reduce_switching_activity", "qualified_decoder", ("addr", "enable", "decoded"), "always_comb",
        "The full one-hot shift is evaluated before enable selects whether decoded is observable.",
        "always_comb computes raw_decode = 16'b1 << addr unconditionally, then gates only decoded.", "rtl_code",
        """module qualified_decoder(input logic enable, input logic [3:0] addr, output logic [15:0] decoded);\n  logic [15:0] raw_decode;\n  always_comb begin\n    raw_decode = 16'b1 << addr;\n    if (enable) decoded = raw_decode; else decoded = 16'd0;\n  end\nendmodule""",
        timing="Any rewrite must keep decoded combinational and zero whenever enable is low.", resources="The 4-to-16 decode cone and decoded output are involved.", recommendation="Express the decode only in the enabled branch and use synthesis/toggle comparison to assess impact."),
    Example("register_bank", "activity", "rtl_area_activity_review", "reduce_area", "write_bank", ("wr_addr", "wr_en", "regs"), "always_ff",
        "Four repeated address comparisons form separate write-enable conditions for the register bank.",
        "Each if statement repeats wr_en && wr_addr == constant for one regs element.", "rtl_code",
        """module write_bank(input logic clk, wr_en, input logic [1:0] wr_addr, input logic [7:0] wr_data, output logic [7:0] regs [0:3]);\n  always_ff @(posedge clk) begin\n    if (wr_en && wr_addr == 2'd0) regs[0] <= wr_data;\n    if (wr_en && wr_addr == 2'd1) regs[1] <= wr_data;\n    if (wr_en && wr_addr == 2'd2) regs[2] <= wr_data;\n    if (wr_en && wr_addr == 2'd3) regs[3] <= wr_data;\n  end\nendmodule""",
        timing="A rewrite must preserve same-edge writes and the one-selected-register behavior.", resources="Four 2-bit equality compares and four 8-bit register write enables are described.", recommendation="Consider indexed writing regs[wr_addr] <= wr_data under wr_en, then compare synthesis results."),
    Example("comparator", "activity", "rtl_area_activity_review", "reduce_switching_activity", "qualified_compare", ("a", "b", "valid", "equal_q"), "always_ff",
        "The 32-bit equality result is registered every clock, including cycles where valid is low.",
        "equal_q receives a == b unconditionally; valid only controls the separate result_valid register.", "rtl_code",
        """module qualified_compare(input logic clk, rst_n, valid, input logic [31:0] a, b, output logic result_valid, equal_q);\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin equal_q <= 1'b0; result_valid <= 1'b0; end\n    else begin equal_q <= (a == b); result_valid <= valid; end\n  end\nendmodule""",
        timing="Gating equal_q changes its value on invalid cycles; consumers must use result_valid as specified.", resources="A 32-bit equality comparator and one result register are involved.", recommendation="If equal_q is don't-care when result_valid is low, qualify its update with valid and compare activity."),

    Example("serializer", "report", "rtl_tool_report_explanation", "explain_lint_log", None, ("load_data", "shift_q"), "lint_log",
        "The lint report identifies truncation from 9-bit load_data into the 8-bit shift_q register.",
        "The supplied WIDTH warning names shift_q[7:0] as the destination and load_data[8:0] as the source.", "lint_log",
        "Synthetic lint excerpt\nserializer.sv:12 WIDTH: assignment to shift_q[7:0] truncates load_data[8:0]\nRule: WIDTH_TRUNCATION; severity: warning",
        timing="If bit 8 carries payload or framing information, truncation changes the loaded serial sequence.", resources="The report concerns the 8-bit shift_q register and 9-bit load_data bus.", recommendation="Confirm whether bit 8 is intentional, then align widths or select the intended eight bits explicitly."),
    Example("fsm", "report", "rtl_tool_report_explanation", "explain_synthesis_report", None, ("state_reg", "grant_reg"), "synthesis_report",
        "The synthesis excerpt reports three state bits for a three-state FSM rather than a compact two-bit encoding.",
        "The FSM section lists encoding=onehot and state_bits=3 for states IDLE, WAIT, and GRANT.", "synthesis_report",
        "Synthetic synthesis excerpt\nFSM request_ctrl: states=3 encoding=onehot state_bits=3\nRegisters: state_reg[2:0]=3, grant_reg=1\nArea units are unavailable.",
        timing="Changing encoding can affect decode timing and illegal-state recovery, so behavior and timing remain constraints.", resources="The excerpt identifies three state flip-flops and one grant register; it provides no area measurement.", recommendation="Treat compact encoding as a candidate only; confirm policy and compare synthesis and equivalence."),
    Example("timer", "report", "rtl_tool_report_explanation", "explain_toggle_report", None, ("prescale_count", "tick"), "toggle_report",
        "The toggle excerpt marks prescale_count[0] as the highest-activity timer signal.",
        "prescale_count[0] has toggle_rate 0.50, higher than bits 1 through 3 and tick.", "toggle_report",
        "Synthetic toggle excerpt\nsignal,prescale_count[0],toggle_rate,0.50\nsignal,prescale_count[1],toggle_rate,0.25\nsignal,prescale_count[2],toggle_rate,0.125\nsignal,tick,toggle_rate,0.01",
        timing="Any prescaler change must retain the tick period and phase expected by downstream logic.", resources="The report covers prescale_count bits and tick; it is activity evidence, not power evidence.", recommendation="Use the excerpt to focus review on the prescaler, then obtain matched VCD comparisons for any change."),

    Example("handshake", "reject", "unsafe_optimization_rejection", "reject_unsafe_optimization", "skid_buffer", ("hold_valid", "hold_data", "in_ready", "out_ready"), "always_ff",
        "Removing hold_valid and hold_data would eliminate storage used when downstream out_ready is low.",
        "The always_ff block captures in_data exactly when in_valid is accepted but cannot pass downstream.", "rtl_code",
        """module skid_buffer(input logic clk, rst_n, in_valid, out_ready, input logic [7:0] in_data, output logic in_ready, out_valid, output logic [7:0] out_data);\n  logic hold_valid; logic [7:0] hold_data;\n  assign in_ready = !hold_valid; assign out_valid = hold_valid | in_valid; assign out_data = hold_valid ? hold_data : in_data;\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) hold_valid <= 1'b0;\n    else if (in_valid && in_ready && !out_ready) begin hold_valid <= 1'b1; hold_data <= in_data; end\n    else if (out_ready) hold_valid <= 1'b0;\n  end\nendmodule""",
        timing="Removing the register changes backpressure tolerance and ready/valid behavior on stalled cycles.", resources="One valid bit and one 8-bit payload register provide the skid capacity.", recommendation="Reject register removal unless the interface contract forbids downstream stalls."),
    Example("fsm", "reject", "unsafe_optimization_rejection", "reject_unsafe_optimization", "safe_fsm", ("state", "next_state", "fault"), "case",
        "Dropping the explicit ERROR state would remove the fault-hold behavior visible in the state transition case.",
        "When fault is asserted in RUN, next_state becomes ERROR and stays ERROR until reset.", "rtl_code",
        """module safe_fsm(input logic clk, rst_n, start, fault, output logic running);\n  typedef enum logic [1:0] {IDLE, RUN, ERROR} state_t; state_t state, next_state;\n  always_comb begin next_state = state; case (state) IDLE: if (start) next_state = RUN; RUN: if (fault) next_state = ERROR; ERROR: next_state = ERROR; default: next_state = ERROR; endcase end\n  always_ff @(posedge clk or negedge rst_n) if (!rst_n) state <= IDLE; else state <= next_state;\n  assign running = (state == RUN);\nendmodule""",
        timing="Removing ERROR changes next-state behavior after fault and recovery only occurs through reset.", resources="The two-bit state register and ERROR decode implement fault containment.", recommendation="Reject state removal without a changed safety specification and equivalence against that specification."),
    Example("register_bank", "reject", "unsafe_optimization_rejection", "reject_unsafe_optimization", "dual_read_bank", ("mem", "rd_addr_a", "rd_addr_b", "rd_data_a", "rd_data_b"), "assign",
        "Sharing the two read paths through one time-multiplexed port would no longer provide both outputs combinationally in the same cycle.",
        "Two continuous assignments independently index mem with rd_addr_a and rd_addr_b.", "rtl_code",
        """module dual_read_bank(input logic clk, wr_en, input logic [1:0] wr_addr, rd_addr_a, rd_addr_b, input logic [7:0] wr_data, output logic [7:0] rd_data_a, rd_data_b);\n  logic [7:0] mem [0:3];\n  always_ff @(posedge clk) if (wr_en) mem[wr_addr] <= wr_data;\n  assign rd_data_a = mem[rd_addr_a]; assign rd_data_b = mem[rd_addr_b];\nendmodule""",
        timing="Time multiplexing would add arbitration or latency and change the dual-read interface contract.", resources="The source describes a four-entry memory with two combinational read paths.", recommendation="Reject port sharing unless the specification permits serialized reads and an interface change."),
    Example("timer", "reject", "unsafe_optimization_rejection", "reject_unsafe_optimization", "sticky_timer", ("done", "count", "rst_n"), "always_ff",
        "Removing reset from done would make its startup and reset value unspecified instead of zero.",
        "The asynchronous reset branch explicitly assigns done <= 1'b0 and count <= 8'd0.", "rtl_code",
        """module sticky_timer(input logic clk, rst_n, start, input logic [7:0] limit, output logic done);\n  logic [7:0] count;\n  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) begin count <= 8'd0; done <= 1'b0; end\n    else if (start) begin count <= 8'd0; done <= 1'b0; end\n    else if (!done) begin count <= count + 8'd1; if (count == limit) done <= 1'b1; end\n  end\nendmodule""",
        timing="Reset removal changes observable done behavior during and immediately after reset.", resources="The done flag and 8-bit count register have explicit reset behavior.", recommendation="Reject reset removal unless startup state is proven unobservable and the reset contract changes."),

    Example("serializer", "compare", "rtl_before_after_judgment", "compare_before_after", "gated_serializer", ("shift_q", "shift_en", "serial_out"), "always_ff",
        "The after version holds shift_q when shift_en is low, whereas the before version shifts every cycle.",
        "Both versions use the same output tap, but only the after always_ff block conditions the shift assignment on shift_en.", "before_rtl_code",
        """module gated_serializer(input logic clk, rst_n, shift_en, input logic serial_in, output logic serial_out);\n  logic [7:0] shift_q; always_ff @(posedge clk or negedge rst_n) if (!rst_n) shift_q <= 8'd0; else shift_q <= {shift_q[6:0], serial_in}; assign serial_out = shift_q[7];\nendmodule""",
        """module gated_serializer(input logic clk, rst_n, shift_en, input logic serial_in, output logic serial_out);\n  logic [7:0] shift_q; always_ff @(posedge clk or negedge rst_n) if (!rst_n) shift_q <= 8'd0; else if (shift_en) shift_q <= {shift_q[6:0], serial_in}; assign serial_out = shift_q[7];\nendmodule""",
        timing="The after version changes cycle-level behavior whenever shift_en is low; that is correct only if shift_en is part of the intended protocol.", resources="Both versions use one 8-bit shift register; quantitative activity remains unmeasured.", recommendation="Accept only after confirming disabled cycles must hold state and comparing traces around shift_en transitions."),
    Example("decoder", "compare", "rtl_before_after_judgment", "compare_before_after", "command_decoder", ("opcode", "hit"), "always_comb",
        "The after version adds a default assignment, removing the retained hit value for unlisted opcodes.",
        "The before case omits default behavior; the after always_comb initializes hit to zero before the same cases.", "before_rtl_code",
        """module command_decoder(input logic [1:0] opcode, output logic hit);\n  always_comb begin case (opcode) 2'd0: hit = 1'b0; 2'd1: hit = 1'b1; endcase end\nendmodule""",
        """module command_decoder(input logic [1:0] opcode, output logic hit);\n  always_comb begin hit = 1'b0; case (opcode) 2'd1: hit = 1'b1; default: hit = 1'b0; endcase end\nendmodule""",
        timing="The after version is purely combinational for every opcode; it intentionally removes latch-like retention.", resources="The change affects the two-bit decode and one output assignment.", recommendation="Prefer the after form if unlisted opcodes must produce zero, then lint and simulate all opcode values."),
    Example("shift_register", "compare", "rtl_before_after_judgment", "compare_before_after", "tap_delay", ("delay_q", "din", "dout"), "always_ff",
        "The after concatenation is behaviorally aligned with the two explicit assignments for a two-stage delay.",
        "Before assigns delay_q[0] from din and delay_q[1] from the previous delay_q[0]; after assigns {delay_q[0], din} to {delay_q[1], delay_q[0]}.", "before_rtl_code",
        """module tap_delay(input logic clk, rst_n, din, output logic dout);\n  logic [1:0] delay_q; always_ff @(posedge clk or negedge rst_n) begin if (!rst_n) delay_q <= 2'b00; else begin delay_q[0] <= din; delay_q[1] <= delay_q[0]; end end assign dout = delay_q[1];\nendmodule""",
        """module tap_delay(input logic clk, rst_n, din, output logic dout);\n  logic [1:0] delay_q; always_ff @(posedge clk or negedge rst_n) if (!rst_n) delay_q <= 2'b00; else delay_q <= {delay_q[0], din}; assign dout = delay_q[1];\nendmodule""",
        timing="Both forms should retain the two-edge delay from din to dout and identical reset state.", resources="Both forms describe the same two flip-flops and output tap, pending synthesis confirmation.", recommendation="Use simulation or equivalence across reset and data transitions before accepting the compact form."),
]


def _artifacts(example: Example) -> dict[str, str | None]:
    fields = {"rtl_code": None, "before_rtl_code": None, "after_rtl_code": None, "testbench": None, "synthesis_report": None, "toggle_report": None, "lint_log": None}
    fields[example.artifact_field] = example.artifact
    if example.second_artifact is not None:
        fields["after_rtl_code"] = example.second_artifact
    return fields


def _tool_checks(example: Example) -> dict[str, dict | None]:
    checks: dict[str, dict | None] = {"parse": None, "lint": None, "simulation": None, "equivalence": None, "synthesis": None, "toggle": None, "power": None}
    tool = {"lint_log": "lint", "synthesis_report": "synthesis", "toggle_report": "toggle"}.get(example.artifact_field)
    if tool:
        checks[tool] = {"status": "unknown", "tool": "synthetic_fixture", "version": None, "summary": "Synthetic report excerpt supplied for explanation only.", "artifact_ref": example.artifact_field}
    return checks


def make_row(index: int, example: Example) -> dict:
    activity_task = example.task_type == "rtl_area_activity_review"
    task = {
        "schema_version": "rtl_task_v0.1", "domain": "digital_rtl", "task_type": example.task_type, "user_goal": example.goal,
        "design_context": {"target_domain": "rfid_nfc_digital_ic", "priority": ["correctness", "low_switching_activity", "low_area"], "timing_policy": "timing_is_constraint_not_reward"},
        "artifacts": _artifacts(example),
        "extracted_rtl_summary": {"top_module": example.module, "clock_signals": ["clk"] if "clk" in example.artifact else [], "reset_signals": ["rst_n"] if "rst_n" in example.artifact else [], "registered_signals": list(example.signals) if "always_ff" in example.artifact else [], "combinational_blocks": [example.block] if example.block in {"always_comb", "assign", "case"} else [], "suspected_fsm_signals": [signal for signal in example.signals if "state" in signal], "suspected_counters": [signal for signal in example.signals if signal in {"count", "remaining", "prescale_count"}], "unused_enable_signals": [], "activity_hotspots": list(example.signals) if activity_task else []},
        "constraints": {"preserve_top_level_interface": True, "preserve_cycle_level_behavior": True, "preserve_reset_behavior": True, "do_not_claim_power_without_power_report": True, "prefer_minimal_patch": True},
        "assumptions": ["The artifact is synthetic and limited to the behavior shown; no unstated tool result is assumed."], "required_output": REQUIRED_OUTPUT,
    }
    levels = {"correctness": "suggestion_only", "area": "insufficient_evidence", "activity": "suggestion_only" if activity_task else "not_applicable", "power": "insufficient_evidence"}
    if example.artifact_field == "synthesis_report": levels["area"] = "tool_supported"
    if example.artifact_field == "toggle_report": levels["activity"] = "tool_supported"
    answer = {
        "schema_version": "rtl_answer_v0.1", "task_type": example.task_type,
        "issue_summary": [{"issue": example.issue, "severity": "medium", "evidence": {"signal_names": list(example.signals), "code_location": {"module": example.module, "block": example.block, "line_range": None}, "reason": example.reason}}],
        "time_reasoning": {"clock_cycle_behavior": example.timing, "latency_or_state_risk": "Do not add latency, state, or interface changes without specification confirmation.", "reset_behavior_risk": "Preserve every explicit reset value and post-reset transition."},
        "space_reasoning": {"hardware_resources_involved": list(example.signals), "area_risk": example.resources + " Area impact requires synthesis comparison.", "activity_risk": "Switching impact requires matched VCD toggle comparison."},
        "safe_optimization": {"recommendation": example.recommendation, "patch_style": "explanation_only" if example.task_type == "rtl_tool_report_explanation" else "minimal", "expected_effect": "The recommendation addresses the concrete artifact behavior; quantitative effects remain unmeasured.", "requires_spec_confirmation": example.task_type in {"unsafe_optimization_rejection", "rtl_before_after_judgment"}},
        "functional_risk": [example.risk],
        "verification_plan": ["Run lint/compile on the original and any proposed RTL", "Run focused simulation and compare cycle-level outputs"],
        "claim_levels": levels, "patch": {"provided": False, "patch_type": "none", "diff": None, "notes": "No patch is provided without specification and verification context."},
    }
    if activity_task:
        answer["verification_plan"] += ["Synthesis area comparison is required; synthesis evidence is unavailable", "VCD toggle/activity comparison is required; toggle evidence is unavailable"]
    return {
        "id": f"golden_{example.family}_{example.short}_{index + 1:03d}", "dataset_version": "dataset_v0.1", "split": "unsplit", "source": "handwritten_golden", "license": "project_internal", "design_family": example.family, "task_family": example.task_type, "created_by": "human", "review_status": "reviewed",
        "provenance": {"origin": "handwritten", "public_dataset_name": None, "public_dataset_url": None, "source_commit": None, "notes": "Concrete synthetic public-safe RTL/report example."},
        "tool_checks": _tool_checks(example), "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}, {"role": "assistant", "content": answer}],
    }


def main() -> None:
    output = Path(__file__).resolve().parents[2] / "data" / "golden" / "golden_v0.1.jsonl"
    write_jsonl(output, [make_row(index, example) for index, example in enumerate(EXAMPLES)])


if __name__ == "__main__":
    main()
