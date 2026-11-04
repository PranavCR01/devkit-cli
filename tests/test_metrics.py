"""JD feature: PR throughput + AI cost per merged PR."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from devkit.core.metrics import throughput as tp


def _usage_line(mid, branch, ts, model="claude-sonnet-5-5", inp=1_000_000, out=0, cw=0, cr=0,
                session="s1", cwd="/w/proj"):
    return json.dumps({"type": "assistant", "timestamp": ts, "gitBranch": branch, "sessionId": session,
                       "cwd": cwd, "message": {"id": mid, "role": "assistant", "model": model,
                       "usage": {"input_tokens": inp, "output_tokens": out,
                                 "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}})


def _write(tmp_path, lines):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines))
    return p


PRS = [
    {"number": 1, "title": "feat a", "headRefName": "feat/a", "createdAt": "2026-10-05T10:00:00Z",
     "mergedAt": "2026-10-06T10:00:00Z", "state": "MERGED"},
    {"number": 2, "title": "feat b", "headRefName": "feat/b", "createdAt": "2026-10-06T10:00:00Z",
     "mergedAt": None, "state": "OPEN"},
]


def test_cost_math_and_cache_pricing(tmp_path):
    p = _write(tmp_path, [_usage_line("m1", "feat/a", "2026-10-05T11:00:00Z",
                                      inp=1_000_000, out=1_000_000, cw=1_000_000, cr=1_000_000)])
    recs, _ = tp.load_usage([p])
    # sonnet-5-5: 2 in + 10 out + 2*1.25 write + 0.20 read
    assert abs(recs[0].cost_usd - (2 + 10 + 2.5 + 0.2)) < 1e-9


def test_streamed_duplicate_message_ids_counted_once(tmp_path):
    line = _usage_line("dup", "feat/a", "2026-10-05T11:00:00Z")
    recs, _ = tp.load_usage([_write(tmp_path, [line, line, "garbage", '{"type":"user"}'])])
    assert len(recs) == 1


def test_cost_per_merged_pr_and_attribution(tmp_path):
    p = _write(tmp_path, [
        _usage_line("m1", "feat/a", "2026-10-05T11:00:00Z"),               # $2 -> PR 1
        _usage_line("m2", "feat/b", "2026-10-06T11:00:00Z"),               # $2 -> PR 2 (open)
        _usage_line("m3", "main", "2026-10-06T12:00:00Z", session="s2"),   # $2 unattributed
    ])
    usage, _ = tp.load_usage([p])
    rep = tp.compute(tp.load_prs_from_json(PRS), usage, period="week")
    row = rep["rows"][0]
    assert row["prs_opened"] == 2 and row["prs_merged"] == 1
    assert row["median_cycle_hours"] == 24.0
    assert row["ai_cost_per_merged_pr"] == 2.0 and row["sessions"] == 2
    t = rep["totals"]
    assert t["ai_cost_usd"] == 6.0 and t["ai_cost_unattributed"] == 2.0
    assert t["attribution_rate"] == round(4 / 6, 3)
    assert rep["top_prs_by_cost"][0]["number"] == 1


def test_unknown_model_is_reported_not_silently_free(tmp_path):
    usage, _ = tp.load_usage([_write(tmp_path, [_usage_line("x", "feat/a", "2026-10-05T11:00:00Z",
                                                            model="mystery-model")])])
    rep = tp.compute([], usage)
    assert rep["totals"]["unpriced_tokens"] == 1_000_000


def test_monthly_periods_and_since_filter(tmp_path):
    prs = tp.load_prs_from_json(PRS + [{"number": 3, "title": "old", "headRefName": "old",
                                        "createdAt": "2026-01-01T00:00:00Z",
                                        "mergedAt": "2026-01-02T00:00:00Z"}])
    rep = tp.compute(prs, [], period="month", since=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert [r["period"] for r in rep["rows"]] == ["2026-10"]


def test_metrics_cli_markdown(tmp_path):
    from devkit.cli import app
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps(PRS))
    tr = _write(tmp_path, [_usage_line("m1", "feat/a", "2026-10-05T11:00:00Z")])
    r = CliRunner().invoke(app, ["metrics", "--prs", str(prs), "--transcripts", str(tr),
                                 "--since", "2026-09-01", "--output", "md"])
    assert r.exit_code == 0, r.output
    assert "| 2026-W41 | 2 | 1 |" in r.output and "$2.00" in r.output


def test_project_filter(tmp_path):
    p = _write(tmp_path, [_usage_line("a", "feat/a", "2026-10-05T11:00:00Z", cwd="/w/proj"),
                          _usage_line("b", "feat/a", "2026-10-05T11:00:00Z", cwd="/w/other")])
    usage, _ = tp.load_usage([p], project_filter="proj")
    assert len(usage) == 1
