"""Offline, cache-aware context ablation (see docs/issues/02).

DevKit does not sit on the request path (Headroom does), so ablation is an
explicit offline job over a captured request:

    devkit eval ablate request.json          # an Anthropic Messages request body
    devkit eval ablate session.jsonl         # a Claude Code transcript (best effort)

For each candidate chunk (a user text block or a tool_result payload that sits
AFTER the last cache breakpoint), the request is replayed with that chunk
replaced by a short placeholder. The replay keeps `system` and `tools`, never
removes tool_use/tool_result blocks (that would 400), and never sends empty
content. A cheap lexical prefilter (Jaccard distance vs. a measured noise
baseline) discards clearly-changed outputs; survivors go to the order-swapped
judge, which is the actual decision maker.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from devkit.paths import SUGGESTIONS_DIR

# Headroom bypass header so replays are measured on uncompressed context.
SKIP_HEADER = "x-headroom-bypass"
ABLATION_MIN_CHUNK_TOKENS = 100
DEFAULT_MAX_CHUNKS = 8
REPLAY_MAX_TOKENS = 2048
PLACEHOLDER = "[content omitted]"
# Outputs whose distance from the original exceeds baseline noise by this much
# are treated as clearly changed and not sent to the judge.
PREFILTER_MARGIN = 0.35

# Request keys forwarded on replay. Sampling / thinking settings are dropped on
# purpose: the replay model may differ from the original and reject them.
_REPLAY_KEYS = ("system", "tools", "tool_choice")


def rough_tokens(text: str) -> int:
    return max(0, len(text) // 4)


@dataclass
class Chunk:
    message_idx: int
    block_idx: int          # -1 => message content is a plain string
    kind: str               # "text" | "tool_result"
    text: str

    @property
    def tokens(self) -> int:
        return rough_tokens(self.text)

    @property
    def location(self) -> str:
        return f"messages[{self.message_idx}].content[{self.block_idx}] ({self.kind})"


@dataclass
class AblationReport:
    chunks_considered: int = 0
    skipped_cached: int = 0
    skipped_small: int = 0
    api_calls: int = 0
    baseline_distance: float = 0.0
    candidates: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── request loading ──────────────────────────────────────────────────────────

def load_request(path: str | Path) -> tuple[dict, str]:
    """Return (request_body, fidelity). fidelity is 'request' or 'transcript'."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") and p.suffix != ".jsonl":
        body = json.loads(text)
        if "messages" not in body:
            raise ValueError("request JSON has no 'messages' field")
        return body, "request"
    return _body_from_transcript(text.splitlines()), "transcript"


def _block_to_text(block: dict) -> dict | None:
    """Flatten tool blocks to text so a transcript replays without tool schemas."""
    t = block.get("type")
    if t == "text":
        return {"type": "text", "text": block.get("text", "")} if block.get("text") else None
    if t in ("thinking", "redacted_thinking"):
        return None
    if t == "tool_use":
        return {"type": "text",
                "text": f"[tool call {block.get('name')}: {json.dumps(block.get('input', {}))[:2000]}]"}
    if t == "tool_result":
        return {"type": "text", "text": f"[tool result]\n{_tool_result_text(block)}"}
    return None


def _body_from_transcript(lines: list[str]) -> dict:
    """Rebuild the last request of a Claude Code transcript.

    The transcript line format is internal to Claude Code and unversioned, so
    this is defensive: unknown lines are skipped. system/tools are not in the
    transcript, so tool blocks are flattened to text (fidelity='transcript').
    """
    messages: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [
            b2 for b in (content or []) if isinstance(b, dict) and (b2 := _block_to_text(b))]
        if not blocks:
            continue
        if messages and messages[-1]["role"] == msg["role"]:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": msg["role"], "content": blocks})
    # The request we can replay ends with the last user turn.
    while messages and messages[-1]["role"] != "user":
        messages.pop()
    if not messages:
        raise ValueError("transcript contains no replayable user turn")
    return {"messages": messages}


