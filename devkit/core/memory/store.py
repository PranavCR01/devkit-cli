from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

FactType = Literal[
    "decision", "pattern", "bug", "architecture", "preference", "vulnerability_pattern"
]


@dataclass
class Fact:
    id: str
    project: str
    workstream: str | None
    content: str
    fact_type: FactType
    valid_at: str        # ISO 8601
    invalid_at: str | None  # None = currently valid
    created_at: str
    source: str          # "manual" | "scan" | "auto"
    confidence: float


@dataclass
class SearchResult:
    fact: Fact
    score: float         # RRF combined score
    match_type: str      # "semantic" | "keyword" | "hybrid"


class MemoryStore(ABC):
    """Abstract interface. v1 = SQLiteBackend. v2 = GraphitiBackend."""

    @abstractmethod
    def save(
        self,
        content: str,
        fact_type: FactType,
        project: str,
        workstream: str | None = None,
        source: str = "manual",
    ) -> Fact:
        """Save a fact. Automatically detects and handles contradictions."""

    @abstractmethod
    def search(
        self,
        query: str,
        projects: list[str] | None = None,
        fact_types: list[FactType] | None = None,
        limit: int = 10,
        include_invalid: bool = False,
    ) -> list[SearchResult]:
        """Hybrid semantic + keyword search across all stored facts."""

    @abstractmethod
    def contradict(self, fact_id: str, reason: str | None = None) -> None:
        """Explicitly invalidate a fact."""

    @abstractmethod
    def get_snapshot(self, project: str, token_cap: int = 2000) -> str:
        """Get session snapshot for injection. Returns capped text."""

    @abstractmethod
    def save_workstream(self, name: str, project: str, context: dict) -> None:
        """Save current workstream state."""

    @abstractmethod
    def load_workstream(self, name: str, project: str) -> dict | None:
        """Load a saved workstream state."""

    @abstractmethod
    def list_projects(self) -> list[str]:
        """List all projects with stored facts."""
