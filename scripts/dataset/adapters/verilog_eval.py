"""Conservative VerilogEval local-data adapter."""

from pathlib import Path
import json
import re

from .base import DiscoveryResult, ImportOptions, ImportRejection, PublicDatasetAdapter, RawPublicExample
from .manifest import ManifestAdapter
from scripts.dataset.constants import ARTIFACT_FIELDS

class VerilogEvalAdapter(PublicDatasetAdapter):
    name = "verilog_eval"

    def discover_examples(self, root: Path, options: ImportOptions) -> DiscoveryResult:
        manifest = root / "manifest.jsonl" if root.is_dir() else root
        if manifest.exists() and manifest.name == "manifest.jsonl":
            result = ManifestAdapter().discover_examples(manifest, options)
            augmented: list[RawPublicExample] = []
            for example in result.examples:
                metadata = dict(example.metadata)
                prompt = example.artifacts.get("lint_log")
                if isinstance(prompt, str) and prompt.strip():
                    metadata.setdefault("raw_prompt", prompt)
                augmented.append(RawPublicExample(
                    source_id=example.source_id,
                    root=example.root,
                    artifacts=example.artifacts,
                    source=example.source,
                    license=example.license,
                    design_family=example.design_family,
                    task_type=example.task_type,
                    user_goal=example.user_goal,
                    provenance=example.provenance,
                    metadata=metadata,
                ))
            return DiscoveryResult(augmented, result.rejections, result.warnings, result.discovered_examples)
        if root.is_file() and root.suffix == ".jsonl":
            return _discover_jsonl(root, options)
        if root.is_dir():
            return _discover_directory(root, options)
        return DiscoveryResult(
            [],
            [ImportRejection(None, "VerilogEval layout not recognized", ["expected manifest.jsonl, supported JSONL, or dataset_spec-to-rtl/*.txt/*.sv files"])],
            [],
            0,
        )


def _read_text(path: Path, max_bytes: int) -> tuple[str | None, str | None]:
    try:
        if not path.exists():
            return None, f"missing file: {path}"
        if path.stat().st_size > max_bytes:
            return None, f"file too large: {path}"
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return None, f"could not read {path}: {exc}"


def _family_from_id(source_id: str, prompt: str, rtl: str) -> str:
    text = f"{source_id} {prompt} {rtl}".lower()
    for name, patterns in {
        "fsm": ("fsm", "state"),
        "counter": ("count", "counter", "timer"),
        "shift_register": ("shift", "rotate"),
        "mux": ("mux", "multiplexer"),
        "decoder": ("decoder", "decode"),
        "register": ("dff", "flip-flop", "register"),
        "arithmetic": ("adder", "add", "popcount"),
    }.items():
        if any(pattern in text for pattern in patterns):
            return name
    return "verilog_eval"


def _example(source_id: str, root: Path, prompt: str, rtl: str, testbench: str | None, source: str, license_value: str | None, metadata: dict) -> RawPublicExample:
    metadata = dict(metadata)
    metadata["raw_prompt"] = prompt
    artifacts = {name: None for name in ARTIFACT_FIELDS}
    artifacts["rtl_code"] = rtl
    artifacts["testbench"] = testbench
    artifacts["lint_log"] = f"VerilogEval prompt/specification for reviewer context:\n{prompt}"
    family = _family_from_id(source_id, prompt, rtl)
    return RawPublicExample(
        source_id=source_id,
        root=root,
        artifacts=artifacts,  # type: ignore[arg-type]
        source=source,
        license=license_value or "see_upstream_verilog_eval",
        design_family=family,
        task_type="rtl_bug_review",
        user_goal="find_correctness_bug",
        provenance={
            "public_dataset_name": "VerilogEval",
            "public_dataset_url": "https://github.com/NVlabs/verilog-eval",
            "source_commit": None,
            "notes": "Local VerilogEval artifact staged by user; verify exact license/provenance before promotion.",
        },
        metadata=metadata,
    )


def _discover_directory(root: Path, options: ImportOptions) -> DiscoveryResult:
    dataset_dir = root / "dataset_spec-to-rtl"
    if not dataset_dir.exists():
        dataset_dir = root / "dataset_code-complete-iccad2023"
    if not dataset_dir.exists():
        return DiscoveryResult([], [ImportRejection(None, "VerilogEval directory layout not recognized", ["expected dataset_spec-to-rtl or dataset_code-complete-iccad2023"] )], [], 0)
    examples: list[RawPublicExample] = []
    rejections: list[ImportRejection] = []
    for prompt_path in sorted(dataset_dir.glob("*_prompt.txt")):
        stem = prompt_path.name[:-len("_prompt.txt")]
        if options.limit is not None and len(examples) >= options.limit:
            break
        ref_path = dataset_dir / f"{stem}_ref.sv"
        test_path = dataset_dir / f"{stem}_test.sv"
        prompt, prompt_error = _read_text(prompt_path, options.max_artifact_bytes)
        rtl, rtl_error = _read_text(ref_path, options.max_artifact_bytes)
        if prompt_error or rtl_error:
            rejections.append(ImportRejection(stem, "missing prompt or RTL", [error for error in (prompt_error, rtl_error) if error]))
            continue
        testbench = None
        if test_path.exists():
            testbench, _ = _read_text(test_path, options.max_artifact_bytes)
        examples.append(_example(
            stem, dataset_dir, prompt or "", rtl or "", testbench,
            options.source or "public_verilog_eval", options.license,
            {"layout": "verilog_eval_directory", "prompt_path": str(prompt_path), "rtl_path": str(ref_path)},
        ))
    return DiscoveryResult(examples, rejections, [], len(examples) + len(rejections))


def _pick(row: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _discover_jsonl(path: Path, options: ImportOptions) -> DiscoveryResult:
    examples: list[RawPublicExample] = []
    rejections: list[ImportRejection] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return DiscoveryResult([], [ImportRejection(None, "could not read VerilogEval JSONL", [str(exc)])], [], 0)
    for line_number, raw in enumerate(lines, 1):
        if options.limit is not None and len(examples) >= options.limit:
            break
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            rejections.append(ImportRejection(None, "malformed JSONL", [f"line {line_number}: {exc.msg}"]))
            continue
        if not isinstance(row, dict):
            rejections.append(ImportRejection(None, "JSONL row must be object", [f"line {line_number}: row must be object"]))
            continue
        source_id = _pick(row, ("task_id", "problem_id", "id", "name")) or f"line_{line_number}"
        prompt = _pick(row, ("prompt", "instruction", "description", "spec"))
        rtl = _pick(row, ("canonical_solution", "solution", "rtl", "verilog", "reference_solution"))
        testbench = _pick(row, ("test", "testbench", "checker"))
        if not prompt or not rtl or not re.search(r"\bmodule\b", rtl):
            rejections.append(ImportRejection(source_id, "missing prompt/spec or RTL", [f"line {line_number}: could not identify prompt/spec and module RTL"]))
            continue
        examples.append(_example(
            source_id, path.parent, prompt, rtl, testbench,
            options.source or "public_verilog_eval", options.license,
            {"layout": "verilog_eval_jsonl", "line": line_number, "category": row.get("category"), "difficulty": row.get("difficulty")},
        ))
    return DiscoveryResult(examples, rejections, [], len(examples) + len(rejections))
