"""RTLFixer local-data adapter skeleton."""
from .base import PublicDatasetAdapter, RawPublicExample

class RTLFixerAdapter(PublicDatasetAdapter):
    name = "rtlfixer"
    def discover_examples(self, root):
        raise NotImplementedError("TODO v0.2: discover explicitly supplied local RTLFixer data")
    def to_draft_row(self, example: RawPublicExample) -> dict:
        raise NotImplementedError("TODO v0.2: map a local example to dataset_v0.1 draft format")

