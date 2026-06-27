import json
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path.home() / ".devkit" / "manifest.json"


class Manifest:

    def load(self) -> dict:
        if not MANIFEST_PATH.exists():
            return {"version": "1", "updated_at": "", "projects": {}, "blueprints": {}}
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
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
            data["projects"][project]["fact_count"] = (
                data["projects"][project].get("fact_count", 0) + delta
            )
            self.save(data)

    def update_workstream(self, project: str, workstream: str) -> None:
        data = self.load()
        if project in data["projects"]:
            wss = data["projects"][project].setdefault("workstreams", ["main"])
            if workstream not in wss:
                wss.append(workstream)
            self.save(data)

    def refresh_knowledge_graphs(self) -> None:
        data = self.load()
        for name in data["projects"]:
            self._detect_knowledge_graph(data, name)
        self._recount_facts(data)
        self.save(data)

    def _recount_facts(self, data: dict) -> None:
        """Recount valid facts per project directly from memory.db via raw sqlite3."""
        import sqlite3
        memory_db = Path.home() / ".devkit" / "memory.db"
        if not memory_db.exists():
            return
        try:
            conn = sqlite3.connect(str(memory_db))
            rows = conn.execute(
                "SELECT project, COUNT(*) FROM facts WHERE invalid_at IS NULL GROUP BY project"
            ).fetchall()
            conn.close()
        except Exception:
            return

        # Reset all registered projects to 0, then accumulate from query rows.
        # Facts may be stored with a full path or a basename; match by basename.
        manifest_counts: dict[str, int] = {k: 0 for k in data["projects"]}
        for proj_val, count in rows:
            if proj_val in manifest_counts:
                manifest_counts[proj_val] += count
            else:
                basename = Path(proj_val).name
                if basename in manifest_counts:
                    manifest_counts[basename] += count

        for proj_name, count in manifest_counts.items():
            data["projects"][proj_name]["fact_count"] = count

    def _detect_knowledge_graph(self, data: dict, project_name: str) -> None:
        project = data["projects"][project_name]
        graph_path = Path(project["path"]) / ".understand-anything" / "knowledge-graph.json"

        if graph_path.exists():
            stat = graph_path.stat()
            try:
                with open(graph_path, encoding="utf-8") as f:
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
        data = self.load()
        items = []

        for proj_name, proj in data["projects"].items():
            if proj.get("knowledge_graph"):
                kg = proj["knowledge_graph"]
                items.append({
                    "id": f"{proj_name}:graph",
                    "project": proj_name,
                    "type": "graph",
                    "name": "knowledge-graph",
                    "description": f"{kg['node_count']} nodes, {kg['edge_count']} edges",
                    "updated_at": kg["updated_at"],
                    "token_estimate": kg["node_count"] * 5,
                })

            if proj.get("fact_count", 0) > 0:
                items.append({
                    "id": f"{proj_name}:snapshot",
                    "project": proj_name,
                    "type": "snapshot",
                    "name": "session-snapshot",
                    "description": f"{proj['fact_count']} facts",
                    "updated_at": proj.get("last_scan") or proj.get("updated_at", ""),
                    "token_estimate": proj["fact_count"] * 35,
                })

            for ws in proj.get("workstreams", []):
                items.append({
                    "id": f"{proj_name}:workstream:{ws}",
                    "project": proj_name,
                    "type": "workstream",
                    "name": ws,
                    "description": "Workstream context",
                    "updated_at": "",
                    "token_estimate": 300,
                })

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
