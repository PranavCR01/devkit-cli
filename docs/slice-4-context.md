# DevKit — Slice 4: `/context`

## Overview

Slice 4 builds the discovery and deliberate context assembly layer. This is the glue that makes memory, graphs, and search actually usable. Without it, developers have to already know what exists before they can use it. `/context list` answers "what do I have?" before `/context add` answers "inject this into my session."

**Prerequisite: Slices 1, 2, and 3 must be fully working before starting this slice.**

---

## Goals

- `devkit context list` shows all available context items across all projects
- Grouped display: by project, then by type (graphs, snapshots, workstreams, blueprints)
- `devkit context add <name>` injects single item into Claude Code session
- `devkit context build` interactive multi-select with live token budget display
- `devkit context budget` shows current assembled context token count
- Token budget enforcement: drop whole items when over cap (never truncate mid-item)
- Manifest file (`~/.devkit/manifest.json`) tracks all available context items without rescanning on every call
- Hook-based injection into Claude Code session via echo to stdout

---

## Success Criteria

- `devkit context list` completes in under 500ms regardless of project count
- `devkit context build` interactive mode works cleanly in Windows terminal
- Injected context appears correctly in Claude Code session (test by asking Claude to reference it)
- Token budget display is accurate within 10% of actual token count
- `devkit context list --project swagath-central` filters correctly to one project
- Manifest updates automatically when scan completes, memory is saved, or workstream switches

---

## File Structure Additions

```
devkit/
├── commands/
│   └── context.py            # /context command
└── core/
    └── context/
        ├── __init__.py
        ├── manifest.py        # Manifest file management
        ├── assembler.py       # Context assembly + token budgeting
        └── injector.py        # Hook-based injection

~/.devkit/
├── manifest.json              # Registry of all available context items
└── hooks/
    └── session-start.sh       # Claude Code SessionStart hook (from Slice 2)
```

---

## Manifest File Schema

`~/.devkit/manifest.json`:

```json
{
    "version": "1",
    "updated_at": "2025-06-10T12:00:00Z",
    "projects": {
        "swagath-central": {
            "path": "D:/Python files/swagath-central",
            "knowledge_graph": {
                "path": "D:/Python files/swagath-central/.understand-anything/knowledge-graph.json",
                "updated_at": "2025-06-10T10:00:00Z",
                "node_count": 847,
                "edge_count": 1203
            },
            "workstreams": ["payment-feature", "bug-rls-fix", "main"],
            "fact_count": 23,
            "last_scan": "2025-06-09T15:00:00Z",
            "grade": "C"
        },
        "cia-project": {
            "path": "D:/Python files/cia-project",
            "knowledge_graph": null,
            "workstreams": ["main"],
            "fact_count": 11,
            "last_scan": "2025-05-12T09:00:00Z",
            "grade": "A"
        }
    },
    "blueprints": {
        "auth-pattern": {
            "path": "D:/Users/crpra/.devkit/blueprints/auth-pattern",
            "source_project": "swagath-central",
            "created_at": "2025-06-05T09:00:00Z",
            "token_estimate": 1200
        }
    }
}
```

### Manifest update triggers

Update the manifest (event-driven, not polling) when:
- `devkit init` runs in a new project directory → register project
- `devkit scan` completes → update `last_scan` and `grade`
- `devkit memory save` runs → increment `fact_count`
- `devkit memory switch` runs → update `workstreams`
- `devkit fork` creates a blueprint → add to `blueprints`
- `devkit context refresh` runs → rescan all project paths for new knowledge graphs

Never rebuild the manifest from scratch on every read — only update the changed fields.

---

## Key Classes

### `devkit/core/context/manifest.py`

