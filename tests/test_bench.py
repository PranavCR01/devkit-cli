"""JD feature: judge benchmark harness."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from devkit.core.eval.bench import DEFAULT_DATASET, load_dataset, run_bench
from devkit.core.eval.judge import ClaudeJudge


def test_bundled_dataset_is_valid_and_balanced():
    cases = load_dataset(DEFAULT_DATASET)
    labels = [c["label"] for c in cases]
    assert len(cases) >= 12 and labels.count("degraded") >= 5 and labels.count("equivalent") >= 5


def _verdict(w):
    return json.dumps({"reasoning": "r", "winner": w, "confidence": 1, "information_lost": []})


def test_rubber_stamp_judge_shows_full_false_safe_rate(fake_client_factory):
    """A judge that always says 'tie' looks great on equivalence and is caught by false-SAFE."""
    client = fake_client_factory(lambda kw: _verdict("tie"))
    rep = run_bench(ClaudeJudge(client=client, model="m"), load_dataset())
    s = rep.summary()
    assert s["false_safe_rate"] == 1.0 and s["false_reject_rate"] == 0.0


def test_position_biased_judge_is_inconclusive_everywhere(fake_client_factory):
    client = fake_client_factory(lambda kw: _verdict("A"))
    s = run_bench(ClaudeJudge(client=client, model="m"), load_dataset()).summary()
    assert s["inconclusive_rate"] == 1.0 and s["order_agreement_rate"] == 0.0


def test_oracle_judge_scores_perfectly(fake_client_factory):
    cases = {c["original"] + "|" + c["reduced"]: c for c in load_dataset()}
    def responder(kw):
        txt = kw["messages"][0]["content"]
        a = txt.split("<response_a>\n")[1].split("\n</response_a>")[0]
        b = txt.split("<response_b>\n")[1].split("\n</response_b>")[0]
        case = cases.get(a + "|" + b) or cases.get(b + "|" + a)
        if case["label"] == "equivalent":
            return _verdict("tie")
        return _verdict("A" if a == case["original"] else "B")
    s = run_bench(ClaudeJudge(client=fake_client_factory(responder), model="m"), load_dataset()).summary()
    assert s["accuracy"] == 1.0 and s["false_safe_rate"] == 0.0


def test_bench_cli_dry_run():
    from devkit.cli import app
    r = CliRunner().invoke(app, ["eval", "bench", "--dry-run"])
    assert r.exit_code == 0 and "judge calls" in r.output
