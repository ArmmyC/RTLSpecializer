"""RTLLM/RTLLM-2 local-data adapter skeleton."""
from .base import PublicDatasetAdapter, RawPublicExample

class RTLLMAdapter(PublicDatasetAdapter):
    name = "rtllm"
    def discover_examples(self, root):
        raise NotImplementedError("TODO v0.2: discover explicitly supplied local RTLLM data")
    def to_draft_row(self, example: RawPublicExample) -> dict:
        raise NotImplementedError("TODO v0.2: map a local example to dataset_v0.1 draft format")