```python
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

MANIFEST_PATH = Path.home() / ".devkit" / "manifest.json"

class Manifest:

    def load(self) -> dict:
        if not MANIFEST_PATH.exists():
            return {"version": "1", "updated_at": "", "projects": {}, "blueprints": {}}
        with open(MANIFEST_PATH) as f:
            return json.load(f)

    def save(self, data: dict) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(MANIFEST_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def register_project(self, name: str, path: str) -> None:
        data = self.load()
        if name not in data["projects"]:
            data["projects"][name] = {
                "path": path,
                "knowledge_graph": None,
                "workstreams": ["main"],
                "fact_count": 0,
                "last_scan": None,
                "grade": None,
            }
        self._detect_knowledge_graph(data, name)
        self.save(data)

    def update_scan(self, project: str, grade: str) -> None:
        data = self.load()
        if project in data["projects"]:
            data["projects"][project]["last_scan"] = datetime.now(timezone.utc).isoformat()
            data["projects"][project]["grade"] = grade
        self.save(data)

    def update_fact_count(self, project: str, delta: int = 1) -> None:
        data = self.load()
        if project in data["projects"]:
            data["projects"][project]["fact_count"] = \
                data["projects"][project].get("fact_count", 0) + delta
        self.save(data)

    def refresh_knowledge_graphs(self) -> None:
        """Scan all registered project paths for .understand-anything/knowledge-graph.json"""
        data = self.load()
        for name, project in data["projects"].items():
            self._detect_knowledge_graph(data, name)
        self.save(data)

    def _detect_knowledge_graph(self, data: dict, project_name: str) -> None:
        project = data["projects"][project_name]
        project_path = Path(project["path"])
        graph_path = project_path / ".understand-anything" / "knowledge-graph.json"

        if graph_path.exists():
            stat = graph_path.stat()
            # Quick node/edge count without loading full graph
            try:
                with open(graph_path) as f:
                    graph = json.load(f)
                node_count = len(graph.get("nodes", []))
                edge_count = len(graph.get("edges", []))
            except Exception:
                node_count = edge_count = 0

            project["knowledge_graph"] = {
                "path": str(graph_path),
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "node_count": node_count,
                "edge_count": edge_count,
            }
        else:
            project["knowledge_graph"] = None

    def get_all_context_items(self) -> list[dict]:
        """Flat list of all available context items with metadata.

        Each item:
        {
            id: str,             # "project:type:name" — used in devkit context add <id>
            project: str,
            type: str,           # "graph" | "snapshot" | "workstream" | "blueprint"
            name: str,
            description: str,
            updated_at: str,
            token_estimate: int,
        }
        """
        data = self.load()
        items = []

        for proj_name, proj in data["projects"].items():
            # Knowledge graph
            if proj.get("knowledge_graph"):
                kg = proj["knowledge_graph"]
                items.append({
                    "id": f"{proj_name}:graph",
                    "project": proj_name,
                    "type": "graph",
                    "name": "knowledge-graph",
                    "description": f"{kg['node_count']} nodes, {kg['edge_count']} edges",
                    "updated_at": kg["updated_at"],
                    "token_estimate": kg["node_count"] * 5,  # rough: ~5 tokens per node summary
                })

            # Session snapshot
            if proj.get("fact_count", 0) > 0:
                items.append({
                    "id": f"{proj_name}:snapshot",
                    "project": proj_name,
                    "type": "snapshot",
                    "name": "session-snapshot",
                    "description": f"{proj['fact_count']} facts",
                    "updated_at": proj.get("last_scan") or proj.get("updated_at", ""),
                    "token_estimate": proj["fact_count"] * 35,  # rough: ~35 tokens per fact
                })

            # Workstreams
            for ws in proj.get("workstreams", []):
                items.append({
                    "id": f"{proj_name}:workstream:{ws}",
                    "project": proj_name,
                    "type": "workstream",
                    "name": ws,
                    "description": f"Workstream context",
                    "updated_at": "",
                    "token_estimate": 300,
                })

        # Blueprints (cross-project)
        for bp_name, bp in data.get("blueprints", {}).items():
            items.append({
                "id": f"blueprint:{bp_name}",
                "project": bp["source_project"],
                "type": "blueprint",
                "name": bp_name,
                "description": f"from {bp['source_project']}",
                "updated_at": bp["created_at"],
                "token_estimate": bp.get("token_estimate", 1000),
            })

        return items
```

### `devkit/core/context/assembler.py`

```python
try:
    import tiktoken
    ENCODER = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(ENCODER.encode(text))
except ImportError:
    # Fallback: rough word count * 1.3
    def count_tokens(text: str) -> int:
        return int(len(text.split()) * 1.3)

TOKEN_CAP_DEFAULT = 8000

class ContextAssembler:
    """Assembles selected context items into injectable string.

    Token budget rules:
    - Estimate tokens before adding each item
    - Drop whole items (NEVER truncate mid-item) when over budget
    - Always append "N items omitted due to token budget" if any dropped
    - Group items by project in output for readability
    """

    def __init__(self, token_cap: int = TOKEN_CAP_DEFAULT):
        self.token_cap = token_cap
        self.items: list[dict] = []       # {"meta": item_dict, "content": str}
        self.current_tokens: int = 0
        self.dropped: list[str] = []      # names of dropped items

    def add_item(self, item: dict, content: str) -> bool:
        """Try to add an item. Returns False if it would exceed budget."""
        item_tokens = count_tokens(content)
        if self.current_tokens + item_tokens > self.token_cap:
            self.dropped.append(item["name"])
            return False
        self.items.append({"meta": item, "content": content})
        self.current_tokens += item_tokens
        return True

    def render(self) -> str:
        """Render assembled context as injectable XML-tagged string."""
        if not self.items:
            return ""

        parts = ["<devkit-context>"]
        for item in self.items:
            meta = item["meta"]
            parts.append(
                f'\n[{meta["type"].upper()}] {meta["project"]} / {meta["name"]}'
            )
            parts.append(item["content"])

        if self.dropped:
            parts.append(
                f'\n[OMITTED] {len(self.dropped)} items excluded due to token budget: '
                + ", ".join(self.dropped)
            )

        parts.append("\n</devkit-context>")
        parts.append("\nApply the above DevKit context silently to this session.")
        return "\n".join(parts)

    def remaining_tokens(self) -> int:
        return self.token_cap - self.current_tokens

    def summary(self) -> str:
        return (
            f"{len(self.items)} items assembled | "
            f"{self.current_tokens}/{self.token_cap} tokens used | "
            f"{len(self.dropped)} dropped"
        )
```

