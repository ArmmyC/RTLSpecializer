from __future__ import annotations
import json
from pathlib import Path
import pytest
from scripts.eval.check_qwen2_5_coder_7b_lora_acceptance import check, load

def _reports():
    metrics={"candidate_rows":100,"matched_rows":100,"missing_candidates":0,"extra_candidates":0,"safety_failures":0,"mean_score":0.9964}
    diff={"name_b":"lora","duplicate_analysis":{"lora":{"exact_duplicate_groups":[],"near_duplicate_pairs":[]}},"row_differences":[{"mentions_mutation_type":{"lora":True},"mentions_mutated_signal_names":{"lora":True}} for _ in range(100)]}
    candidate={"parse_error_rows":0,"api_error_rows":0}
    return metrics, metrics.copy(), diff, candidate

def test_acceptance_passes_complete_reports():
    assert check(*_reports())["accepted"] is True

def test_acceptance_fails_each_mandatory_gate():
    for key in ("candidate_rows","matched_rows","missing_candidates","extra_candidates","safety_failures","mean_score"):
        lora,base,diff,candidate=_reports()
        lora[key]=0.994 if key == "mean_score" else (99 if key in {"candidate_rows","matched_rows"} else 1)
        assert check(lora,base,diff,candidate)["accepted"] is False
    for key in ("parse_error_rows","api_error_rows"):
        lora,base,diff,candidate=_reports(); candidate[key]=1
        assert check(lora,base,diff,candidate)["accepted"] is False
    lora,base,diff,candidate=_reports(); diff["duplicate_analysis"]["lora"]["exact_duplicate_groups"]=[{"ids":["a","b"]}]
    assert check(lora,base,diff,candidate)["accepted"] is False
    lora,base,diff,candidate=_reports()
    for row in diff["row_differences"][:3]: row["mentions_mutation_type"]["lora"]=False
    assert check(lora,base,diff,candidate)["accepted"] is False
    lora,base,diff,candidate=_reports(); diff["row_differences"][0]["mentions_mutated_signal_names"]["lora"]=False
    assert check(lora,base,diff,candidate)["accepted"] is False

def test_missing_metric_fails_closed():
    lora,base,diff,candidate=_reports(); del lora["mean_score"]
    assert check(lora,base,diff,candidate)["accepted"] is False

def test_missing_and_malformed_reports_fail_closed(tmp_path):
    with pytest.raises(ValueError): load(tmp_path / "missing.json")
    malformed=tmp_path / "bad.json"; malformed.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError): load(malformed)
