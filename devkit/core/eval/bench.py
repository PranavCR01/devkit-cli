"""Judge benchmark: measure the ablation judge before trusting it.

Dataset (JSONL), one case per line:
  {"id", "task", "original", "reduced", "label": "equivalent" | "degraded",
   optional: "operator", "split", "source", "seed_id", "why"}

The main dataset (judge_bench_v2.jsonl) is generated from annotated seeds by
devkit/core/eval/benchgen.py: degraded labels hold by construction. See
docs/eval/judge-bench.md for the method and its limits.

The metric that matters most is the FALSE-SAFE rate: a degraded output the
judge calls SAFE is a suggestion that would silently delete needed context.
Rates come with 95% Wilson intervals because n per operator is small.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_DATASET = DATA_DIR / "judge_bench_v2.jsonl"
SMOKE_DATASET = DATA_DIR / "judge_bench.jsonl"


def load_dataset(path: str | Path = DEFAULT_DATASET, split: str | None = None) -> list[dict]:
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
        case.setdefault("operator", "unspecified")
        case.setdefault("source", "constructed")
        case.setdefault("split", "all")
        cases.append(case)
    if split and split != "all":
        cases = [c for c in cases if c["split"] == split]
        if not cases:
            raise ValueError(f"no cases in split {split!r}")
    return cases


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials (sane at k=0 and small n)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


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

    @staticmethod
    def rate(x: int, d: int) -> float:
        return round(x / d, 3) if d else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "cases": self.n,
            "accuracy": self.rate(self.correct, self.n),
            "false_safe_rate": self.rate(self.false_safe, self.n_degraded),
            "false_safe_ci95": list(wilson(self.false_safe, self.n_degraded)),
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
        rep.rows.append({"id": case["id"], "label": case["label"], "operator": case.get("operator"),
                         "source": case.get("source"), "split": case.get("split"),
                         "verdict": result.verdict.value, "correct": ok,
                         "order_agreement": result.order_agreement,
                         "reasoning": result.reasoning[:300]})
    return rep


def by_operator(reports: list[BenchReport]) -> list[dict]:
    """Per-operator error counts across runs.

    For degraded operators the error is a false SAFE; for equivalence operators
    it is a false reject. The CI uses the WORST run (most errors): conservative,
    because repeated runs over the same cases are not independent samples.
    """
    groups: dict[tuple[str, str], list[list[bool]]] = defaultdict(lambda: [[] for _ in reports])
    for ri, rep in enumerate(reports):
        for row in rep.rows:
            groups[(row["label"], row["operator"])][ri].append(not row["correct"])
    out = []
    for (label, op), runs in sorted(groups.items()):
        n = len(runs[0])
        errors = [sum(r) for r in runs]
        worst = max(errors)
        out.append({"label": label, "operator": op, "n": n, "errors_per_run": errors,
                     "error_rate_range": [round(min(errors) / n, 3), round(worst / n, 3)],
                     "ci95_worst_run": list(wilson(worst, n))})
    return out


def aggregate(reports: list[BenchReport]) -> dict:
    sums = [r.summary() for r in reports]
    keys = ("accuracy", "false_safe_rate", "false_reject_rate", "inconclusive_rate", "order_agreement_rate")
    worst_fs = max(r.false_safe for r in reports)
    n_deg = reports[0].n_degraded
    para = [[row for row in r.rows if row["source"] == "llm_paraphrase"] for r in reports]
    return {
        "runs": len(reports),
        "cases": reports[0].n,
        "ranges": {k: [min(s[k] for s in sums), max(s[k] for s in sums)] for k in keys},
        "false_safe_counts": [r.false_safe for r in reports],
        "false_safe_ci95_worst_run": list(wilson(worst_fs, n_deg)),
        "llm_paraphrase_false_reject": [sum(not x["correct"] for x in p) for p in para],
        "llm_paraphrase_n": len(para[0]),
        "judge_calls": sum(r.judge_calls for r in reports),
        "by_operator": by_operator(reports),
    }


def save_report(payload: dict, model: str, samples: int, dataset: str) -> Path:
    from datetime import datetime, timezone
    from devkit.paths import EVAL_DIR
    out_dir = EVAL_DIR / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"bench-{ts}.json"
    path.write_text(json.dumps({"model": model, "samples": samples, "dataset": dataset,
                                "created_at": ts, **payload}, indent=2))
    return path
