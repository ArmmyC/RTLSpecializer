from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_powershell_template_uses_expected_safe_inputs() -> None:
    script_path = ROOT / "scripts" / "eval" / "run_qwen2_5_coder_7b_base_eval.ps1"
    text = script_path.read_text(encoding="utf-8")

    assert "RTLSPEC_EVAL_API_KEY" in text
    assert "sk-armmy" not in text
    assert "docs/eval/rtl_answer_schema_reminder.md" in text
    assert "qwen2_5_coder_7b_base_schema" in text
    assert "response-format-json" in text
    assert "rtlcoder_synthetic_active_model_base_schema" in text
    assert "rtlcoder_synthetic_rule_baseline" in text


def test_base_eval_doc_places_base_eval_before_lora_training() -> None:
    doc_path = ROOT / "docs" / "eval" / "qwen2_5_coder_7b_base_eval.md"
    text = doc_path.read_text(encoding="utf-8")

    assert "Qwen/Qwen2.5-Coder-7B-Instruct" in text
    assert "/v1/models" in text
    assert "before any LoRA training" in text
    assert "run_qwen2_5_coder_7b_base_eval.ps1" in text
    assert "rtl_answer_schema_reminder.md" in text
