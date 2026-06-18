"""Public dataset adapter contracts; adapters never download data."""

from .base import DiscoveryResult, ImportOptions, ImportRejection, PublicDatasetAdapter, RawPublicExample
from .manifest import ManifestAdapter
from .rtlfixer import RTLFixerAdapter
from .rtllm import RTLLMAdapter
from .verilog_eval import VerilogEvalAdapter

_ADAPTERS = {
    "manifest": ManifestAdapter,
    "verilog_eval": VerilogEvalAdapter,
    "rtllm": RTLLMAdapter,
    "rtlfixer": RTLFixerAdapter,
}


def get_adapter(name: str) -> PublicDatasetAdapter:
    try:
        return _ADAPTERS[name]()
    except KeyError as exc:
        allowed = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"unknown adapter {name!r}; expected one of: {allowed}") from exc


__all__ = [
    "DiscoveryResult", "ImportOptions", "ImportRejection", "PublicDatasetAdapter",
    "RawPublicExample", "get_adapter",
]
