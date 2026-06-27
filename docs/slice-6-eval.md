# DevKit — Slice 6: `/eval` (Headroom-backed)

## Overview

Slice 6 builds the token optimization and prompt evaluation layer by wrapping Headroom as the compression engine. DevKit does not reimplement proxy infrastructure — Headroom handles SSE passthrough, cache stabilization, and content compression. DevKit's `/eval` is the orchestration, reporting, ablation verification, and prompt regression layer on top.

**Prerequisite: Slices 1–5 fully working. `pip install "headroom-ai[proxy]"` available.**

**Build order: start → status/report → ablation → judge → versions. Do not jump ahead.**

---

## Division of Responsibility

| Concern | Owner |
|---------|-------|
| Proxy server + SSE passthrough | Headroom |
| Cache prefix stabilization | Headroom |
| Content compression (JSON, logs, code, text) | Headroom |
| CCR store (originals preserved) | Headroom |
| Verbosity steering | Headroom |
| Compression stats + session logs | Headroom (DevKit reads via API) |
| Ablation-based chunk verification | DevKit |
| Claude-as-judge with order-swap | DevKit |
| Prompt version tracking | DevKit (memory store) |
| Per-project waste reporting | DevKit |
| Integration with /memory | DevKit |

---

## Goals

- `devkit eval start` — starts Headroom proxy on port 8787, prints setup instructions
- `devkit eval stop` — stops Headroom proxy
- `devkit eval status` — proxy running? live stats from GET /stats
- `devkit eval report` — Headroom savings + DevKit ablation suggestions + judge verdicts
- `devkit eval learn` — wraps `headroom learn --verbosity`
- `devkit eval versions` — prompt version history from memory store
- `devkit eval verify <id>` — run Claude-as-judge on a pending suggestion
- DevKit reads ~/.headroom/logs/proxy.log (JSONL) for per-call data
- DevKit uses get_compression_store() Python API for CCR store access
- Ablation runs async — never blocks the session
- Judge uses Claude Haiku with order-swap bias mitigation

---

## Success Criteria

- `devkit eval start` starts Headroom and prints ANTHROPIC_BASE_URL setup instructions
- `devkit eval status` shows live stats from Headroom GET /stats
- Claude Code routes through Headroom after ANTHROPIC_BASE_URL is set
- `devkit eval report` reads ~/.headroom/logs/proxy.log and shows per-session savings
- At least one ablation suggestion generated and judge-verified in a real session
- DevKit's own API calls excluded from analysis (X-Headroom-Skip header)
- `devkit eval stop` cleanly terminates the Headroom process

---

## Key Integration Points (from Headroom source analysis)

| Point | Detail |
|-------|--------|
| Proxy entry | headroom/cli/proxy.py:804, Uvicorn on port 8787 |
| Stats API | GET http://127.0.0.1:8787/stats → JSON |
| Session logs | ~/.headroom/logs/proxy.log, JSONL, one line per call |
| CCR store | ~/.headroom/ccr_store.db, SQLite WAL, 30-min TTL |
| Python API | get_compression_store() singleton, fully importable |
| Skip header | X-Headroom-Skip: true — excludes call from compression |
| wrap behavior | Sets ANTHROPIC_BASE_URL=http://127.0.0.1:8787 in child env |
| MCP tools | headroom_compress, headroom_retrieve, headroom_stats, headroom_read |

---

## File Structure

```
devkit/
├── commands/
│   └── eval.py                # /eval command (start, stop, status, report, learn, verify, versions)
└── core/
    └── eval/
        ├── __init__.py
        ├── headroom_bridge.py  # Headroom integration: stats, logs, CCR store
        ├── ablation.py         # async ablation worker
        ├── judge.py            # Claude-as-judge with order-swap
        └── versions.py         # prompt version tracking via memory store

~/.headroom/                    # Headroom's directory (read-only for DevKit)
    ├── ccr_store.db
    └── logs/proxy.log

~/.devkit/eval/                 # DevKit's eval state
    ├── ablation_queue.jsonl
    └── suggestions/
        └── candidate-<id>.json
```

---

## headroom_bridge.py

```python
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HEADROOM_PORT = 8787
HEADROOM_BASE = f"http://127.0.0.1:{HEADROOM_PORT}"
HEADROOM_LOG = Path.home() / ".headroom" / "logs" / "proxy.log"
SKIP_HEADER = "X-Headroom-Skip"


class HeadroomBridge:
    def is_running(self) -> bool:
        try:
            resp = httpx.get(f"{HEADROOM_BASE}/stats", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "headroom", "proxy", "--port", str(HEADROOM_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        try:
            httpx.post(f"{HEADROOM_BASE}/admin/shutdown", timeout=3.0)
        except Exception:
            pass

    def get_stats(self) -> dict[str, Any]:
        try:
            return httpx.get(f"{HEADROOM_BASE}/stats", timeout=5.0).json()
        except Exception:
            return {}

    def get_session_calls(self, limit: int = 50) -> list[dict]:
        if not HEADROOM_LOG.exists():
            return []
        calls = []
        try:
            lines = HEADROOM_LOG.read_text(encoding="utf-8").strip().splitlines()
            for line in reversed(lines[-limit:]):
                try:
                    calls.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return list(reversed(calls))

    def compute_session_savings(self, calls: list[dict]) -> dict[str, Any]:
        total_input = sum(c.get("input_tokens", 0) for c in calls)
        total_compressed = sum(c.get("compressed_tokens", 0) for c in calls)
        total_saved = total_input - total_compressed
        ratio = (total_saved / total_input) if total_input > 0 else 0.0
        return {
            "calls": len(calls),
            "total_input_tokens": total_input,
            "total_compressed_tokens": total_compressed,
            "tokens_saved": total_saved,
            "compression_ratio": ratio,
            "estimated_cost_saved_usd": total_saved * 0.000003,
        }

    def setup_instructions(self) -> str:
        return (
            f"\nHeadroom proxy running on http://127.0.0.1:{HEADROOM_PORT}\n"
            f"\nAdd to ~/.claude/settings.json:\n"
            f'  {{"env": {{"ANTHROPIC_BASE_URL": "http://127.0.0.1:{HEADROOM_PORT}"}}}}\n'
            f"\nOr export in shell before starting Claude Code:\n"
            f"  export ANTHROPIC_BASE_URL=http://127.0.0.1:{HEADROOM_PORT}\n"
            f"\nRestart Claude Code after setting.\n"
        )
```

