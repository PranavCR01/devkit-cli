"""Issue 02: ablation was never invoked; judge failed open."""
from __future__ import annotations

import json

import pytest

from devkit.core.eval import ablation as ab
from devkit.core.eval.judge import ClaudeJudge, JudgeVerdict


def verdict_json(winner, lost=()):
    return json.dumps({"reasoning": "r", "winner": winner, "confidence": 0.9,
                       "information_lost": list(lost)})


# ── judge ───────────────────────────────────────────────────────────────────

def test_judge_errors_are_inconclusive_not_safe(fake_client_factory):
    client = fake_client_factory(lambda kw: RuntimeError("503 overloaded"))
    result = ClaudeJudge(client=client, model="m").judge("orig", "reduced", "task")
    assert result.verdict is JudgeVerdict.INCONCLUSIVE


def test_judge_unparseable_output_is_inconclusive(fake_client_factory):
    client = fake_client_factory(lambda kw: "not json at all")
    assert ClaudeJudge(client=client, model="m").judge("a", "b").verdict is JudgeVerdict.INCONCLUSIVE


def test_judge_safe_requires_both_orderings(fake_client_factory):
    client = fake_client_factory(lambda kw: verdict_json("tie"))
    r = ClaudeJudge(client=client, model="m").judge("a", "b", "t")
    assert r.verdict is JudgeVerdict.SAFE and r.order_agreement
    assert len(client.messages.calls) == 2


def test_judge_position_bias_is_inconclusive(fake_client_factory):
    # Judge always prefers whatever is shown first -> orderings disagree.
    client = fake_client_factory(lambda kw: verdict_json("A"))
    assert ClaudeJudge(client=client, model="m").judge("a", "b").verdict is JudgeVerdict.INCONCLUSIVE


def test_judge_original_wins_both_is_unsafe(fake_client_factory):
    def responder(kw):
        content = kw["messages"][0]["content"]
        a = content.split("<response_a>\n")[1].split("\n</response_a>")[0]
        return verdict_json("A" if a == "ORIG" else "B", ["the migration step"])
    client = fake_client_factory(responder)
    r = ClaudeJudge(client=client, model="m").judge("ORIG", "RED")
    assert r.verdict is JudgeVerdict.UNSAFE and "migration step" in r.reasoning


def test_judge_tie_with_lost_info_is_not_safe(fake_client_factory):
    client = fake_client_factory(lambda kw: verdict_json("tie", ["port number"]))
    assert ClaudeJudge(client=client, model="m").judge("a", "b").verdict is not JudgeVerdict.SAFE


def test_judge_sends_task_and_full_output(fake_client_factory):
    client = fake_client_factory(lambda kw: verdict_json("tie"))
    long = "x" * 3000
    ClaudeJudge(client=client, model="m").judge(long, long, "fix the login bug")
    content = client.messages.calls[0]["messages"][0]["content"]
    assert "fix the login bug" in content and long in content
    assert client.messages.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_judge_samples_multiply_calls(fake_client_factory):
    client = fake_client_factory(lambda kw: verdict_json("tie"))
    ClaudeJudge(client=client, model="m", samples=3).judge("a", "b")
    assert len(client.messages.calls) == 6


# ── ablation segmentation ───────────────────────────────────────────────────

BIG = "lorem ipsum dolor " * 60  # ~1000 chars, > 100 tokens


def claude_code_like_body(cache_at_msg=None):
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "earlier context " + BIG}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "file dump " + BIG}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {"role": "user", "content": [{"type": "text", "text": "now fix the bug"}]},
    ]
    if cache_at_msg is not None:
        msgs[cache_at_msg]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return {"model": "claude-opus-5-5", "max_tokens": 32000, "system": "SYS",
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
            "thinking": {"type": "adaptive"}, "messages": msgs}


def test_segment_skips_cached_prefix_and_final_task():
    chunks, skipped = ab.segment(claude_code_like_body(cache_at_msg=0))
    assert skipped == 1
    assert [(c.message_idx, c.kind) for c in chunks] == [(2, "tool_result")]


def test_segment_respects_top_level_auto_caching():
    body = claude_code_like_body()
    body["cache_control"] = {"type": "ephemeral"}
    chunks, skipped = ab.segment(body)
    assert chunks == [] and skipped == 2