# ── segmentation (pure) ──────────────────────────────────────────────────────

def cached_prefix_end(body: dict) -> tuple[int, int]:
    """Position (message_idx, block_idx) of the last cache breakpoint in messages.

    Everything at or before it is part of the cached prefix and is never ablated:
    changing it forces a 1.25x cache re-write that can exceed the savings.
    Top-level automatic caching moves the breakpoint to the last block, so the
    whole conversation is treated as cached. (-1, -1) means nothing is cached.
    """
    msgs = body.get("messages", [])
    if body.get("cache_control") and msgs:
        last = msgs[-1].get("content")
        return len(msgs) - 1, (len(last) - 1 if isinstance(last, list) else -1)
    pos = (-1, -1)
    for i, msg in enumerate(msgs):
        content = msg.get("content")
        if isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict) and block.get("cache_control"):
                    pos = (i, j)
    return pos


def _tool_result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def segment(body: dict) -> tuple[list[Chunk], int]:
    """Return (ablatable chunks, number skipped because they are in the cached prefix).

    Only user-side content is ablated (user text and tool_result payloads); the
    final user message is the task itself and is never removed.
    """
    msgs = body.get("messages", [])
    bp = cached_prefix_end(body)
    chunks: list[Chunk] = []
    skipped = 0
    last_idx = len(msgs) - 1
    for i, msg in enumerate(msgs):
        if msg.get("role") != "user" or i == last_idx:
            continue
        content = msg.get("content")
        items: list[tuple[int, str, str]] = []
        if isinstance(content, str):
            items.append((-1, "text", content))
        elif isinstance(content, list):
            for j, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    items.append((j, "text", block.get("text", "")))
                elif block.get("type") == "tool_result":
                    items.append((j, "tool_result", _tool_result_text(block)))
        for j, kind, text in items:
            if (i, max(j, 0)) <= bp:
                skipped += 1
                continue
            if text.strip():
                chunks.append(Chunk(i, j, kind, text))
    return chunks, skipped


def ablate(body: dict, chunk: Chunk) -> dict:
    """Copy of body with chunk's payload replaced by a placeholder (never empty)."""
    reduced = copy.deepcopy(body)
    msg = reduced["messages"][chunk.message_idx]
    if chunk.block_idx == -1:
        msg["content"] = PLACEHOLDER
        return reduced
    block = msg["content"][chunk.block_idx]
    if chunk.kind == "tool_result":
        block["content"] = PLACEHOLDER
    else:
        block["text"] = PLACEHOLDER
    return reduced


