from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from devkit.core.memory.store import MemoryStore, SearchResult

from .classifier import FileClassifier
from .claude_analyzer import ClaudeAnalyzer
from .graph_guide import GraphGuide
from .scorer import calculate_scores
from .semgrep_runner import SemgrepRunner


@dataclass
class Finding:
    id: str
    category: Literal["security", "quality", "ai_antipattern"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    plain_english_desc: str
    business_impact: str
    fix_snippet: str
    file_path: str
    line_start: int
    line_end: int | None
    owasp_ref: str | None
    cwe_ref: str | None
    source: Literal["semgrep", "claude"]
    blast_radius: list[str] = field(default_factory=list)
    memory_match: "SearchResult | None" = field(default=None)


@dataclass
class ScanResult:
    scan_id: str
    project_path: str
    mode: str
    security_score: int
    quality_score: int
    grade: Literal["A", "B", "C", "D", "F"]
    findings: list[Finding]
    scan_duration_seconds: float
    files_scanned: int
    lines_scanned: int
    graph_guided: bool


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class ScanOrchestrator:
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        memory_store: "MemoryStore | None" = None,
        auto_learn: bool = False,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._memory_store = memory_store
        self._auto_learn = auto_learn

    async def run(
        self,
        path: str,
        mode: Literal["web", "api", "ai", "all"] = "all",
        use_graph: bool = True,
        use_semgrep: bool = True,
        use_claude: bool = True,
    ) -> ScanResult:
        start = time.monotonic()
        scan_id = str(uuid.uuid4())

        # 1. Load knowledge graph (optional)
        guide = GraphGuide(path)
        graph_guided = guide.available and use_graph
        graph = guide.graph if graph_guided else None

        # 2. Classify files
        classifier = FileClassifier()
        tier1_files, tier2_files = classifier.classify(path, graph=graph)

        # 3. Parallel semgrep + Claude
        semgrep_raw, claude_raw = await asyncio.gather(
            self._run_semgrep(path, mode, use_semgrep),
            self._run_claude(tier1_files, tier2_files, mode, use_claude),
        )

        # 4. Merge (semgrep wins on structure, Claude enriches prose)
        merged = self._merge(semgrep_raw, claude_raw)

        # 5. Score
        sec_score, qual_score, grade = calculate_scores(list(merged.values()))

        # 6. Blast radius + hydrate Finding objects
        findings: list[Finding] = []
        for raw in merged.values():
            blast = guide.get_blast_radius(raw["file_path"]) if graph_guided else []
            findings.append(_to_finding(raw, blast))

        findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 5))

        result = ScanResult(
            scan_id=scan_id,
            project_path=str(Path(path).resolve()),
            mode=mode,
            security_score=sec_score,
            quality_score=qual_score,
            grade=grade,
            findings=findings,
            scan_duration_seconds=round(time.monotonic() - start, 2),
            files_scanned=len(tier1_files) + len(tier2_files),
            lines_scanned=_count_lines(tier1_files + tier2_files),
            graph_guided=graph_guided,
        )

        if self._auto_learn and self._memory_store:
            await self._store_findings_in_memory(result)

        result.findings = self._enrich_with_memory(result.findings)

        return result

    async def _run_semgrep(self, path: str, mode: str, enabled: bool) -> list[dict]:
        if not enabled:
            return []
        runner = SemgrepRunner()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, runner.run, path, mode)

    async def _run_claude(
        self,
        tier1_files: list[str],
        tier2_files: list[str],
        mode: str,
        enabled: bool,
    ) -> list[dict]:
        if not enabled:
            return []
        analyzer = ClaudeAnalyzer(api_key=self._api_key, model=self._model)
        return await analyzer.analyze(tier1_files, tier2_files, mode)

    async def _store_findings_in_memory(self, result: ScanResult) -> None:
        """Store critical/high findings as vulnerability_pattern facts.

        Deduplicates by title within a single scan to avoid memory bloat.
        Medium/low are handled interactively in scan.py.
        """
        seen_titles: set[str] = set()
        for finding in result.findings:
            if finding.severity not in ("critical", "high"):
                continue
            if finding.title in seen_titles:
                continue
            seen_titles.add(finding.title)
            content = (
                f"Vulnerability pattern: {finding.title}. "
                f"Found in {finding.file_path}. "
                f"{finding.plain_english_desc} "
                f"Fix: {finding.fix_snippet[:200]}. "
                f"OWASP: {finding.owasp_ref}. CWE: {finding.cwe_ref}."
            )
            self._memory_store.save(
                content=content,
                fact_type="vulnerability_pattern",
                project=result.project_path,
                source="scan",
            )

    def _enrich_with_memory(self, findings: list[Finding]) -> list[Finding]:
        """Attach memory_match to findings where a past scan saw the same pattern.

        Threshold 0.020 is an RRF score equivalent to a rank-1 semantic result
        (spec's 0.80 cosine threshold; RRF scores range ~0.016-0.033).
        """
        if not self._memory_store:
            return findings
        for finding in findings:
            similar = self._memory_store.search(
                query=finding.title,
                fact_types=["vulnerability_pattern"],
                limit=1,
            )
            if similar and similar[0].score >= 0.020:
                finding.memory_match = similar[0]
        return findings

    @staticmethod
    def _merge(semgrep_raw: list[dict], claude_raw: list[dict]) -> dict[str, dict]:
        """Deduplicate on file_path:line_start. Semgrep wins; Claude enriches prose."""
        merged: dict[str, dict] = {}

        for f in claude_raw:
            key = f"{f.get('file_path', '')}:{f.get('line_start', 0)}"
            merged[key] = {**f, "source": "claude"}

        for f in semgrep_raw:
            key = f"{f.get('file_path', '')}:{f.get('line_start', 0)}"
            if key in merged:
                existing = merged[key]
                merged[key] = {
                    **f,
                    "source": "semgrep",
                    "plain_english_desc": existing.get("plain_english_desc") or f.get("plain_english_desc", ""),
                    "business_impact":    existing.get("business_impact")    or f.get("business_impact", ""),
                    "fix_snippet":        existing.get("fix_snippet")        or f.get("fix_snippet", ""),
                }
            else:
                merged[key] = {**f, "source": "semgrep"}

        return merged


def _to_finding(raw: dict, blast_radius: list[str]) -> Finding:
    return Finding(
        id=str(uuid.uuid4()),
        category=raw.get("category", "security"),
        severity=raw.get("severity", "info"),
        title=raw.get("title", ""),
        plain_english_desc=raw.get("plain_english_desc", ""),
        business_impact=raw.get("business_impact", ""),
        fix_snippet=raw.get("fix_snippet", ""),
        file_path=raw.get("file_path", ""),
        line_start=raw.get("line_start", 0),
        line_end=raw.get("line_end"),
        owasp_ref=raw.get("owasp_ref"),
        cwe_ref=raw.get("cwe_ref"),
        source=raw.get("source", "claude"),
        blast_radius=blast_radius,
    )


def _count_lines(file_paths: list[str]) -> int:
    total = 0
    for p in file_paths:
        try:
            total += Path(p).read_text(encoding="utf-8", errors="ignore").count("\n")
        except OSError:
            pass
    return total
