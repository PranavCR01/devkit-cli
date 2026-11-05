"""Issue 05: scan pipeline grading, memory self-match, config, ASCII output."""
from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from devkit.core.scanner.orchestrator import ScanOrchestrator


class _B:
    def __init__(self, text, type="text"):
        self.type, self.text = type, text


class _R:
    def __init__(self, text, stop="end_turn"):
        self.content = [_B("", "thinking"), _B(text)]
        self.stop_reason = stop


class FakeAsyncMessages:
    def __init__(self, fn):
        self.fn, self.calls, self.active, self.peak = fn, 0, 0, 0

    async def create(self, **kw):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        out = self.fn(kw)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, _R) else _R(out)


class FakeAsyncClient:
    def __init__(self, fn):
        self.messages = FakeAsyncMessages(fn)


def _project(tmp_path, n_files=3, lines=10):
    for i in range(n_files):
        (tmp_path / f"mod{i}.py").write_text("\n".join(f"x{j} = {j}" for j in range(lines)))
    return tmp_path


def _run(orch, path, **kw):
    return asyncio.run(orch.run(str(path), use_semgrep=False, **kw))


def test_all_claude_chunks_failing_is_incomplete_not_grade_a(tmp_path):
    client = FakeAsyncClient(lambda kw: RuntimeError("401 invalid x-api-key"))
    res = _run(ScanOrchestrator(api_key="k", claude_client=client), _project(tmp_path))
    assert res.incomplete and res.display_grade == "INCOMPLETE"
    assert any("failed" in w for w in res.warnings)


@pytest.mark.parametrize("reply", ["[{\"severity\": \"high\"}]x", "not json", _R("{}", stop="max_tokens")])
def test_malformed_or_truncated_output_counts_as_failure(tmp_path, reply):
    client = FakeAsyncClient(lambda kw: reply)
    res = _run(ScanOrchestrator(api_key="k", claude_client=client), _project(tmp_path))
    assert res.incomplete


def test_list_shaped_json_is_accepted(tmp_path):
    finding = {"category": "security", "severity": "high", "title": "t", "file_path": "mod0.py",
               "line_start": 2}
    client = FakeAsyncClient(lambda kw: json.dumps([finding]))
    res = _run(ScanOrchestrator(api_key="k", claude_client=client), _project(tmp_path))
    assert not res.incomplete and res.findings[0].title == "t"


def test_concurrency_is_bounded(tmp_path):
    client = FakeAsyncClient(lambda kw: '{"findings": []}')
    proj = _project(tmp_path, n_files=12, lines=1500)
    _run(ScanOrchestrator(api_key="k", claude_client=client, max_concurrency=2), proj)
    assert client.messages.calls > 2 and client.messages.peak <= 2


def test_semgrep_missing_marks_scan_incomplete(tmp_path, monkeypatch):
    import devkit.core.scanner.semgrep_runner as sr
    def boom(*a, **k):
        raise RuntimeError("semgrep not found on PATH")
    monkeypatch.setattr(sr.SemgrepRunner, "run", boom)
    res = asyncio.run(ScanOrchestrator(api_key="k").run(str(_project(tmp_path)), use_claude=False))
    assert res.incomplete and "Semgrep" in res.warnings[0]


def test_first_scan_does_not_match_its_own_findings(tmp_path, backend):
    finding = {"category": "security", "severity": "critical", "title": "SQL injection in login",
               "file_path": "mod0.py", "line_start": 3, "plain_english_desc": "d", "fix_snippet": "f"}
    client = FakeAsyncClient(lambda kw: json.dumps({"findings": [finding]}))
    orch = ScanOrchestrator(api_key="k", claude_client=client, memory_store=backend,
                            auto_learn=True, project_name="myproj")
    first = _run(orch, _project(tmp_path))
    assert first.findings[0].memory_match is None, "new finding matched itself"
    facts = backend.list_facts(project="myproj", fact_type="vulnerability_pattern")
    assert len(facts) == 1, "facts must be stored under the project name, not the absolute path"
    second = _run(ScanOrchestrator(api_key="k", claude_client=client, memory_store=backend,
                                   auto_learn=True, project_name="myproj"), tmp_path)
    assert second.findings[0].memory_match is not None
    assert len(backend.list_facts(project="myproj", fact_type="vulnerability_pattern")) == 1


def test_scan_no_claude_works_without_api_key(tmp_path, monkeypatch):
    import devkit.core.scanner.semgrep_runner as sr
    monkeypatch.setattr(sr.SemgrepRunner, "run", lambda self, p, m, t=120: [])
    from devkit.cli import app
    r = CliRunner().invoke(app, ["scan", str(_project(tmp_path)), "--no-claude"])
    assert r.exit_code == 0, r.output
    assert "Missing config" not in r.output


def test_scan_output_is_ascii(tmp_path, monkeypatch):
    import devkit.core.scanner.semgrep_runner as sr
    finding = {"category": "security", "severity": "high", "title": "Hardcoded key",
               "plain_english_desc": "d", "fix_snippet": "f", "file_path": "mod0.py",
               "line_start": 1, "line_end": 1, "owasp_ref": None, "cwe_ref": "CWE-798",
               "source": "semgrep"}
    monkeypatch.setattr(sr.SemgrepRunner, "run", lambda self, p, m, t=120: [finding])
    from devkit.cli import app
    r = CliRunner().invoke(app, ["scan", str(_project(tmp_path)), "--no-claude"])
    assert r.exit_code == 0, r.output
    assert r.output.isascii(), [c for c in r.output if not c.isascii()]
    assert "Hardcoded key" in r.output


def test_scan_honors_config(tmp_path, monkeypatch):
    from devkit.config import Config
    seen = {}
    import devkit.core.scanner.semgrep_runner as sr
    def fake_run(self, p, m, timeout=120):
        seen["timeout"] = timeout
        return []
    monkeypatch.setattr(sr.SemgrepRunner, "run", fake_run)
    Config().set("semgrep_timeout", "33")
    Config().set("scan_max_file_size_kb", "1")
    (tmp_path / "big.py").write_text("y = 1\n" * 1000)  # > 1 KB, should be skipped
    (tmp_path / "small.py").write_text("y = 1\n")
    from devkit.cli import app
    r = CliRunner().invoke(app, ["scan", str(tmp_path), "--no-claude", "--output", "json"])
    data = json.loads(r.output)
    assert seen["timeout"] == 33 and data["files_scanned"] == 1
