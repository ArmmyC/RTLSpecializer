"""Conservative RTLLM/RTLLM-2 local-data adapter."""

from pathlib import Path

from .base import DiscoveryResult, ImportOptions, ImportRejection, PublicDatasetAdapter
from .manifest import ManifestAdapter

class RTLLMAdapter(PublicDatasetAdapter):
    name = "rtllm"

    def discover_examples(self, root: Path, options: ImportOptions) -> DiscoveryResult:
        manifest = root / "manifest.jsonl" if root.is_dir() else root
        if manifest.exists() and manifest.name == "manifest.jsonl":
            return ManifestAdapter().discover_examples(manifest, options)
        return DiscoveryResult(
            [],
            [ImportRejection(None, "RTLLM layout not recognized", ["expected a local manifest.jsonl; see docs/dataset/public_manifest_format.md"])],
            [],
            0,
        )