---

## CLI Commands

```bash
# Discovery
devkit context list                              # All context items, grouped by project
devkit context list --project swagath-central    # Filter by project
devkit context list --type graph                 # Types: graph|snapshot|workstream|blueprint
devkit context list --json                       # JSON output

# Injection
devkit context add <item-id>                     # Inject single item into session
devkit context add "swagath-central:graph"       # Inject knowledge graph
devkit context add "swagath-central:workstream:payment-feature"
devkit context add "blueprint:auth-pattern"

# Interactive assembly
devkit context build                             # Multi-select + live token counter
devkit context build --token-cap 10000           # Custom token cap

# Budget management
devkit context budget                            # Show current assembly token usage
devkit context clear                             # Clear assembled context

# Maintenance
devkit context refresh                           # Rescan all projects, update manifest
devkit context register <project-name> <path>    # Manually register a project
```

### `devkit context list` output

```
DevKit Context Registry
────────────────────────────────────────────────────────
swagath-central  (D:/Python files/swagath-central)
  📊 graph        knowledge-graph        847 nodes  updated 2h ago   ~4,200 tokens
  💾 snapshot     session-snapshot       23 facts   updated 1h ago   ~800 tokens
  🔀 workstream   payment-feature                   updated 3d ago   ~300 tokens
  🔀 workstream   bug-rls-fix                       updated 1w ago   ~300 tokens
  🔀 workstream   main                              updated 2w ago   ~300 tokens

cia-project  (D:/Python files/cia-project)
  💾 snapshot     session-snapshot       11 facts   updated 2d ago   ~400 tokens
  🔀 workstream   main                              updated 1w ago   ~300 tokens

blueprints
  📋 blueprint    auth-pattern           from: swagath-central  5d ago  ~1,200 tokens

────────────────────────────────────────────────────────
2 projects · 1 blueprint · 8 items available
Run: devkit context add <item-id>  or  devkit context build
```

### `devkit context build` interactive output

```
DevKit Context Builder  [budget: 8,000 tokens]

Select items to inject (space to select, enter to confirm):

swagath-central
  [ ] 📊 graph          ~4,200 tokens
  [x] 💾 snapshot       ~800 tokens
  [x] 🔀 payment-feature ~300 tokens

cia-project
  [ ] 💾 snapshot       ~400 tokens

blueprints
  [ ] 📋 auth-pattern   ~1,200 tokens

Selected: 2 items · 1,100 tokens used · 6,900 remaining
> Inject selected context? [Y/n]:
```

---

## Hook Registration

The session-start hook from Slice 2 now calls the context assembler:

`~/.devkit/hooks/session-start.sh` (updated):

```bash
#!/bin/bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_NAME=$(basename "$PROJECT_ROOT")

SNAPSHOT=$(python3 -m devkit.cli context inject --project "$PROJECT_NAME" --format hook 2>/dev/null)
if [ -n "$SNAPSHOT" ]; then
    echo "$SNAPSHOT"
fi
```

`devkit context inject --format hook` renders the auto-assembled snapshot (Hermes-style: recent decisions + active workstream, capped at 2000 tokens) suitable for echo injection.

---

## Open Decisions

1. **Interactive build on Windows** — `rich` prompts work in Windows Terminal but may not in the basic cmd.exe. Test in your actual environment. If `rich` select doesn't work, fall back to numbered selection: "Enter numbers separated by commas: 1,3,5".

2. **Token cap default** — 8000 for manual `context build`. Session snapshot auto-injection (from hook) uses a separate 2000 token cap. These are independent.

3. **`tiktoken` dependency** — adds ~10MB. Gate it behind a try/import with a word-count fallback (shown in assembler above) so token estimation works even without it installed.

4. **Graph content for injection** — loading the full knowledge-graph.json (potentially 4000+ tokens) into context is heavy. For `context add graph`, inject only the architectural summary (layer names, key file summaries) not the full node/edge list. Load the full graph only for `/fork` subgraph extraction.

5. **additionalContext vs echo** — test echo first (simpler, used by Understand Anything). If Claude doesn't reliably acknowledge injected content, switch to the JSON `additionalContext` response format from Claude Code hooks docs.
