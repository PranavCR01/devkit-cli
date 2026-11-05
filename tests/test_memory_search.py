"""Issue 07: workstreams, contradiction bookkeeping, scoped search."""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner


def test_switch_back_to_existing_workstream_makes_it_active(backend):
    backend.set_active_workstream("payments", "p")
    backend.set_active_workstream("main", "p")
    backend.set_active_workstream("payments", "p")
    assert backend.active_workstream("p") == "payments"
    assert "Workstream: payments" in backend.get_snapshot("p")


def test_cli_switch_saves_and_restores_state(backend, monkeypatch):
    import devkit.commands.memory_cmd as mc
    monkeypatch.setattr(mc, "_get_backend", lambda: backend)
    monkeypatch.setattr(mc, "_detect_project", lambda: "p")
    from devkit.cli import app
    run = CliRunner().invoke
    assert run(app, ["memory", "switch", "payments"]).exit_code == 0
    assert run(app, ["memory", "save", "stripe retries are idempotent", "--type", "decision"]).exit_code == 0
    r = run(app, ["memory", "switch", "auth-bugfix", "--note", "half-way through webhook retries"])
    assert r.exit_code == 0, r.output
    r = run(app, ["memory", "switch", "payments"])
    assert "half-way through webhook retries" in r.output
    assert "1 fact(s) tagged 'payments'" in r.output
    assert backend.active_workstream("p") == "payments"
    facts = backend.list_facts(project="p", workstream="payments")
    assert facts and facts[0].content.startswith("stripe")


def test_contradiction_records_measured_similarity(backend):
    a = backend.save("use postgres for the queue", "decision", "p")
    backend.save("use postgres for the queue now", "decision", "p")
    with closing(sqlite3.connect(backend.db_path)) as conn:
        sim = conn.execute("SELECT similarity FROM contradictions").fetchone()[0]
        reason = conn.execute("SELECT invalid_reason FROM facts WHERE id = ?", (a.id,)).fetchone()[0]
    assert 0.85 <= sim <= 1.0 and sim != 0.85
    assert reason and "superseded" in reason


def test_contradict_persists_reason(backend):
    f = backend.save("tabs over spaces", "preference", "p")
    backend.contradict(f.id, "team voted for spaces")
    with closing(sqlite3.connect(backend.db_path)) as conn:
        assert conn.execute("SELECT invalid_reason FROM facts WHERE id=?", (f.id,)).fetchone()[0] == \
            "team voted for spaces"


def test_migration_upgrades_old_schema(tmp_path, fake_embedder):
    pytest.importorskip("sqlite_vec")
    db = tmp_path / "old.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""CREATE TABLE facts (id TEXT PRIMARY KEY, project TEXT NOT NULL, workstream TEXT,
            content TEXT NOT NULL, fact_type TEXT NOT NULL, valid_at TEXT NOT NULL, invalid_at TEXT,
            created_at TEXT NOT NULL, source TEXT DEFAULT 'manual', confidence REAL DEFAULT 1.0,
            embedding BLOB)""")
        conn.execute("""CREATE TABLE workstreams (id TEXT PRIMARY KEY, project TEXT NOT NULL,
            name TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(project, name))""")
        conn.execute("INSERT INTO facts VALUES ('x','p',NULL,'old fact','decision','2025','',"
                     "'2025','manual',1.0,NULL)")
    from devkit.core.memory.sqlite_backend import SQLiteBackend
    b = SQLiteBackend(db, embedder=fake_embedder)
    with closing(sqlite3.connect(db)) as conn, conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)")}
        assert "invalid_reason" in cols
        assert conn.execute("SELECT content FROM facts WHERE id='x'").fetchone()[0] == "old fact"
    b.set_active_workstream("w", "p")


def test_scoped_search_finds_in_scope_fact_despite_crowding(backend):
    for i in range(80):
        backend.save(f"jwt token rotation note {i} variant {i * 7} extra{i}", "pattern", f"other{i % 5}")
    target = backend.save("jwt token rotation with refresh", "decision", "mine")
    res = backend.search("jwt token rotation", projects=["mine"], limit=3)
    assert res and res[0].fact.id == target.id
    res = backend._semantic_search(backend._get_embedder().embed("jwt token rotation"),
                                   ["mine"], None, False, 3)
    assert res and res[0][0] == target.id


def test_type_scoped_keyword_search_filters_in_sql(backend):
    for i in range(50):
        backend.save(f"redis cache eviction idea {i} n{i}", "pattern", "p")
    bug = backend.save("redis cache eviction bug under load", "bug", "p")
    kw = backend._keyword_search("redis cache", None, ["bug"], False, 2)
    assert [fid for fid, _ in kw] == [bug.id]
