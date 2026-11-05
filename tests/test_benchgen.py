"""Judge benchmark v2: labels by construction, deterministic build, honest statistics."""
from __future__ import annotations

import copy
import json

import pytest
from typer.testing import CliRunner

from devkit.core.eval import bench, benchgen as bg


@pytest.fixture(scope="module")
def seeds():
    return bg.load_seeds()


@pytest.fixture(scope="module")
def cases(seeds):
    return bg.build(seeds)


# ── dataset shape ────────────────────────────────────────────────────────────

def test_committed_dataset_is_exactly_what_the_seeds_generate(cases):
    expected = "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in cases)
    assert bg.DATASET_PATH.read_text(encoding="utf-8") == expected, "run: devkit eval bench-build"


def test_build_is_deterministic(seeds):
    assert bg.build(copy.deepcopy(seeds)) == bg.build(copy.deepcopy(seeds))


def test_balanced_labels_and_splits(cases):
    s = bg.summary(cases)
    assert s["by_label"] == {"degraded": 60, "equivalent": 60}
    assert s["by_split"] == {"dev": 60, "test": 60}


def test_every_degradation_operator_is_covered(cases):
    counts = bg.summary(cases)["by_operator"]
    for op in bg.DEGRADE_OPS:
        assert counts.get(op, 0) >= 5, f"{op} has too few cases for a per-operator rate"


def test_split_is_by_seed_so_dev_tuning_cannot_leak_into_test(cases):
    split_of = {}
    for c in cases:
        assert split_of.setdefault(c["seed_id"], c["split"]) == c["split"]


def test_ids_unique_and_degraded_cases_explain_themselves(cases):
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["reduced"] != c["original"]
        if c["label"] == "degraded":
            assert c["why"], c["id"]


# ── labels by construction ───────────────────────────────────────────────────

def test_replace_operators_change_exactly_one_annotated_span(seeds):
    for s in seeds:
        original = bg.render(s)
        for m in s["mutations"]:
            if m["op"] in bg._REPLACE_OPS:
                out = bg.apply_mutation(s, m)
                assert out == original.replace(m["find"], m["replace"])
                assert m["find"] not in out or m["find"] in m["replace"]


def test_drop_step_removes_exactly_one_step(seeds):
    for s in seeds:
        for m in s["mutations"]:
            if m["op"] == "drop_step":
                out = bg.apply_mutation(s, m)
                assert s["steps"][m["index"]].split("`")[0] not in out or len(out) < len(bg.render(s))
                assert out == bg.render(s, steps=[x for i, x in enumerate(s["steps"]) if i != m["index"]])


def test_drop_tool_call_removes_one_call_line(seeds):
    for s in seeds:
        for m in s["mutations"]:
            if m["op"] == "drop_tool_call":
                out = bg.apply_mutation(s, m)
                assert m["find"] not in out
                assert out.count("[tool_use") == bg.render(s).count("[tool_use") - 1


def test_equivalent_variants_keep_every_substance_marker(seeds, cases):
    by_id = {s["id"]: s for s in seeds}
    for c in cases:
        if c["label"] != "equivalent":
            continue
        for marker in bg.substance_markers(by_id[c["seed_id"]]):
            assert marker in c["reduced"], (c["id"], marker)


def test_drop_rationale_only_removes_the_why_line(seeds):
    for s in seeds:
        if s.get("rationale"):
            out = bg.apply_equivalent(s, "drop_rationale", 0)
            assert "Why:" not in out and out in bg.render(s)


def test_paraphrases_are_tagged_for_separate_reporting(cases):
    para = [c for c in cases if c["operator"] == "llm_paraphrase"]
    assert para and all(c["source"] == "llm_paraphrase" for c in para)
    assert all(c["source"] == "constructed" for c in cases if c["operator"] != "llm_paraphrase")


# ── the validator refuses bad seeds ──────────────────────────────────────────

def _good():
    return {"id": "x", "split": "dev", "task": "t", "steps": ["Set `a = 1`", "Return `a`"],
            "mutations": [{"op": "change_constant", "find": "`a = 1`", "replace": "`a = 2`", "why": "w"},
                          {"op": "drop_step", "index": 1, "why": "w"}]}


@pytest.mark.parametrize("breakage, msg", [
    (lambda s: s["mutations"][0].update(find="`zzz`"), "occurs 0x"),
    (lambda s: s["steps"].append("Set `a = 1` again"), "occurs 2x"),
    (lambda s: s["mutations"][0].pop("why"), "needs a 'why'"),
    (lambda s: s["mutations"][0].update(replace="`a = 1`"), "different replace"),
    (lambda s: s.update(paraphrase="Return a."), "paraphrase drops"),
    (lambda s: s.update(rationale="because `a = 1`"), "occurs 2x|rationale contains"),
    (lambda s: s.update(split="train"), "split must be"),
    (lambda s: s["mutations"].pop(), ">= 2 mutations"),
])
def test_validator_rejects(breakage, msg):
    s = _good()
    bg.validate_seed(s)
    breakage(s)
    with pytest.raises(bg.SeedError, match=msg):
        bg.validate_seed(s)


# ── statistics ───────────────────────────────────────────────────────────────

def test_wilson_interval_known_values():
    lo, hi = bench.wilson(0, 30)
    assert lo == 0.0 and 0.11 <= hi <= 0.12           # "0 of 30" still allows ~11%
    assert bench.wilson(5, 10) == (0.237, 0.763)
    assert bench.wilson(0, 0) == (0.0, 1.0)


def _report(rows):
    r = bench.BenchReport()
    for label, op, correct in rows:
        r.rows.append({"label": label, "operator": op, "correct": correct, "source": "constructed"})
        r.n += 1
        r.n_degraded += label == "degraded"
        r.n_equivalent += label == "equivalent"
        r.false_safe += label == "degraded" and not correct
    return r


def test_by_operator_uses_worst_run_for_the_interval():
    r1 = _report([("degraded", "off_by_one", True), ("degraded", "off_by_one", False)])
    r2 = _report([("degraded", "off_by_one", False), ("degraded", "off_by_one", False)])
    row = bench.by_operator([r1, r2])[0]
    assert row["errors_per_run"] == [1, 2]
    assert row["error_rate_range"] == [0.5, 1.0]
    assert row["ci95_worst_run"] == list(bench.wilson(2, 2))


def test_split_filter_and_unknown_split():
    assert {c["split"] for c in bench.load_dataset(split="test")} == {"test"}
    with pytest.raises(ValueError):
        bench.load_dataset(split="nope")


def test_cli_bench_build_check_and_runs_dry_run():
    from devkit.cli import app
    r = CliRunner().invoke(app, ["eval", "bench-build", "--check"])
    assert r.exit_code == 0 and "matches" in r.output
    r = CliRunner().invoke(app, ["eval", "bench", "--dry-run", "--runs", "3", "--split", "test"])
    assert r.exit_code == 0 and "60 cases" in r.output and "360 judge calls" in r.output


def test_review_sheet_is_in_sync_with_seeds(seeds, cases):
    assert bg.REVIEW_PATH.read_text(encoding="utf-8") == bg.review_markdown(seeds, cases), \
        "run: devkit eval bench-build"
