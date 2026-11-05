"""Session-start context injection (see docs/issues/03).

Pipeline:
1. Pinned items: anything the user selected with `devkit context add/build`
   (persisted in context_session.json) goes in first, whole-item, budget permitting.
2. Retrieved facts: candidate facts for the project are ranked by Reciprocal
   Rank Fusion over three lists:
     - hybrid BM25 + vector search against a query built from the session's
       situation (active workstream, git branch, recent commit subjects),
     - recency,
     - fact-type priority (decisions/architecture/bugs before preferences).
3. Packing: facts are added one at a time until the token cap, so an
   over-budget project still gets its most relevant facts instead of nothing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from devkit.core.context.assembler import count_tokens
from devkit.core.search.rrf import rrf_merge
from devkit.paths import SESSION_FILE

# Claude Code caps SessionStart hook output (~10k chars); stay under it.
HOOK_CHAR_LIMIT = 9500
_TYPE_PRIORITY = ["decision", "architecture", "bug", "vulnerability_pattern", "pattern", "preference"]


def detect_project(cwd: Path | None = None) -> str:
    cwd = cwd or Path.cwd()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, cwd=str(cwd), timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).name
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd.name


def situation_query(cwd: Path | None = None, workstream: str | None = None) -> str:
    """Words describing what the developer is working on right now."""
    parts: list[str] = []
    if workstream and workstream != "main":
        parts.append(workstream.replace("-", " ").replace("_", " "))
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, cwd=str(cwd or Path.cwd()), timeout=5)
        if branch.returncode == 0 and branch.stdout.strip() not in ("", "HEAD", "main", "master"):
            parts.append(branch.stdout.strip().replace("/", " ").replace("-", " ").replace("_", " "))
        log = subprocess.run(["git", "log", "-5", "--format=%s"], capture_output=True,
                             text=True, cwd=str(cwd or Path.cwd()), timeout=5)
        if log.returncode == 0:
            parts.extend(log.stdout.strip().splitlines())
    except (OSError, subprocess.SubprocessError):
        pass
    return " ".join(parts).strip()


def rank_facts(facts: list, search_hits: list[str]) -> list:
    """RRF over relevance (search hits), recency, and type priority."""
    by_id = {f.id: f for f in facts}
    recency = sorted(facts, key=lambda f: f.valid_at, reverse=True)
    priority = sorted(facts, key=lambda f: (_TYPE_PRIORITY.index(f.fact_type)
                                            if f.fact_type in _TYPE_PRIORITY else 99,
                                            ), )
    lists = [[(f.id, 0.0) for f in recency], [(f.id, 0.0) for f in priority]]
    relevant = [(fid, 0.0) for fid in search_hits if fid in by_id]
    if relevant:
        lists.insert(0, relevant)
        lists.insert(0, relevant)  # weight relevance x2 when we have a query
    merged = rrf_merge(lists, limit=len(facts))
    return [by_id[fid] for fid, _ in merged]


def load_session_items() -> list[dict]:
    if not SESSION_FILE.exists():
        return []
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []


def fetch_item_content(item: dict, manifest_data: dict) -> str:
    """Content for a manifest context item (graph/snapshot/workstream/blueprint)."""
    item_type, proj_name = item.get("type"), item.get("project")
    if item_type == "graph":
        kg = (manifest_data.get("projects", {}).get(proj_name, {}) or {}).get("knowledge_graph") or {}
        graph_path = Path(kg.get("path", ""))
        if not kg or not graph_path.is_file():
            return ""
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        nodes, edges = graph.get("nodes", []), graph.get("edges", [])
        lines = [f"Knowledge Graph: {proj_name}", f"Nodes: {len(nodes)}, Edges: {len(edges)}"]
        for node in nodes[:50]:
            name = node.get("name") or node.get("filePath") or str(node.get("id", ""))
            ntype = node.get("type", "")
            lines.append(f"  {ntype + ': ' if ntype else ''}{name}")
        if len(nodes) > 50:
            lines.append(f"  ... and {len(nodes) - 50} more nodes")
        return "\n".join(lines)
    if item_type in ("snapshot", "workstream"):
        try:
            from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
            if not MEMORY_DB.exists():
                return ""
            ws = item.get("name") if item_type == "workstream" else None
            facts = SQLiteBackend(MEMORY_DB).list_facts(project=proj_name, workstream=ws, limit=20)
        except Exception:
            return ""
        if not facts:
            return ""
        lines = [f"Project: {proj_name}"] + ([f"Workstream: {ws}"] if ws else [])
        lines += [f"[{f.fact_type.upper()}] {f.content}" for f in facts]
        return "\n".join(lines)
    if item_type == "blueprint":
        from devkit.core.fork.blueprint import Blueprint
        try:
            return Blueprint.render_for_injection(item["name"])
        except Exception:
            return ""
    return ""


def build_injection(
    project: str,
    token_cap: int = 2000,
    cwd: Path | None = None,
    backend: Any = None,
    include_session_items: bool = True,
) -> str:
    """Return the <devkit-context> block for a session, or '' if there is nothing."""
    used = 0
    sections: list[str] = []
    omitted: list[str] = []

    if include_session_items:
        try:
            from devkit.core.context.manifest import Manifest
            manifest_data = Manifest().load()
        except Exception:
            manifest_data = {}
        for item in load_session_items():
            content = fetch_item_content(item, manifest_data)
            if not content:
                continue
            cost = count_tokens(content)
            label = f"[{str(item.get('type', '')).upper()}] {item.get('project', '')} / {item.get('name', '')}"
            if used + cost > token_cap:
                omitted.append(label)
                continue
            sections.append(f"{label}\n{content}")
            used += cost

    if backend is None:
        try:
            from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
            if MEMORY_DB.exists():
                backend = SQLiteBackend(MEMORY_DB)
        except Exception:
            backend = None

    if backend is not None:
        facts = backend.list_facts(project=project, limit=500)
        if facts:
            workstream = None
            try:
                wss = backend.list_workstreams(project)
                workstream = wss[0]["name"] if wss else None
            except Exception:
                pass
            query = situation_query(cwd, workstream)
            hits: list[str] = []
            if query:
                try:
                    hits = [r.fact.id for r in backend.search(query, projects=[project], limit=50)]
                except Exception:
                    hits = []
            header = f"[MEMORY] {project}" + (f" | workstream: {workstream}" if workstream else "")
            lines: list[str] = []
            for fact in rank_facts(facts, hits):
                line = f"- [{fact.fact_type.upper()}] {fact.content}"
                cost = count_tokens(line)
                if used + cost > token_cap:
                    omitted.append(f"{fact.fact_type}:{fact.id[:8]}")
                    continue
                lines.append(line)
                used += cost
            if lines:
                sections.append(header + "\n" + "\n".join(lines))

    if not sections:
        return ""
    parts = ["<devkit-context>", *sections]
    if omitted:
        parts.append(f"[OMITTED] {len(omitted)} item(s) over the {token_cap}-token budget.")
    parts.append("</devkit-context>")
    parts.append("Apply the above DevKit context silently to this session.")
    out = "\n\n".join(parts)
    if len(out) > HOOK_CHAR_LIMIT:
        out = out[:HOOK_CHAR_LIMIT].rsplit("\n", 1)[0] + "\n[TRUNCATED]\n</devkit-context>"
    return out
