from __future__ import annotations
import asyncio
import copy
import json
import uuid
from pathlib import Path

import anthropic

ABLATION_TRIGGER_TOKENS = 2000
ABLATION_SAMPLE_RATE = 0.20
ABLATION_MIN_CHUNK_TOKENS = 100
SUGGESTIONS_DIR = Path.home() / ".devkit" / "eval" / "suggestions"
# Correct Headroom bypass header (x-headroom-bypass, not X-Headroom-Skip)
SKIP_HEADER = "x-headroom-bypass"


class AblationWorker:
    """Async background worker that tests context chunk removal.

    Uses Haiku + x-headroom-bypass to exclude ablation calls from
    Headroom compression (we need uncompressed calls for accurate delta measurement).

    Never ablates content before the last cache_control breakpoint —
    doing so would invalidate the cache prefix and raise net cost.
    """

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self._queue: asyncio.Queue = asyncio.Queue()

    async def run(self) -> None:
        import random
        while True:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if random.random() < ABLATION_SAMPLE_RATE:
                    await self._process(job)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception:
                pass

    async def enqueue(self, request_body: dict) -> None:
        tokens = self._rough_tokens(request_body)
        if tokens >= ABLATION_TRIGGER_TOKENS:
            await self._queue.put(request_body)

    async def _process(self, body: dict) -> None:
        last_bp = self._find_last_cache_breakpoint(body)
        chunks = self._segment_context(body, last_bp)
        if not chunks:
            return

        original_output = await self._get_output(body)

        for chunk in chunks:
            if self._rough_tokens_text(chunk["content"]) < ABLATION_MIN_CHUNK_TOKENS:
                continue
            reduced_body = self._remove_chunk(body, chunk)
            reduced_output = await self._get_output(reduced_body)
            delta = self._jaccard_distance(original_output, reduced_output)
            if delta < 0.15:
                self._save_candidate(chunk, original_output, reduced_output,
                                     self._rough_tokens_text(chunk["content"]))

    async def _get_output(self, body: dict) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_output_sync, body)

    def _get_output_sync(self, body: dict) -> str:
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=min(body.get("max_tokens", 1000), 1000),
                messages=body.get("messages", []),
                extra_headers={SKIP_HEADER: "true"},
            )
            return response.content[0].text if response.content else ""
        except Exception:
            return ""

    def _find_last_cache_breakpoint(self, body: dict) -> int:
        last = -1
        for i, msg in enumerate(body.get("messages", [])):
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("cache_control"):
                        last = i
        return last

    def _segment_context(self, body: dict, last_breakpoint: int) -> list[dict]:
        chunks = []
        for i, msg in enumerate(body.get("messages", [])):
            if i <= last_breakpoint:
                continue
            content = msg.get("content", [])
            if isinstance(content, list):
                for j, block in enumerate(content):
                    if isinstance(block, dict) and block.get("type") == "text":
                        chunks.append({"message_idx": i, "block_idx": j, "content": block["text"]})
            elif isinstance(content, str) and content:
                chunks.append({"message_idx": i, "block_idx": -1, "content": content})
        return chunks

    def _remove_chunk(self, body: dict, chunk: dict) -> dict:
        reduced = copy.deepcopy(body)
        msg = reduced["messages"][chunk["message_idx"]]
        if chunk["block_idx"] >= 0 and isinstance(msg["content"], list):
            msg["content"].pop(chunk["block_idx"])
        elif chunk["block_idx"] == -1:
            msg["content"] = ""
        return reduced

    def _save_candidate(self, chunk: dict, original_output: str, reduced_output: str,
                        tokens_saved: float) -> None:
        SUGGESTIONS_DIR.mkdir(parents=True, exist_ok=True)
        cid = str(uuid.uuid4())[:8]
        (SUGGESTIONS_DIR / f"candidate-{cid}.json").write_text(json.dumps({
            "id": cid,
            "chunk_preview": chunk["content"][:200],
            "tokens_saved": int(tokens_saved),
            "original_output": original_output[:500],
            "reduced_output": reduced_output[:500],
            "verified": False,
            "verdict": None,
        }, indent=2))

    def _rough_tokens(self, body: dict) -> int:
        return sum(len(str(m.get("content", ""))) for m in body.get("messages", [])) // 4

    def _rough_tokens_text(self, text: str) -> float:
        return len(text.split()) * 1.3

    def _jaccard_distance(self, a: str, b: str) -> float:
        if not a or not b:
            return 1.0
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa:
            return 1.0
        return 1.0 - len(sa & sb) / len(sa | sb)
