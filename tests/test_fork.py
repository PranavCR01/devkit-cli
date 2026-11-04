"""Issue 04: fork path traversal + extraction correctness."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

pytest.importorskip("networkx")


@pytest.mark.parametrize("name", ["..", "../x", "a/b", "/abs", "..\\x", "", "a" * 80, "x/../.."])
def test_blueprint_names_cannot_escape_dir(name):
    from devkit.core.fork.blueprint import blueprint_dir
    with pytest.raises(ValueError):
        blueprint_dir(name)


def test_fork_delete_dotdot_does_not_remove_devkit_home():
    from devkit.cli import app
    from devkit.paths import DEVKIT_DIR
    (DEVKIT_DIR / "memory.db").write_text("precious")
    r = CliRunner().invoke(app, ["fork", "delete", "..", "--yes"])
    assert r.exit_code == 1
    assert (DEVKIT_DIR / "memory.db").read_text() == "precious"


def test_fork_apply_rejects_traversal():
    from devkit.cli import app
    r = CliRunner().invoke(app, ["fork", "apply", "../../etc"])
    assert r.exit_code == 1 and "Invalid blueprint name" in r.output


def _graph(tmp_path):
    nodes = [
        {"id": "auth", "name": "auth", "filePath": "src/auth/index.ts", "type": "module"},
        {"id": "authCtl", "name": "AuthController", "filePath": "src/api/AuthController.ts"},
        {"id": "login", "name": "loginHandler", "filePath": "src/api/login.ts", "tags": ["Auth"]},
        {"id": "author", "name": "authorProfile", "filePath": "src/blog/author.ts"},
        {"id": "a", "name": "a", "filePath": "src/a.ts"},
        {"id": "b", "name": "b", "filePath": "src/b.ts"},
        {"id": "c", "name": "c", "filePath": "src/c.ts"},
        {"id": "logger", "name": "logger", "filePath": "src/logger.ts"},
    ]
    edges = [{"source": s, "target": "auth", "type": "imports"} for s in ("a", "b", "c", "authCtl")]
    edges += [{"source": s, "target": "logger", "type": "imports"} for s in ("a", "b", "c", "auth")]
    edges += [{"source": "login", "target": "auth", "type": "calls"}]
    p = tmp_path / "kg.json"
    p.write_text(json.dumps({"nodes": nodes, "edges": edges}))
    return str(p)


def test_seed_matching_is_word_boundary_and_case_insensitive(tmp_path):
    from devkit.core.fork.extractor import SubgraphExtractor
    seeds = set(SubgraphExtractor(_graph(tmp_path)).find_seed_nodes("auth"))
    assert {"auth", "authCtl", "login"} <= seeds
    assert "author" not in seeds


def test_seed_with_high_in_degree_is_not_external(tmp_path):
    from devkit.core.fork.extractor import SubgraphExtractor
    ex = SubgraphExtractor(_graph(tmp_path))
    nodes, _edges, external = ex.extract("auth", max_nodes=8)
    ids = {n["id"] for n in nodes}
    assert "auth" in ids, "the auth module itself used to be dropped as a 'shared dependency'"
    assert "src/logger.ts" in external


def test_blueprint_records_actual_seed_nodes(tmp_path):
    from devkit.core.fork.blueprint import Blueprint
    from devkit.core.fork.extractor import SubgraphExtractor
    ex = SubgraphExtractor(_graph(tmp_path))
    nodes, edges, ext = ex.extract("auth", max_nodes=8)
    Blueprint.create("auth-pattern", "auth", "p", nodes, edges, ext, [], {}, seed_nodes=ex.last_seed_ids)
    assert set(Blueprint.load("auth-pattern")["seed_nodes"]) == set(ex.last_seed_ids)
