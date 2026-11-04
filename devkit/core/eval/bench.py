"""Judge benchmark: measure the ablation judge before trusting it (JD: "evaluate
prompts and models using defined datasets, acceptance criteria, and automated checks").

Dataset (JSONL), one case per line:
  {"id": "...", "task": "...", "original": "...", "reduced": "...",
   "label": "equivalent" | "degraded"}

The metric that matters most is the FALSE-SAFE rate: a degraded output the judge
calls SAFE is a suggestion that would silently delete needed context.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "data" / "judge_bench.jsonl"


def load_dataset(path: str | Path = DEFAULT_DATASET) -> list[dict]:
    cases = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        missing = {"id", "original", "reduced", "label"} - set(case)
        if missing:
            raise ValueError(f"line {n}: missing {sorted(missing)}")
        if case["label"] not in ("equivalent", "degraded"):
            raise ValueError(f"line {n}: label must be 'equivalent' or 'degraded'")
        cases.append(case)
    return cases


@dataclass
class BenchReport:
    n: int = 0
    correct: int = 0
    false_safe: int = 0          # degraded judged SAFE  (dangerous)
    false_reject: int = 0        # equivalent judged not SAFE (missed savings)
    inconclusive: int = 0
    order_agreement: int = 0
    judge_calls: int = 0
    n_equivalent: int = 0
    n_degraded: int = 0
    rows: list[dict] = field(default_factory=list)

    def rate(self, x: int, d: int) -> float:
        return round(x / d, 3) if d else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "cases": self.n,
            "accuracy": self.rate(self.correct, self.n),
            "false_safe_rate": self.rate(self.false_safe, self.n_degraded),
            "false_reject_rate": self.rate(self.false_reject, self.n_equivalent),
            "inconclusive_rate": self.rate(self.inconclusive, self.n),
            "order_agreement_rate": self.rate(self.order_agreement, self.n),
            "judge_calls": self.judge_calls,
        }

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "rows": self.rows}


def run_bench(judge: Any, cases: list[dict]) -> BenchReport:
    from devkit.core.eval.judge import JudgeVerdict

    rep = BenchReport()
    for case in cases:
        result = judge.judge(case["original"], case["reduced"], case.get("task", ""))
        safe = result.verdict is JudgeVerdict.SAFE
        equivalent = case["label"] == "equivalent"
        rep.n += 1
        rep.judge_calls += len(result.runs)
        rep.n_equivalent += equivalent
        rep.n_degraded += not equivalent
        rep.inconclusive += result.verdict is JudgeVerdict.INCONCLUSIVE
        rep.order_agreement += bool(result.order_agreement)
        ok = safe == equivalent
        rep.correct += ok
        rep.false_safe += (safe and not equivalent)
        rep.false_reject += (equivalent and not safe)
        rep.rows.append({"id": case["id"], "label": case["label"], "verdict": result.verdict.value,
                         "correct": ok, "order_agreement": result.order_agreement,
                         "reasoning": result.reasoning[:200]})
    return rep


def save_report(rep: BenchReport, model: str, samples: int, dataset: str) -> Path:
    from datetime import datetime, timezone
    from devkit.paths import EVAL_DIR
    out_dir = EVAL_DIR / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"bench-{ts}.json"
    path.write_text(json.dumps({"model": model, "samples": samples, "dataset": dataset,
                                "created_at": ts, **rep.to_dict()}, indent=2))
    return path
