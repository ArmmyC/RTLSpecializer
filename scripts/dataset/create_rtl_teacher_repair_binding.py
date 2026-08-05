"""Create one immutable lineage binding for an RTL teacher repair handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import create_teacher_repair_binding


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--private-assets-root", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=Path)
    parser.add_argument("--prior-evidence", required=True, type=Path)
    parser.add_argument("--repair-packet", required=True, type=Path)
    parser.add_argument("--teacher-generation-binding", required=True, type=Path)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--previous-workspace-tree-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report, code = create_teacher_repair_binding(
        args.tasks,
        args.assets,
        args.private_assets_root,
        args.candidates,
        args.attempts,
        args.prior_evidence,
        args.repair_packet,
        args.teacher_generation_binding,
        args.output,
        candidate_id=args.candidate_id,
        previous_workspace_tree_sha256=args.previous_workspace_tree_sha256,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
