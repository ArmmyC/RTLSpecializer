#!/usr/bin/env python3
"""Apply fixed held-out acceptance gates to the Qwen2.5-Coder-7B LoRA pilot."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

THRESHOLDS={"candidate_rows":100,"matched_rows":100,"missing_candidates":0,"extra_candidates":0,"parse_error_rows":0,"api_error_rows":0,"safety_failures":0,"mean_score":0.995,"exact_duplicate_groups":0,"mutation_type_mentions":98,"mutated_signal_mentions":100}

def load(path: Path) -> dict[str,Any]:
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"could not read {path}: {exc}") from exc
    if not isinstance(value,dict): raise ValueError(f"report must be an object: {path}")
    return value

def check(lora: dict[str,Any], base: dict[str,Any], diff: dict[str,Any], candidate: dict[str,Any]) -> dict[str,Any]:
    errors=[]; checks={}
    name=diff.get("name_b"); base_name=diff.get("name_a")
    duplicate_analysis=diff.get("duplicate_analysis")
    if not isinstance(name,str) or not isinstance(base_name,str) or not isinstance(duplicate_analysis,dict):
        return {"accepted":False,"checks":{},"base_reference":{},"lora_result":{},"deltas":{},"errors":["difference report is missing named duplicate analysis"],"warnings":[]}
    analysis=duplicate_analysis.get(name); base_analysis=duplicate_analysis.get(base_name)
    if not isinstance(analysis,dict) or not isinstance(base_analysis,dict):
        return {"accepted":False,"checks":{},"base_reference":{},"lora_result":{},"deltas":{},"errors":["difference report is missing duplicate analysis for base or LoRA"],"warnings":[]}
    rows=diff.get("row_differences",[]) if isinstance(diff.get("row_differences"),list) else []
    mutation=sum(bool((row.get("mentions_mutation_type") or {}).get(name)) for row in rows)
    signal=sum(bool((row.get("mentions_mutated_signal_names") or {}).get(name)) for row in rows)
    values={**{key:lora.get(key) for key in ("candidate_rows","matched_rows","missing_candidates","extra_candidates","safety_failures","mean_score")},"parse_error_rows":candidate.get("parse_error_rows"),"api_error_rows":candidate.get("api_error_rows"),"exact_duplicate_groups":len(analysis.get("exact_duplicate_groups",[])) if isinstance(analysis,dict) else None,"mutation_type_mentions":mutation,"mutated_signal_mentions":signal}
    for key, threshold in THRESHOLDS.items():
        value=values.get(key)
        ok=isinstance(value,(int,float)) and (value>=threshold if key in {"mean_score","mutation_type_mentions","mutated_signal_mentions"} else value==threshold)
        checks[key]={"value":value,"required":threshold,"passed":ok}
        if not ok: errors.append(f"mandatory check failed: {key}")
    base_score=base.get("mean_score")
    near_base=len(base_analysis.get("near_duplicate_pairs",[])) if isinstance(base_analysis.get("near_duplicate_pairs"),list) else None
    near_lora=len(analysis.get("near_duplicate_pairs",[])) if isinstance(analysis.get("near_duplicate_pairs"),list) else None
    return {"accepted":not errors,"checks":checks,"base_reference":{"base_mean_score":base_score,"base_near_duplicate_pairs":near_base},"lora_result":{"lora_mean_score":lora.get("mean_score"),"lora_near_duplicate_pairs":near_lora},"deltas":{"mean_score_delta_vs_base":round(lora.get("mean_score",0)-base_score,6) if isinstance(base_score,(int,float)) and isinstance(lora.get("mean_score"),(int,float)) else None,"near_duplicate_delta":near_lora-near_base if isinstance(near_lora,int) else None},"errors":errors,"warnings":["near-duplicate behavior is reported but is not an automatic failure"]}

def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--lora-metrics",type=Path,required=True);p.add_argument("--base-metrics",type=Path,required=True);p.add_argument("--difference-report",type=Path,required=True);p.add_argument("--candidate-report",type=Path,required=True);p.add_argument("--output-json",type=Path,required=True);p.add_argument("--output-md",type=Path,required=True);p.add_argument("--json",action="store_true");a=p.parse_args(argv)
 try: result=check(load(a.lora_metrics),load(a.base_metrics),load(a.difference_report),load(a.candidate_report))
 except ValueError as exc: result={"accepted":False,"checks":{},"base_reference":{},"lora_result":{},"deltas":{},"errors":[str(exc)],"warnings":[]}
 a.output_json.parent.mkdir(parents=True,exist_ok=True);a.output_json.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
 a.output_md.parent.mkdir(parents=True,exist_ok=True);a.output_md.write_text("# LoRA Pilot Acceptance\n\n"+json.dumps(result,indent=2)+"\n",encoding="utf-8")
 if a.json: print(json.dumps(result,indent=2))
 return 0 if result["accepted"] else 1
if __name__=="__main__": raise SystemExit(main())
