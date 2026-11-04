"""Issue 03: SessionStart hook and context assembly."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_hooks_json_has_required_wrapper_and_single_command():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert set(data) == {"hooks"}, "plugin hooks.json must wrap events in a top-level 'hooks' key"
    cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    from devkit.cli import HOOK_COMMAND
    assert cmd == HOOK_COMMAND


def test_plugin_manifest_schema():
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "devkit"
    assert isinstance(data["author"], dict) and data["author"]["name"]
    assert "components" not in data
    assert not (ROOT / ".claude-plugin" / "plugin-local.json").exists()


def test_register_hook_is_idempotent_and_preserves_settings(tmp_path):
    from devkit.cli import register_session_hook
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "echo other"}]}]}}))
    assert register_session_hook(settings) is True
    assert register_session_hook(settings) is False
    data = json.loads(settings.read_text())
    assert data["model"] == "x"
    cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "echo other" in cmds and sum("devkit context inject" in c for c in cmds) == 1


def test_init_register_hook_writes_claude_settings():
    from devkit.cli import app
    from devkit.paths import claude_home
    r = CliRunner().invoke(app, ["init", "--register-hook"])
    assert r.exit_code == 0, r.output
    assert "devkit context inject" in (claude_home() / "settings.json").read_text()


def _long_facts(backend, n=60):
    for i in range(n):
        backend.save(f"decision {i}: " + "we chose approach number %d because reasons " % i * 6,
                     "decision", "proj")


def test_inject_over_budget_still_injects_top_facts(backend):
    from devkit.core.context.injector import build_injection
    _long_facts(backend)
    out = build_injection("proj", token_cap=500, backend=backend, include_session_items=False)
    assert "[DECISION]" in out, "previously the whole snapshot was dropped when over budget"
    assert "[OMITTED]" in out


def test_inject_uses_hybrid_retrieval_to_rank(backend, monkeypatch):
    from devkit.core.context import injector
    backend.save("payment webhooks must verify stripe signatures", "decision", "proj")
    for i in range(30):
        backend.save(f"unrelated logging decision number {i} about log levels", "decision", "proj")
    monkeypatch.setattr(injector, "situation_query", lambda cwd, ws: "stripe payment webhooks")
    out = injector.build_injection("proj", token_cap=120, backend=backend, include_session_items=False)
    assert "stripe signatures" in out


def test_rank_facts_rrf_prefers_relevant_over_recent():
    from devkit.core.context.injector import rank_facts
    from devkit.core.memory.store import Fact
    def f(i, t="decision"):
        return Fact(str(i), "p", None, f"c{i}", t, f"2026-01-{i + 1:02d}", None, "", "manual", 1.0)
    facts = [f(i) for i in range(10)]
    ranked = rank_facts(facts, search_hits=["0"])  # oldest fact is the only relevant one
    assert ranked[0].id == "0"


def test_pinned_items_from_context_add_are_injected(backend):
    from devkit.core.context import injector
    from devkit.core.context.manifest import Manifest
    from devkit.core.fork.blueprint import Blueprint
    Blueprint.create("auth-pattern", "auth", "src", [{"id": "n1", "name": "login", "type": "function"}],
                     [], [], [{"fact_type": "decision", "content": "JWT in httpOnly cookie"}], {})
    Manifest().register_blueprint("auth-pattern", "src", Path("x"), 50)
    from devkit.cli import app
    r = CliRunner().invoke(app, ["context", "add", "blueprint:auth-pattern"])
    assert r.exit_code == 0, r.output
    assert "httpOnly cookie" in r.output, "blueprint content used to fail to load (read a dir as file)"
    out = injector.build_injection("proj", backend=backend)
    assert "httpOnly cookie" in out


def test_inject_hook_mode_reads_cwd_from_stdin_and_never_errors(tmp_path):
    from devkit.cli import app
    r = CliRunner().invoke(app, ["context", "inject", "--format", "hook"],
                           input=json.dumps({"cwd": str(tmp_path), "source": "startup"}))
    assert r.exit_code == 0 and r.output == ""
    r = CliRunner().invoke(app, ["context", "inject", "--format", "hook"], input="garbage{")
    assert r.exit_code == 0


def test_inject_output_respects_hook_char_limit(backend):
    from devkit.core.context.injector import HOOK_CHAR_LIMIT, build_injection
    _long_facts(backend, 200)
    out = build_injection("proj", token_cap=100000, backend=backend, include_session_items=False)
    assert len(out) <= HOOK_CHAR_LIMIT + 40
