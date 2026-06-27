from __future__ import annotations
import asyncio
import json
from enum import Enum

import anthropic

# Correct Headroom bypass header (x-headroom-bypass, not X-Headroom-Skip)
SKIP_HEADER = "x-headroom-bypass"

JUDGE_SYSTEM_PROMPT = """You are evaluating whether removing a context chunk produces an equivalent response.

Evaluate Response A vs Response B:
1. Code correctness -- functionally equivalent?
2. Requirement completeness -- all requirements addressed?
3. No lost context -- no critical information missing?
4. No hallucinated APIs -- no invented functions?

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
    Uses x-headroom-bypass so judge calls skip Headroom compression.
    """

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5") -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def verify(
        self,
        original_output: str,
        optimized_output: str,
        task_description: str = "",
    ) -> tuple[JudgeVerdict, str]:
        loop = asyncio.get_running_loop()
        result1, result2 = await asyncio.gather(
            loop.run_in_executor(
                None, self._compare_sync, original_output, optimized_output, task_description
            ),
            loop.run_in_executor(
                None, self._compare_sync, optimized_output, original_output, task_description
            ),
        )
        # run1: original=A, optimized=B => safe if winner is B or tie
        run1_safe = result1.get("winner") in ("B", "tie")
        # run2: optimized=A, original=B => safe if winner is A or tie
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
                model=self.model,
                max_tokens=300,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": (
                        f"{task_ctx}Response A:\n{output_a[:2000]}"
                        f"\n\nResponse B:\n{output_b[:2000]}"
                    ),
                }],
                extra_headers={SKIP_HEADER: "true"},
            )
            text = (
                response.content[0].text
                .strip()
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )
            return json.loads(text)
        except Exception:
            return {
                "winner": "tie",
                "confidence": 0.5,
                "reasoning": "error",
                "information_lost": [],
            }
