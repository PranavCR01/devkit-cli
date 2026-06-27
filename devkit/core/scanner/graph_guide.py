from __future__ import annotations
import json
from collections import defaultdict, deque
from pathlib import Path


class GraphGuide:
    """Loads Understand Anything knowledge graph and provides:
    1. Graph-guided Tier 1 file prioritization
    2. Blast radius tracing via reverse BFS
    """

    def __init__(self, project_path: str) -> None:
        graph_path = Path(project_path) / ".understand-anything" / "knowledge-graph.json"
        self.graph: dict | None = None
        self.available = False
        self.reverse_adj: dict[str, list[str]] = defaultdict(list)
        self.node_by_id: dict[str, dict] = {}

        if graph_path.exists():
            with open(graph_path) as f:
                self.graph = json.load(f)
            self._build_reverse_adj()
            self.available = True

    def _build_reverse_adj(self) -> None:
        """Build reverse adjacency list: target → [sources].

        Reverse direction so that given a vulnerable file we can find every
        file that calls/imports/depends on it — the blast radius.
        """
        assert self.graph is not None
        for node in self.graph["nodes"]:
            self.node_by_id[node["id"]] = node

        dependency_edge_types = {"calls", "imports", "depends_on", "reads_from", "writes_to"}
        for edge in self.graph["edges"]:
            if edge["type"] in dependency_edge_types:
                self.reverse_adj[edge["target"]].append(edge["source"])

    def get_tier1_files(self) -> list[str]:
        """Return files that are high-priority based on graph structure.

        Priority: endpoint/schema/table node types, or high inbound call count (>=5).
        """
        if not self.available or self.graph is None:
            return []

        priority: list[str] = []
        for node in self.graph["nodes"]:
            if node.get("type") in ("endpoint", "schema", "table"):
                if fp := node.get("filePath"):
                    priority.append(fp)
            if len(self.reverse_adj.get(node["id"], [])) >= 5:
                if fp := node.get("filePath"):
                    priority.append(fp)

        return list(set(priority))

    def get_blast_radius(self, file_path: str) -> list[str]:
        """Backward BFS from file_path over the reverse call graph.

        Returns all files that can reach (call/import/depend on) the vulnerable file.
        O(V+E) on the reverse adjacency list.
        """
        if not self.available or self.graph is None:
            return []

        start_ids = [
            node["id"]
            for node in self.graph["nodes"]
            if node.get("filePath") == file_path
        ]

        visited: set[str] = set()
        queue: deque[str] = deque(start_ids)
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for parent in self.reverse_adj.get(current, []):
                if parent not in visited:
                    queue.append(parent)

        blast: list[str] = []
        for node_id in visited:
            if node := self.node_by_id.get(node_id):
                if fp := node.get("filePath"):
                    if fp != file_path:
                        blast.append(fp)

        return list(set(blast))
