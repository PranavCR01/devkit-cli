"""JD feature: multi-agent handoff ledger."""
from __future__ import annotations

import subprocess
import threading
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from devkit.core.handoff.ledger import Ledger, LedgerError, _now, branch_commits, create_worktree, \
    has_merge_conflict


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db")


def test_full_lifecycle_handoff_takeover_done(ledger):
    tid = ledger.create("Add retries to webhook sender", "p", criteria=["tests pass", "idempotent"])
    ledger.claim(tid, "agent-a")
    ledger.check(tid, 1, "agent-a")
    ledger.release(tid, "agent-a", "retry loop done; idempotency key still TODO in sender.py:40")
    t = ledger.claim(tid, "agent-b")
    assert t.owner == "agent-b"
    brief = ledger.brief(tid)
    assert "START HERE" in brief and "sender.py:40" in brief and "[x] #1" in brief and "[ ] #2" in brief
    with pytest.raises(LedgerError, match="Unchecked"):
        ledger.complete(tid, "agent-b")
    ledger.check(tid, 2, "agent-b")
    ledger.complete(tid, "agent-b", commits=["abc123"])
    s = ledger.stats("p")
    assert s["completed"] == 1 and s["handoffs"] == 1 and s["takeovers"] == 1
    kinds = [e["kind"] for e in ledger.details(tid)["events"]]
    assert kinds == ["created", "claimed", "criterion_checked", "handoff", "takeover",
                     "criterion_checked", "done"]


def test_cannot_claim_someone_elses_live_task(ledger):
    tid = ledger.create("t", "p")
    ledger.claim(tid, "a")
    with pytest.raises(LedgerError, match="claimed"):
        ledger.claim(tid, "b")
    with pytest.raises(LedgerError, match="not claimed by"):
        ledger.release(tid, "b", "hijack")


def test_concurrent_claims_have_one_winner(ledger):
    tid = ledger.create("race", "p")
    winners, errors = [], []
    def go(name):
        try:
            ledger.claim(tid, name)
            winners.append(name)
        except LedgerError:
            errors.append(name)
    threads = [threading.Thread(target=go, args=(f"agent-{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(winners) == 1 and len(errors) == 7


def test_dependencies_block_claims(ledger):
    a = ledger.create("schema migration", "p")
    b = ledger.create("api endpoint", "p", depends_on=[a])
    with pytest.raises(LedgerError, match="Blocked"):
        ledger.claim(b, "x")
    ledger.claim(a, "x")
    ledger.complete(a, "x")
    assert ledger.claim(b, "y").owner == "y"


def test_expired_lease_recovery(ledger):
    tid = ledger.create("t", "p")
    ledger.claim(tid, "crashy", lease_minutes=1)
    assert ledger.recover(now=_now()) == []
    rec = ledger.recover(now=_now() + timedelta(minutes=5))
    assert [t.id for t in rec] == [tid]
    assert ledger.claim(tid, "rescuer").owner == "rescuer"
    assert ledger.stats("p")["recoveries"] == 1


def test_expired_lease_can_be_taken_over_directly(ledger):
    tid = ledger.create("t", "p")
    ledger.claim(tid, "a", lease_minutes=1)
    t = ledger.claim(tid, "b", now=_now() + timedelta(minutes=5))
    assert t.owner == "b"


def test_release_requires_note(ledger):
    tid = ledger.create("t", "p")
    ledger.claim(tid, "a")
    with pytest.raises(LedgerError, match="handover note"):
        ledger.release(tid, "a", "  ")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


def test_worktree_commits_and_conflict_detection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    path, branch, base = create_worktree(repo, "abc123")
    assert base == "main" and branch == "devkit/abc123"
    (tmp_path / "repo-wt-abc123" / "f.txt").write_text("agent change\n")
    _git(path, "commit", "-qam", "agent work")
    assert len(branch_commits(repo, base, branch)) == 1
    conflict = has_merge_conflict(repo, base, branch)
    if conflict is None:
        pytest.skip("git < 2.38: merge-tree --write-tree unsupported")
    assert conflict is False
    (repo / "f.txt").write_text("main change\n")
    _git(repo, "commit", "-qam", "main moved")
    assert has_merge_conflict(repo, base, branch) is True


def test_conflict_blocks_completion_and_is_counted(ledger):
    tid = ledger.create("t", "p")
    ledger.claim(tid, "a")
    with pytest.raises(LedgerError, match="conflicts"):
        ledger.complete(tid, "a", conflict=True)
    assert ledger.stats("p")["integration_conflicts"] == 1


def test_cli_roundtrip(monkeypatch):
    from devkit.cli import app
    monkeypatch.setenv("DEVKIT_AGENT", "agent-a")
    run = CliRunner().invoke
    r = run(app, ["handoff", "create", "Fix flaky test", "-a", "test passes 10x", "-p", "demo"])
    assert r.exit_code == 0, r.output
    tid = r.output.split("Task ")[1].split()[0]
    r = run(app, ["handoff", "claim", tid])
    assert r.exit_code == 0 and "<devkit-task" in r.output
    assert run(app, ["handoff", "release", tid, "-m", "root cause: shared tmp dir"]).exit_code == 0
    monkeypatch.setenv("DEVKIT_AGENT", "agent-b")
    r = run(app, ["handoff", "claim", tid])
    assert "shared tmp dir" in r.output
    assert run(app, ["handoff", "check", tid, "1"]).exit_code == 0
    assert run(app, ["handoff", "done", tid]).exit_code == 0
    r = run(app, ["handoff", "stats", "-p", "demo"])
    assert "handoffs                 1" in r.output
