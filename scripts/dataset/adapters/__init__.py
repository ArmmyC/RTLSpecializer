"""Public dataset adapter contracts; adapters never download data."""

from .base import PublicDatasetAdapter, RawPublicExample

__all__ = ["PublicDatasetAdapter", "RawPublicExample"]

