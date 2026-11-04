"""Shared task ledger for multi-agent coding work (handoff / takeover / recovery).

Each task has an owner with a time-limited *lease*. An agent claims a task,
works (optionally in its own git worktree + branch), heartbeats to keep the
lease, and either completes it (all acceptance criteria checked, commits
recorded, merge conflicts checked) or releases it with handover notes for the
next agent. A crashed agent's lease expires and `recover` returns the task to
the pool with its history intact. Every action is an event, so what each
agent did is traceable and the counts feed delivery metrics.

Storage: SQLite in $DEVKIT_HOME/handoff/ledger.db. Claims run inside
BEGIN IMMEDIATE so two agents cannot claim the same task.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from devkit.paths import HANDOFF_DIR

LEDGER_DB = HANDOFF_DIR / "ledger.db"
DEFAULT_LEASE_MINUTES = 30
CLAIMABLE = ("open", "handoff")


class LedgerError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class Task:
    id: str
    project: str
    title: str
    description: str
    status: str
    owner: str | None
    lease_until: str | None
    worktree: str | None
    branch: str | None
    base_ref: str | None
    created_at: str
    updated_at: str


class Ledger:
    def __init__(self, db_path: Path = LEDGER_DB) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    owner TEXT, lease_until TEXT, worktree TEXT, branch TEXT, base_ref TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS criteria (
                    task_id TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL,
                    checked_by TEXT, checked_at TEXT, PRIMARY KEY (task_id, idx));
                CREATE TABLE IF NOT EXISTS deps (
                    task_id TEXT NOT NULL, depends_on TEXT NOT NULL, PRIMARY KEY (task_id, depends_on));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, ts TEXT NOT NULL,
                    agent TEXT, kind TEXT NOT NULL, note TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS commits (
                    task_id TEXT NOT NULL, sha TEXT NOT NULL, agent TEXT, PRIMARY KEY (task_id, sha));
            """)
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _tx(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _event(conn, task_id: str, agent: str | None, kind: str, note: str | None = None,
               data: dict | None = None) -> None:
        conn.execute("INSERT INTO events (task_id, ts, agent, kind, note, data) VALUES (?,?,?,?,?,?)",
                     (task_id, _iso(_now()), agent, kind, note, json.dumps(data) if data else None))

    def _resolve(self, conn, task_id: str) -> sqlite3.Row:
        rows = conn.execute("SELECT * FROM tasks WHERE id LIKE ?", (task_id + "%",)).fetchall()
        if not rows:
            raise LedgerError(f"No task with id {task_id!r}")
        if len(rows) > 1:
            raise LedgerError(f"Ambiguous id prefix {task_id!r}")
        return rows[0]

    @staticmethod
    def _to_task(row: sqlite3.Row) -> Task:
        return Task(**{k: row[k] for k in row.keys()})

    def _require_owner(self, row: sqlite3.Row, agent: str) -> None:
        if row["status"] != "claimed" or row["owner"] != agent:
            raise LedgerError(f"Task {row['id']} is not claimed by {agent!r} "
                              f"(status={row['status']}, owner={row['owner']})")

    # ── commands ─────────────────────────────────────────────────────────────
    def create(self, title: str, project: str, description: str = "",
               criteria: list[str] | None = None, depends_on: list[str] | None = None,
               agent: str | None = None) -> str:
        tid = uuid.uuid4().hex[:6]
        now = _iso(_now())
        with self._tx() as conn:
            dep_ids = [self._resolve(conn, d)["id"] for d in depends_on or []]
            conn.execute("INSERT INTO tasks (id, project, title, description, status, created_at, updated_at)"
                         " VALUES (?,?,?,?, 'open', ?, ?)", (tid, project, title, description, now, now))
            for i, text in enumerate(criteria or []):
                conn.execute("INSERT INTO criteria (task_id, idx, text) VALUES (?,?,?)", (tid, i + 1, text))
            for d in dep_ids:
                conn.execute("INSERT INTO deps (task_id, depends_on) VALUES (?,?)", (tid, d))
            self._event(conn, tid, agent, "created", title)
        return tid

    def claim(self, task_id: str, agent: str, lease_minutes: int = DEFAULT_LEASE_MINUTES,
              now: datetime | None = None) -> Task:
        now = now or _now()
        with self._tx(immediate=True) as conn:
            row = self._resolve(conn, task_id)
            expired = row["status"] == "claimed" and row["lease_until"] and row["lease_until"] < _iso(now)
            if row["status"] == "claimed" and row["owner"] == agent and not expired:
                pass  # re-claim by the same agent just extends the lease
            elif row["status"] not in CLAIMABLE and not expired:
                raise LedgerError(f"Task {row['id']} is {row['status']} (owner: {row['owner']})")
            blocking = conn.execute(
                "SELECT t.id, t.status FROM deps d JOIN tasks t ON t.id = d.depends_on "
                "WHERE d.task_id = ? AND t.status != 'done'", (row["id"],)).fetchall()
            if blocking:
                raise LedgerError("Blocked by unfinished dependencies: "
                                  + ", ".join(f"{b['id']} ({b['status']})" for b in blocking))
            kind = "takeover" if (row["status"] == "handoff" or expired) else "claimed"
            if expired:
                self._event(conn, row["id"], row["owner"], "lease_expired")
            conn.execute("UPDATE tasks SET status='claimed', owner=?, lease_until=?, updated_at=? WHERE id=?",
                         (agent, _iso(now + timedelta(minutes=lease_minutes)), _iso(now), row["id"]))
            self._event(conn, row["id"], agent, kind)
            return self._to_task(conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, task_id: str, agent: str, lease_minutes: int = DEFAULT_LEASE_MINUTES) -> str:
        with self._tx(immediate=True) as conn:
            row = self._resolve(conn, task_id)
            self._require_owner(row, agent)
            until = _iso(_now() + timedelta(minutes=lease_minutes))
            conn.execute("UPDATE tasks SET lease_until=?, updated_at=? WHERE id=?", (until, _iso(_now()), row["id"]))
            return until

    def set_workspace(self, task_id: str, worktree: str, branch: str, base_ref: str) -> None:
        with self._tx() as conn:
            row = self._resolve(conn, task_id)
            conn.execute("UPDATE tasks SET worktree=?, branch=?, base_ref=? WHERE id=?",
                         (worktree, branch, base_ref, row["id"]))
            self._event(conn, row["id"], row["owner"], "workspace",
                        f"{branch} @ {worktree}", {"base_ref": base_ref})

    def note(self, task_id: str, agent: str, text: str, intervention: bool = False) -> None:
        with self._tx() as conn:
            row = self._resolve(conn, task_id)
            self._event(conn, row["id"], agent, "intervention" if intervention else "note", text)

    def check(self, task_id: str, idx: int, agent: str) -> None:
        with self._tx() as conn:
            row = self._resolve(conn, task_id)
            cur = conn.execute("UPDATE criteria SET checked_by=?, checked_at=? WHERE task_id=? AND idx=?",
                               (agent, _iso(_now()), row["id"], idx))
            if cur.rowcount == 0:
                raise LedgerError(f"Task {row['id']} has no criterion #{idx}")
            self._event(conn, row["id"], agent, "criterion_checked", str(idx))

    def release(self, task_id: str, agent: str, note: str) -> None:
        if not note.strip():
            raise LedgerError("A handover note is required: what is done, what is next, what is risky.")
        with self._tx(immediate=True) as conn:
            row = self._resolve(conn, task_id)
            self._require_owner(row, agent)
            conn.execute("UPDATE tasks SET status='handoff', owner=NULL, lease_until=NULL, updated_at=? "
                         "WHERE id=?", (_iso(_now()), row["id"]))
            self._event(conn, row["id"], agent, "handoff", note)

    def complete(self, task_id: str, agent: str, commits: list[str] | None = None,
                 conflict: bool = False, force: bool = False, note: str | None = None) -> None:
        if conflict:
            # Recorded in its own transaction so the count survives the refusal below.
            with self._tx(immediate=True) as conn:
                row = self._resolve(conn, task_id)
                self._require_owner(row, agent)
                self._event(conn, row["id"], agent, "conflict", "merge conflicts with base")
        with self._tx(immediate=True) as conn:
            row = self._resolve(conn, task_id)
            self._require_owner(row, agent)
            unchecked = conn.execute("SELECT idx, text FROM criteria WHERE task_id=? AND checked_at IS NULL",
                                     (row["id"],)).fetchall()
            if unchecked and not force:
                raise LedgerError("Unchecked acceptance criteria: "
                                  + "; ".join(f"#{u['idx']} {u['text']}" for u in unchecked))
            if conflict and not force:
                raise LedgerError("Branch conflicts with its base; rebase/resolve first (or --force).")
            for sha in commits or []:
                conn.execute("INSERT OR IGNORE INTO commits (task_id, sha, agent) VALUES (?,?,?)",
                             (row["id"], sha, agent))
            conn.execute("UPDATE tasks SET status='done', lease_until=NULL, updated_at=? WHERE id=?",
                         (_iso(_now()), row["id"]))
            self._event(conn, row["id"], agent, "done", note,
                        {"commits": commits or [], "forced": force and bool(unchecked or conflict)})

    def recover(self, now: datetime | None = None) -> list[Task]:
        """Return tasks with expired leases to the pool (status 'handoff')."""
        now = now or _now()
        with self._tx(immediate=True) as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE status='claimed' AND lease_until < ?",
                                (_iso(now),)).fetchall()
            for row in rows:
                conn.execute("UPDATE tasks SET status='handoff', owner=NULL, lease_until=NULL, updated_at=? "
                             "WHERE id=?", (_iso(now), row["id"]))
                self._event(conn, row["id"], row["owner"], "recovered",
                            f"lease expired at {row['lease_until']}; returned to pool")
            return [self._to_task(r) for r in rows]

    # ── queries ──────────────────────────────────────────────────────────────
    def get(self, task_id: str) -> Task:
        with self._tx() as conn:
            return self._to_task(self._resolve(conn, task_id))

    def list(self, project: str | None = None, status: str | None = None) -> list[Task]:
        q, params = "SELECT * FROM tasks WHERE 1=1", []
        if project:
            q += " AND project=?"
            params.append(project)
        if status:
            q += " AND status=?"
            params.append(status)
        with self._tx() as conn:
            return [self._to_task(r) for r in conn.execute(q + " ORDER BY created_at", params).fetchall()]

    def details(self, task_id: str) -> dict:
        with self._tx() as conn:
            row = self._resolve(conn, task_id)
            tid = row["id"]
            return {
                "task": self._to_task(row),
                "criteria": [dict(r) for r in conn.execute(
                    "SELECT idx, text, checked_by, checked_at FROM criteria WHERE task_id=? ORDER BY idx", (tid,))],
                "deps": [dict(r) for r in conn.execute(
                    "SELECT t.id, t.title, t.status FROM deps d JOIN tasks t ON t.id=d.depends_on "
                    "WHERE d.task_id=?", (tid,))],
                "events": [dict(r) for r in conn.execute(
                    "SELECT ts, agent, kind, note FROM events WHERE task_id=? ORDER BY id", (tid,))],
                "commits": [r["sha"] for r in conn.execute("SELECT sha FROM commits WHERE task_id=?", (tid,))],
            }

    def stats(self, project: str | None = None) -> dict:
        where, params = ("WHERE t.project=?", [project]) if project else ("", [])
        with self._tx() as conn:
            status = dict(conn.execute(f"SELECT status, COUNT(*) FROM tasks t {where} GROUP BY status",
                                       params).fetchall())
            kinds = dict(conn.execute(
                f"SELECT e.kind, COUNT(*) FROM events e JOIN tasks t ON t.id=e.task_id {where} GROUP BY e.kind",
                params).fetchall())
        return {
            "tasks_by_status": status,
            "completed": status.get("done", 0),
            "handoffs": kinds.get("handoff", 0),
            "takeovers": kinds.get("takeover", 0),
            "recoveries": kinds.get("recovered", 0),
            "integration_conflicts": kinds.get("conflict", 0),
            "manual_interventions": kinds.get("intervention", 0),
        }

    def brief(self, task_id: str, memory_lines: list[str] | None = None) -> str:
        """Context block for the agent taking the task next (paste into, or pipe to, a session)."""
        d = self.details(task_id)
        t: Task = d["task"]
        out = [f"<devkit-task id='{t.id}' project='{t.project}' status='{t.status}'>",
               f"TASK: {t.title}"]
        if t.description:
            out.append(t.description)
        if d["criteria"]:
            out.append("\nACCEPTANCE CRITERIA (all must be checked before `devkit handoff done`):")
            for c in d["criteria"]:
                mark = "x" if c["checked_at"] else " "
                out.append(f"  [{mark}] #{c['idx']} {c['text']}")
        if t.branch:
            out.append(f"\nWORKSPACE: branch {t.branch} in {t.worktree} (base {t.base_ref})")
        if d["deps"]:
            out.append("\nDEPENDS ON: " + ", ".join(f"{x['id']} {x['title']} [{x['status']}]" for x in d["deps"]))
        handovers = [e for e in d["events"] if e["kind"] in ("handoff", "recovered", "note", "intervention")]
        if handovers:
            out.append("\nHISTORY (oldest first):")
            for e in handovers[-12:]:
                out.append(f"  {e['ts'][:16]} {e['agent'] or '-'} {e['kind']}: {e['note'] or ''}")
        last = next((e for e in reversed(d["events"]) if e["kind"] == "handoff"), None)
        if last:
            out.append(f"\nSTART HERE (last handover note): {last['note']}")
        if d["commits"]:
            out.append("\nCOMMITS: " + ", ".join(d["commits"]))
        if memory_lines:
            out.append("\nRELEVANT PROJECT MEMORY:")
            out += [f"  - {m}" for m in memory_lines]
        out.append("</devkit-task>")
        return "\n".join(out)


