"""Stable interface for local public-dataset discovery and draft conversion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawPublicExample:
    source_id: str
    root: Path
    artifacts: dict[str, str]
    source: str
    license: str
    design_family: str
    task_type: str
    user_goal: str
    provenance: dict[str, object]
    metadata: dict[str, object]


@dataclass(frozen=True)
class ImportOptions:
    source: str | None = None
    license: str | None = None
    limit: int | None = None
    allow_absolute_paths: bool = False
    allow_outside_root: bool = False
    max_artifact_bytes: int = 1_048_576


@dataclass(frozen=True)
class ImportRejection:
    source_id: str | None
    reason: str
    errors: list[str]
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    examples: list[RawPublicExample]
    rejections: list[ImportRejection]
    warnings: list[str]
    discovered_examples: int


class PublicDatasetAdapter(ABC):
    name: str

    @abstractmethod
    def discover_examples(self, root: Path, options: ImportOptions) -> DiscoveryResult:
        """Discover examples already present under root; never download them."""
