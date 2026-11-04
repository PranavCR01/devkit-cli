"""Claude-as-judge for ablation candidates.

Design (see docs/issues/02):
- Pairwise comparison, run in BOTH orderings (position-bias mitigation).
- `samples` repetitions per ordering; SAFE only if every run says the reduced
  output is non-inferior AND no run reports lost information.
- Fails CLOSED: any API/parse error makes the verdict INCONCLUSIVE, never SAFE.
- Structured output (json_schema) so the verdict is machine-checkable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Headroom bypass header so judge calls are not themselves compressed.
SKIP_HEADER = "x-headroom-bypass"

JUDGE_SYSTEM_PROMPT = """You compare two responses produced for the same task. One was produced with the
full context and one with part of the context removed; you are not told which is which.

Judge only whether the responses are equivalent for the task:
1. Correctness: same functional behavior (same code changes / same tool calls / same facts)?
2. Completeness: every requirement addressed in one is addressed in the other?
3. Lost context: does either miss information the other relied on?
4. Hallucination: does either invent APIs, files, or facts?

Prefer "tie" when they are equivalent in substance even if worded differently.
Pick a winner only when one is materially better. List concrete missing items in
information_lost (empty list if none)."""

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "confidence": {"type": "number"},
        "information_lost": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reasoning", "winner", "confidence", "information_lost"],
    "additionalProperties": False,
}

MAX_OUTPUT_CHARS = 8000


class JudgeVerdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    INCONCLUSIVE = "inconclusive"


@dataclass
class JudgeRun:
    ordering: str            # "original_first" | "reduced_first"
    winner: str | None       # raw winner label, None on error
    reduced_ok: bool | None  # reduced output judged non-inferior; None on error
    information_lost: list[str] = field(default_factory=list)
    reasoning: str = ""
    error: str | None = None


@dataclass
class JudgeResult:
    verdict: JudgeVerdict
    reasoning: str
    runs: list[JudgeRun]

    @property
    def order_agreement(self) -> bool:
        oks = {r.reduced_ok for r in self.runs if r.reduced_ok is not None}
        return len(oks) == 1 and all(r.error is None for r in self.runs)


def _response_text(response: Any) -> str:
    parts = [getattr(b, "text", "") for b in getattr(response, "content", [])
             if getattr(b, "type", "") == "text"]
    return "".join(parts)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + "\n...[middle elided for judging]...\n" + text[-half:]


class ClaudeJudge:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
        samples: int = 1,
    ) -> None:
        if model is None:
            from devkit.config import Config
            model = Config().get("fast_model")
        self.model = model
        self.samples = max(1, samples)
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client

    # Kept async for backwards compatibility with callers using asyncio.run().
    async def verify(
        self,
        original_output: str,
        optimized_output: str,
        task_description: str = "",
    ) -> tuple[JudgeVerdict, str]:
        result = self.judge(original_output, optimized_output, task_description)
        return result.verdict, result.reasoning

    def judge(self, original_output: str, reduced_output: str, task: str = "") -> JudgeResult:
        if not original_output.strip() or not reduced_output.strip():
            return JudgeResult(JudgeVerdict.INCONCLUSIVE, "Empty output; cannot judge.", [])

        runs: list[JudgeRun] = []
        for _ in range(self.samples):
            # original=A, reduced=B -> reduced ok if winner in (B, tie)
            runs.append(self._run(original_output, reduced_output, task, "original_first", ("B", "tie")))
            # reduced=A, original=B -> reduced ok if winner in (A, tie)
            runs.append(self._run(reduced_output, original_output, task, "reduced_first", ("A", "tie")))
        return self._aggregate(runs)

    @staticmethod
    def _aggregate(runs: list[JudgeRun]) -> JudgeResult:
        errors = [r for r in runs if r.error]
        if errors:
            return JudgeResult(JudgeVerdict.INCONCLUSIVE,
                               f"{len(errors)}/{len(runs)} judge call(s) failed: {errors[0].error}", runs)
        lost = sorted({item for r in runs for item in r.information_lost if item.strip()})
        if all(r.reduced_ok for r in runs) and not lost:
            return JudgeResult(JudgeVerdict.SAFE, runs[0].reasoning, runs)
        if not any(r.reduced_ok for r in runs):
            detail = ", ".join(lost) if lost else runs[0].reasoning
            return JudgeResult(JudgeVerdict.UNSAFE, f"Information lost: {detail}", runs)
        if lost and all(r.reduced_ok for r in runs):
            return JudgeResult(JudgeVerdict.INCONCLUSIVE,
                               f"Judged equivalent but reported lost information: {', '.join(lost)}", runs)
        return JudgeResult(JudgeVerdict.INCONCLUSIVE, "Orderings disagreed (position bias).", runs)

    def _run(self, a: str, b: str, task: str, ordering: str, ok_labels: tuple[str, str]) -> JudgeRun:
        try:
            data = self._compare(a, b, task)
            winner = data.get("winner")
            if winner not in ("A", "B", "tie"):
                raise ValueError(f"invalid winner {winner!r}")
            lost = data.get("information_lost") or []
            if not isinstance(lost, list):
                lost = [str(lost)]
            return JudgeRun(ordering, winner, winner in ok_labels,
                            [str(x) for x in lost], str(data.get("reasoning", "")))
        except Exception as exc:  # fail closed
            return JudgeRun(ordering, None, None, error=f"{type(exc).__name__}: {exc}")

    def _compare(self, output_a: str, output_b: str, task: str) -> dict:
        task_ctx = f"<task>\n{task}\n</task>\n\n" if task else ""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=[{"type": "text", "text": JUDGE_SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": (
                    f"{task_ctx}<response_a>\n{_clip(output_a)}\n</response_a>\n\n"
                    f"<response_b>\n{_clip(output_b)}\n</response_b>"
                ),
            }],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            extra_headers={SKIP_HEADER: "true"},
        )
        if getattr(response, "stop_reason", None) in ("max_tokens", "refusal"):
            raise RuntimeError(f"judge stopped: {response.stop_reason}")
        text = _response_text(response).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)