def strip_thinking(body: dict) -> dict:
    """Thinking blocks are bound to the model/conversation that produced them;
    an edited history can't replay them, so both arms of the experiment drop them."""
    out = copy.deepcopy(body)
    for msg in out.get("messages", []):
        if isinstance(msg.get("content"), list):
            msg["content"] = [b for b in msg["content"]
                              if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
            if not msg["content"]:
                msg["content"] = PLACEHOLDER
    return out


def task_text(body: dict) -> str:
    msgs = body.get("messages", [])
    if not msgs:
        return ""
    content = msgs[-1].get("content")
    if isinstance(content, str):
        return content[:2000]
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")[:2000]


def jaccard_distance(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa and not sb:
        return 0.0
    if not sa or not sb:
        return 1.0
    return 1.0 - len(sa & sb) / len(sa | sb)


def response_signature(response: Any) -> str:
    """Text plus tool calls: in Claude Code most turns *are* tool calls."""
    parts: list[str] = []
    for b in getattr(response, "content", []):
        t = getattr(b, "type", "")
        if t == "text":
            parts.append(b.text)
        elif t == "tool_use":
            parts.append(f"[tool_use {b.name} {json.dumps(getattr(b, 'input', {}), sort_keys=True)}]")
    return "\n".join(parts)


# ── runner ───────────────────────────────────────────────────────────────────

class AblationRunner:
    def __init__(
        self,
        client: Any = None,
        model: str | None = None,
        judge: Any = None,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        min_chunk_tokens: int = ABLATION_MIN_CHUNK_TOKENS,
        suggestions_dir: Path = SUGGESTIONS_DIR,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        if model is None:
            from devkit.config import Config
            model = Config().get("fast_model")
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.judge = judge
        self.max_chunks = max_chunks
        self.min_chunk_tokens = min_chunk_tokens
        self.suggestions_dir = suggestions_dir
        self._progress = on_progress or (lambda _m: None)

    def plan(self, body: dict) -> tuple[list[Chunk], AblationReport]:
        chunks, skipped_cached = segment(body)
        report = AblationReport(chunks_considered=len(chunks) + skipped_cached,
                                skipped_cached=skipped_cached)
        big = [c for c in chunks if c.tokens >= self.min_chunk_tokens]
        report.skipped_small = len(chunks) - len(big)
        big.sort(key=lambda c: c.tokens, reverse=True)  # biggest savings first
        return big[: self.max_chunks], report

    def replay(self, body: dict) -> str:
        kwargs = {k: body[k] for k in _REPLAY_KEYS if k in body}
        tc = kwargs.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") in ("any", "tool"):
            kwargs.pop("tool_choice")  # forced tool use 400s on current models
        response = self.client.messages.create(
            model=self.model,
            max_tokens=min(int(body.get("max_tokens", REPLAY_MAX_TOKENS)), REPLAY_MAX_TOKENS),
            messages=body["messages"],
            extra_headers={SKIP_HEADER: "true"},
            **kwargs,
        )
        return response_signature(response)

    def run(self, body: dict, source: str = "", fidelity: str = "request") -> AblationReport:
        body = strip_thinking(body)
        chunks, report = self.plan(body)
        if not chunks:
            return report
        task = task_text(body)
        try:
            original = self.replay(body)
            repeat = self.replay(body)
            report.api_calls += 2
        except Exception as exc:
            report.errors.append(f"original replay failed: {type(exc).__name__}: {exc}")
            return report
        if not original.strip():
            report.errors.append("original replay returned no output")
            return report
        report.baseline_distance = round(jaccard_distance(original, repeat), 3)
        cutoff = min(1.0, report.baseline_distance + PREFILTER_MARGIN)

        for chunk in chunks:
            self._progress(f"ablating {chunk.location} (~{chunk.tokens} tokens)")
            try:
                reduced = self.replay(ablate(body, chunk))
                report.api_calls += 1
            except Exception as exc:
                report.errors.append(f"{chunk.location}: {type(exc).__name__}: {exc}")
                continue
            distance = round(jaccard_distance(original, reduced), 3)
            if distance > cutoff:
                continue  # output clearly changed: chunk matters
            verdict = reasoning = None
            if self.judge is not None:
                result = self.judge.judge(original, reduced, task)
                report.api_calls += len(result.runs)
                verdict, reasoning = result.verdict.value, result.reasoning
            report.candidates.append(self._save(chunk, original, reduced, distance,
                                                report.baseline_distance, verdict, reasoning,
                                                task, source, fidelity))
        return report

    def _save(self, chunk: Chunk, original: str, reduced: str, distance: float,
              baseline: float, verdict: str | None, reasoning: str | None,
              task: str, source: str, fidelity: str) -> dict:
        self.suggestions_dir.mkdir(parents=True, exist_ok=True)
        cid = uuid.uuid4().hex[:8]
        data = {
            "id": cid,
            "source": source,
            "fidelity": fidelity,
            "location": chunk.location,
            "kind": chunk.kind,
            "chunk_preview": chunk.text[:200],
            "tokens_saved": chunk.tokens,
            "task": task,
            "original_output": original,
            "reduced_output": reduced,
            "jaccard_distance": distance,
            "baseline_distance": baseline,
            "replay_model": self.model,
            "verified": verdict is not None,
            "verdict": verdict,
            "verdict_reasoning": reasoning,
        }
        (self.suggestions_dir / f"candidate-{cid}.json").write_text(json.dumps(data, indent=2))
        return data