---

## ablation.py

```python
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
SKIP_HEADER = "X-Headroom-Skip"


class AblationWorker:
    """Async background worker that tests context chunk removal.

    Uses Haiku + X-Headroom-Skip to exclude ablation calls from
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

    def _save_candidate(self, chunk, original_output, reduced_output, tokens_saved):
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
```

---

## judge.py

```python
from __future__ import annotations
import asyncio
import json
from enum import Enum
import anthropic

SKIP_HEADER = "X-Headroom-Skip"

JUDGE_SYSTEM_PROMPT = """You are evaluating whether removing a context chunk produces an equivalent response.

Evaluate Response A vs Response B:
1. Code correctness — functionally equivalent?
2. Requirement completeness — all requirements addressed?
3. No lost context — no critical information missing?
4. No hallucinated APIs — no invented functions?

Return JSON only:
{
    "winner": "A" | "B" | "tie",
    "confidence": 0.0-1.0,
    "reasoning": "under 50 words",
    "information_lost": ["list any critical missing info"]
}"""


class JudgeVerdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    INCONCLUSIVE = "inconclusive"


class ClaudeJudge:
    """Position bias mitigation via mandatory order-swapping.
    Accept ONLY when BOTH orderings agree the optimization is safe.
    Uses X-Headroom-Skip so judge calls bypass Headroom compression.
    """

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def verify(self, original_output: str, optimized_output: str,
                     task_description: str = "") -> tuple[JudgeVerdict, str]:
        loop = asyncio.get_running_loop()
        result1, result2 = await asyncio.gather(
            loop.run_in_executor(None, self._compare_sync, original_output, optimized_output, task_description),
            loop.run_in_executor(None, self._compare_sync, optimized_output, original_output, task_description),
        )
        run1_safe = result1.get("winner") in ("B", "tie")
        run2_safe = result2.get("winner") in ("A", "tie")

        if run1_safe and run2_safe:
            return JudgeVerdict.SAFE, result1.get("reasoning", "")
        elif not run1_safe and not run2_safe:
            lost = result1.get("information_lost", [])
            return JudgeVerdict.UNSAFE, f"Information lost: {', '.join(lost)}"
        else:
            return JudgeVerdict.INCONCLUSIVE, "Orderings disagreed"

    def _compare_sync(self, output_a: str, output_b: str, task: str) -> dict:
        try:
            task_ctx = f"Task: {task}\n\n" if task else ""
            response = self.client.messages.create(
                model=self.model, max_tokens=300,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content":
                    f"{task_ctx}Response A:\n{output_a[:2000]}\n\nResponse B:\n{output_b[:2000]}"}],
                extra_headers={SKIP_HEADER: "true"},
            )
            text = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception:
            return {"winner": "tie", "confidence": 0.5, "reasoning": "error", "information_lost": []}
```

---

## Dependency Addition

```toml
eval = [
    "headroom-ai[proxy]>=0.1.0",
    "datasketch>=1.6.0",
    "xxhash>=3.4.0",
    "httpx>=0.27.0",
]
```

Install: `pip install -e ".[eval]"`

---

## cli.py change

Replace eval stub:
```python
from devkit.commands.eval import eval_app
app.add_typer(eval_app, name="eval")
```

---

## Open Decisions

1. **Headroom log field names** — validate `compressed_tokens` and `compression_ratio` against actual proxy.log output. Field names confirmed from source analysis but validate on first real session.

2. **X-Headroom-Skip header** — confirm this is the correct skip mechanism from headroom/proxy/middleware.py. If different, update in ablation.py and judge.py.

3. **Ablation vs Headroom** — Headroom handles easy compression (JSON bloat, verbose logs). Ablation catches structural waste (whole files pasted, unreferenced context). These are complementary not competing.

4. **Port conflict** — if 8787 is taken, auto-increment. Add `--port` to `devkit eval start` and propagate to setup instructions.

5. **`devkit eval versions` requires Slice 2** — fails gracefully if memory extras not installed.

6. **Process persistence on Windows** — test subprocess.Popen behavior when terminal closes. May need DETACHED_PROCESS flag on Windows.

---

## Portfolio Framing

"I evaluated Headroom (51K stars, Apache 2.0), understood its architecture by reading the source, and built the ablation verification and prompt regression layer it was missing — combining their compression engine with DevKit's per-chunk safety verification and cross-session memory."

That is a stronger FDE story than building a proxy from scratch.