def test_ablate_keeps_tool_pairing_and_never_sends_empty():
    body = claude_code_like_body()
    chunks, _ = ab.segment(body)
    tool_chunk = next(c for c in chunks if c.kind == "tool_result")
    reduced = ab.ablate(body, tool_chunk)
    block = reduced["messages"][2]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "tu1"
    assert block["content"] == ab.PLACEHOLDER
    assert body["messages"][2]["content"][0]["content"].startswith("file dump")  # original untouched


def test_replay_preserves_system_and_tools_and_drops_thinking(fake_client_factory):
    client = fake_client_factory(lambda kw: "same answer every time")
    runner = ab.AblationRunner(client=client, model="claude-haiku-5-5")
    runner.run(claude_code_like_body())
    first = client.messages.calls[0]
    assert first["system"] == "SYS" and first["tools"][0]["name"] == "Read"
    assert "thinking" not in first
    assert first["max_tokens"] <= ab.REPLAY_MAX_TOKENS
    for call in client.messages.calls:
        for m in call["messages"]:
            assert m["content"], "empty content would 400"
            if isinstance(m["content"], list):
                assert all(b.get("type") != "thinking" for b in m["content"])


def test_run_end_to_end_saves_judged_candidates(fake_client_factory, clean_home):
    def responder(kw):
        if "output_config" in kw:  # judge call
            return verdict_json("tie")
        return "patched login.py by adding a null check"
    client = fake_client_factory(responder)
    judge = ClaudeJudge(client=client, model="m")
    runner = ab.AblationRunner(client=client, model="m", judge=judge)
    rep = runner.run(claude_code_like_body(), source="req.json")
    assert rep.candidates and all(c["verdict"] == "safe" for c in rep.candidates)
    saved = list(ab.SUGGESTIONS_DIR.glob("candidate-*.json"))
    assert len(saved) == len(rep.candidates)
    data = json.loads(saved[0].read_text())
    assert data["task"] == "now fix the bug" and data["original_output"].startswith("patched")


def test_run_skips_chunks_whose_removal_changes_output(fake_client_factory):
    def responder(kw):
        dump = json.dumps(kw["messages"])
        return "uses file dump details" if "file dump" in dump else "totally different unrelated reply"
    client = fake_client_factory(responder)
    rep = ab.AblationRunner(client=client, model="m").run(claude_code_like_body())
    assert all("tool_result" not in c["location"] for c in rep.candidates)


def test_run_reports_replay_failure_instead_of_fake_candidates(fake_client_factory):
    client = fake_client_factory(lambda kw: RuntimeError("400 bad request"))
    rep = ab.AblationRunner(client=client, model="m").run(claude_code_like_body())
    assert rep.candidates == [] and rep.errors


def test_transcript_loader_flattens_tools(tmp_path):
    lines = [
        {"type": "user", "message": {"role": "user", "content": "please read a.py"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Read", "input": {"file": "a.py"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "print(1)"}]}},
        {"type": "summary", "summary": "ignored"},
        "not json",
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
    body, fidelity = ab.load_request(p)
    assert fidelity == "transcript"
    flat = json.dumps(body)
    assert "tool_use" not in flat and "[tool call Read" in flat and "print(1)" in flat
    assert body["messages"][-1]["role"] == "user"


def test_eval_cli_ablate_dry_run_needs_no_key(tmp_path):
    from typer.testing import CliRunner
    from devkit.cli import app
    p = tmp_path / "req.json"
    p.write_text(json.dumps(claude_code_like_body()))
    r = CliRunner().invoke(app, ["eval", "ablate", str(p), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "planned" in r.output and "Max API calls" in r.output


def test_eval_learn_does_not_use_python_m():
    from pathlib import Path
    src = (Path(ab.__file__).parents[2] / "commands" / "eval_cmd.py").read_text()
    assert '"-m", "headroom"' not in src


def test_eval_uses_configured_fast_model(fake_client_factory):
    from devkit.config import Config
    Config().set("fast_model", "claude-haiku-5-5")
    client = fake_client_factory(lambda kw: verdict_json("tie"))
    ClaudeJudge(client=client).judge("a", "b")
    assert client.messages.calls[0]["model"] == "claude-haiku-5-5"
    from pathlib import Path
    src = "".join(p.read_text() for p in Path(ab.__file__).parent.glob("*.py"))
    assert "claude-haiku-4-5" not in src
