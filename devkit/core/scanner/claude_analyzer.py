from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path

import anthropic

from .prompts import AI_ADDITIONS, API_ADDITIONS, SECURITY_SYSTEM_PROMPT, WEB_ADDITIONS

CHUNK_SIZE_LINES = 2_000
TIER2_LINE_CAP = 8_000


class ClaudeAnalyzer:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

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

        tasks = [self._analyze_chunk(chunk, system_prompt) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_findings: list[dict] = []
        for result in results:
            if isinstance(result, Exception):
                continue  # one bad chunk doesn't abort the whole scan
            all_findings.extend(result)  # type: ignore[arg-type]
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
        """Concatenate files with headers and split into <=2000-line blocks.

        Tier 1 files are included in full. Tier 2 files share a line budget.
        """
        lines: list[str] = []

        for path in tier1_files:
            content = self._read_safe(path)
            if content is None:
                continue
            lines.append(f"# --- FILE: {path} ---")
            lines.extend(content.splitlines())
            lines.append("")

        tier2_budget = TIER2_LINE_CAP
        for path in tier2_files:
            if tier2_budget <= 0:
                break
            content = self._read_safe(path)
            if content is None:
                continue
            file_lines = content.splitlines()
            allowed = min(len(file_lines), tier2_budget)
            lines.append(f"# --- FILE: {path} ---")
            lines.extend(file_lines[:allowed])
            lines.append("")
            tier2_budget -= allowed

        chunks: list[str] = []
        for i in range(0, len(lines), CHUNK_SIZE_LINES):
            chunk = "\n".join(lines[i : i + CHUNK_SIZE_LINES])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    async def _analyze_chunk(self, chunk: str, system_prompt: str) -> list[dict]:
        """Single Claude API call with prompt caching on the stable system prompt."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": chunk}],
        )
        return self._parse_response(response.content[0].text)

    def _parse_response(self, text: str) -> list[dict]:
        """Parse Claude JSON response; strip markdown fences if present."""
        stripped = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
        stripped = re.sub(r"```\s*$", "", stripped.strip(), flags=re.MULTILINE)
        try:
            data = json.loads(stripped.strip())
        except json.JSONDecodeError:
            return []
        findings = data.get("findings", [])
        return findings if isinstance(findings, list) else []

    @staticmethod
    def _read_safe(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