# ── git helpers (isolated workspaces, traceability, conflict detection) ──────

def _git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


def create_worktree(repo: Path, task_id: str, base_ref: str | None = None) -> tuple[str, str, str]:
    """git worktree add -b devkit/<id> <repo>-wt-<id> <base>. Returns (path, branch, base_ref).

    base_ref defaults to the current branch name (not 'HEAD', which moves)."""
    top = _git(["rev-parse", "--show-toplevel"], repo)
    if top.returncode != 0:
        raise LedgerError("Not inside a git repository")
    root = Path(top.stdout.strip())
    if not base_ref:
        cur = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip()
        base_ref = cur if cur and cur != "HEAD" else _git(["rev-parse", "HEAD"], root).stdout.strip()
    branch = f"devkit/{task_id}"
    path = root.parent / f"{root.name}-wt-{task_id}"
    if path.exists():
        return str(path), branch, base_ref
    out = _git(["worktree", "add", "-b", branch, str(path), base_ref], root)
    if out.returncode != 0:
        raise LedgerError(f"git worktree add failed: {out.stderr.strip()}")
    return str(path), branch, base_ref


def branch_commits(cwd: str | Path, base_ref: str, branch: str) -> list[str]:
    out = _git(["rev-list", "--reverse", f"{base_ref}..{branch}"], cwd)
    return out.stdout.split() if out.returncode == 0 else []


def has_merge_conflict(cwd: str | Path, base_ref: str, branch: str) -> bool | None:
    """True/False via `git merge-tree --write-tree` (git >= 2.38); None if unsupported."""
    out = _git(["merge-tree", "--write-tree", base_ref, branch], cwd)
    if out.returncode == 0:
        return False
    if out.returncode == 1:
        return True
    return None
