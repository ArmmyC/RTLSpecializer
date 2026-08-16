"""Validate coverage of immutable verified RTL generation packages."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_train_coverage import coverage_main


if __name__ == "__main__":
    raise SystemExit(coverage_main())
