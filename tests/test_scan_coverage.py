"""Issue 06: classifier paths, graph guidance, rulesets, line numbers."""
from __future__ import annotations

import json
from pathlib import Path

from devkit.core.scanner.classifier import FileClassifier, is_tier1
from devkit.core.scanner.claude_analyzer import CHUNK_SIZE_LINES, ClaudeAnalyzer
from devkit.core.scanner.semgrep_runner import _map_refs, rulesets_for, detect_languages


def test_parent_directories_do_not_make_everything_tier1(tmp_path):
    root = tmp_path / "home" / "user" / "api-keys-db" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "src" / "auth.py").write_text("def login(): ...\n")
    tier1, tier2 = FileClassifier().classify(str(root))
    names1 = {Path(p).name for p in tier1}
    assert names1 == {"auth.py"}
    assert {Path(p).name for p in tier2} == {"utils.py"}


def test_name_patterns_are_word_based():
    assert is_tier1("src/api/users.py", "")
    assert is_tier1("src/AuthController.ts", "")
    assert not is_tier1("src/monkey_patch.py", "")       # 'key'
    assert not is_tier1("src/rapid_render.py", "")       # 'api'
    assert is_tier1("src/x.py", "from fastapi import FastAPI\n")
    assert not is_tier1("src/x.py", "# we never import flask here, honest\n")


def test_graph_guided_uses_relative_paths_and_high_inbound(tmp_path):
    (tmp_path / "src").mkdir()
    for n in ("routes.ts", "helpers.ts", "core.ts"):
        (tmp_path / "src" / n).write_text("export const x = 1\n")
    graph = {"nodes": [{"id": "r", "type": "endpoint", "filePath": "./src/routes.ts"}]}
    tier1, _ = FileClassifier().classify(str(tmp_path), graph=graph, priority_paths=["src/core.ts"])
    assert {Path(p).name for p in tier1} == {"routes.ts", "core.ts"}


def test_blast_radius_matches_absolute_and_relative_paths(tmp_path):
    from devkit.core.scanner.graph_guide import GraphGuide
    (tmp_path / ".understand-anything").mkdir()
    (tmp_path / ".understand-anything" / "knowledge-graph.json").write_text(json.dumps({
        "nodes": [{"id": "db", "filePath": "src/db.ts"}, {"id": "api", "filePath": "src/api.ts"}],
        "edges": [{"source": "api", "target": "db", "type": "imports"}],
    }))
    g = GraphGuide(str(tmp_path))
    assert g.get_blast_radius(str(tmp_path / "src" / "db.ts")) == ["src/api.ts"]
    assert g.get_blast_radius("src/db.ts") == ["src/api.ts"]


def test_python_repo_gets_python_rules(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    configs = rulesets_for("all", detect_languages(str(tmp_path)))
    assert "p/python" in configs and "p/javascript" not in configs


def test_owasp_2025_numbering_for_injection():
    assert _map_refs("python.lang.security.sql-injection")[0] == "A05:2025"


def test_chunks_have_line_numbers_and_repeat_header(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"v{i} = {i}" for i in range(CHUNK_SIZE_LINES + 500)))
    chunks = ClaudeAnalyzer(client=object())._build_chunks([str(f)], [])
    assert len(chunks) == 2
    assert chunks[0].splitlines()[1] == "1| v0 = 0"
    assert "continued from line" in chunks[1].splitlines()[0]
    first_code = chunks[1].splitlines()[1]
    n = int(first_code.split("|")[0])
    assert first_code == f"{n}| v{n - 1} = {n - 1}"


def test_merge_dedups_after_path_normalization(tmp_path):
    import asyncio
    from devkit.core.scanner.orchestrator import ScanOrchestrator
    (tmp_path / "a.py").write_text("x = 1\n")
    orch = ScanOrchestrator(api_key="k")
    async def sem(*a):
        return [{"file_path": str(tmp_path / "a.py"), "line_start": 1, "title": "rule.one",
                 "severity": "high", "category": "security"},
                {"file_path": str(tmp_path / "a.py"), "line_start": 1, "title": "rule.two",
                 "severity": "low", "category": "security"}]
    async def cl(*a):
        return [{"file_path": "a.py", "line_start": 1, "title": "Claude view", "severity": "high",
                 "category": "security", "plain_english_desc": "explained"}]
    orch._run_semgrep, orch._run_claude = sem, cl
    res = asyncio.run(orch.run(str(tmp_path)))
    titles = sorted(f.title for f in res.findings)
    assert titles == ["rule.one", "rule.two"], titles
    assert any(f.plain_english_desc == "explained" for f in res.findings)
    assert all(f.file_path == "a.py" for f in res.findings)
