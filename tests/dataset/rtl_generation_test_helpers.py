from __future__ import annotations

import json
from pathlib import Path


def make_checkout(root: Path, *, rows: int = 3, with_support: bool = False) -> Path:
    checkout = root / "verilog-eval"
    dataset = checkout / ("dataset_code-complete-iccad2023" if with_support else "dataset_spec-to-rtl")
    dataset.mkdir(parents=True)
    (checkout / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    for index in range(1, rows + 1):
        source_id = f"Prob{index:03d}_task"
        prompt = f"Implement module named TopModule with the following interface.\n - input clk\n - input d\n - output q\nTask {index}.\n"
        rtl = f"module RefModule(input clk, input d, output reg q); always @(posedge clk) q <= d; endmodule\n"
        testbench = f"module tb; TopModule dut(); // synthetic checker text\nendmodule\n"
        (dataset / f"{source_id}_prompt.txt").write_text(prompt, encoding="utf-8")
        (dataset / f"{source_id}_ref.sv").write_text(rtl, encoding="utf-8")
        (dataset / f"{source_id}_test.sv").write_text(testbench, encoding="utf-8")
        if with_support:
            (dataset / f"{source_id}_ifc.txt").write_text("module TopModule(input clk, input d, output q); endmodule\n", encoding="utf-8")
    return dataset


def normalized_task_from_raw(raw: dict) -> dict:
    clocks = raw.get("deterministic_clock_hints") or []
    resets = raw.get("deterministic_reset_hints") or []
    return {
        "schema_version": "rtl_generation_task_v0.1",
        "task_id": raw["task_id"],
        "source_id": raw["source_id"],
        "source_dataset": raw["source_dataset"],
        "design_family": raw["design_family"],
        "language": "systemverilog",
        "specification": raw["raw_specification"],
        "top_module": raw["deterministic_top_module_hint"],
        "interface": {"ports": raw["deterministic_interface_hints"]},
        "clocking": {
            "clock_signal": clocks[0]["signal"] if clocks else None,
            "edge": clocks[0]["edge"] if clocks else None,
        },
        "reset": {
            "signal": resets[0]["signal"] if resets else None,
            "active_level": resets[0]["active_level"] if resets else None,
            "synchronous": resets[0]["synchronous"] if resets else None,
        },
        "latency_contract": None,
        "behavioral_constraints": [],
        "assumptions": [],
        "ambiguities": [],
        "provenance": raw["provenance"],
    }


def load_batch(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
