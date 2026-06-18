"""VerilogEval local-data adapter skeleton."""
from .base import PublicDatasetAdapter, RawPublicExample

class VerilogEvalAdapter(PublicDatasetAdapter):
    name = "verilog_eval"
    def discover_examples(self, root):
        raise NotImplementedError("TODO v0.2: discover explicitly supplied local VerilogEval data")
    def to_draft_row(self, example: RawPublicExample) -> dict:
        raise NotImplementedError("TODO v0.2: map a local example to dataset_v0.1 draft format")

