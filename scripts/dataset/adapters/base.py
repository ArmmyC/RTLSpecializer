"""Stable interface for local public-dataset discovery and draft conversion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawPublicExample:
    source_id: str
    path: Path
    content: str
    license: str | None = None
    metadata: dict[str, object] | None = None


class PublicDatasetAdapter(ABC):
    name: str

    @abstractmethod
    def discover_examples(self, root: Path) -> list[RawPublicExample]:
        """Discover examples already present under root; never download them."""

    @abstractmethod
    def to_draft_row(self, example: RawPublicExample) -> dict:
        """Convert one raw example to a review-required dataset draft."""

