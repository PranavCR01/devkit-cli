from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path

import anthropic

from devkit.config import DEFAULT_MODEL

from .prompts import AI_ADDITIONS, API_ADDITIONS, SECURITY_SYSTEM_PROMPT, WEB_ADDITIONS

CHUNK_SIZE_LINES = 2_000
TIER2_LINE_CAP = 8_000


class ClaudeAnalyzer:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client=None,
        max_concurrency: int = 4,
        tier2_line_cap: int = TIER2_LINE_CAP,
    ) -> None:
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_concurrency = max(1, max_concurrency)
        self.tier2_line_cap = tier2_line_cap
        self.total_chunks = 0
        self.failed_chunks = 0
        self.errors: list[str] = []

    async def analyze(
        self,
        tier1_files: list[str],
        tier2_files: list[str],
        mode: str,
    ) -> list[dict]:
        """Analyze files with Claude. Returns raw findings list."""
        system_prompt = self._build_system_prompt(mode)
        chunks = self._build_chunks(tier1_files, tier2_files)
        if not chunks:
            return []

        self.total_chunks = len(chunks)
        sem = asyncio.Semaphore(self.max_concurrency)

        async def _bounded(chunk: str):
            async with sem:
                return await self._analyze_chunk(chunk, system_prompt)

        results = await asyncio.gather(*(_bounded(c) for c in chunks), return_exceptions=True)

        all_findings: list[dict] = []
        for result in results:
            if isinstance(result, BaseException):
                # One bad chunk doesn't abort the scan, but it is counted: a scan
                # whose chunks failed must not be graded as if the code were clean.
                self.failed_chunks += 1
                self.errors.append(f"{type(result).__name__}: {result}")
                continue
            all_findings.extend(result)
        return all_findings

    def _build_system_prompt(self, mode: str) -> str:
        additions = {"web": WEB_ADDITIONS, "api": API_ADDITIONS, "ai": AI_ADDITIONS}
        if mode in additions:
            return SECURITY_SYSTEM_PROMPT + additions[mode]
        # "all" mode: include every addition
        return SECURITY_SYSTEM_PROMPT + WEB_ADDITIONS + API_ADDITIONS + AI_ADDITIONS

    def _build_chunks(
        self,
        tier1_files: list[str],
        tier2_files: list[str],
    ) -> list[str]:
        """Line-numbered file blocks packed into <=CHUNK_SIZE_LINES chunks.

        Every line is prefixed with its 1-based line number so Claude can report
        exact line_start values. A file split across chunks repeats its header
        (marked 'continued') so no chunk contains anonymous code.
        Tier 1 files are included in full; Tier 2 files share a line budget.
        """
        files: list[tuple[str, list[str]]] = []
        for path in tier1_files:
            content = self._read_safe(path)
            if content is not None:
                files.append((path, content.splitlines()))
        budget = self.tier2_line_cap
        for path in tier2_files:
            if budget <= 0:
                break
            content = self._read_safe(path)
            if content is None:
                continue
            lines = content.splitlines()[:budget]
            budget -= len(lines)
            files.append((path, lines))

        chunks: list[str] = []
        current: list[str] = []
        for path, lines in files:
            start = 0
            while start < len(lines) or (start == 0 and not lines):
                room = CHUNK_SIZE_LINES - len(current) - 2
                if room <= 0:
                    chunks.append("\n".join(current))
                    current = []
                    continue
                part = lines[start:start + room]
                header = f"# --- FILE: {path} ---" if start == 0 else \
                    f"# --- FILE: {path} (continued from line {start + 1}) ---"
                current.append(header)
                current.extend(f"{start + i + 1}| {line}" for i, line in enumerate(part))
                current.append("")
                start += len(part)
                if not lines:
                    break
        if current:
            chunks.append("\n".join(current))
        return [c for c in chunks if c.strip()]

    async def _analyze_chunk(self, chunk: str, system_prompt: str) -> list[dict]:
        """Single Claude API call with prompt caching on the stable system prompt."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": chunk}],
        )
        if getattr(response, "stop_reason", None) in ("max_tokens", "refusal"):
            raise RuntimeError(f"Claude response incomplete (stop_reason={response.stop_reason})")
        text = "".join(getattr(b, "text", "") for b in response.content
                       if getattr(b, "type", "") == "text")
        return self._parse_response(text)

    def _parse_response(self, text: str) -> list[dict]:
        """Parse Claude's JSON; raise on malformed output so the chunk counts as failed."""
        stripped = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
        stripped = re.sub(r"```\s*$", "", stripped.strip(), flags=re.MULTILINE)
        data = json.loads(stripped.strip())  # JSONDecodeError -> failed chunk
        if isinstance(data, list):
            findings = data
        elif isinstance(data, dict):
            findings = data.get("findings", [])
        else:
            raise ValueError(f"unexpected JSON type {type(data).__name__}")
        if not isinstance(findings, list):
            raise ValueError("'findings' is not a list")
        return [f for f in findings if isinstance(f, dict)]

    @staticmethod
    def _read_safe(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
