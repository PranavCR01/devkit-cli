import json
from pathlib import Path

try:
    import networkx as nx
except ImportError as _e:
    raise ImportError(
        "networkx is required for fork: pip install -e '.[graph]'"
    ) from _e

SHARED_THRESHOLD = 3    # in-degree >= this in full graph -> external dependency
PAGERANK_ALPHA = 0.85   # standard damping factor
SEED_BOOST = 50.0       # personalization weight for seed nodes vs non-seed (1.0)
MAX_NODES_DEFAULT = 30


class SubgraphExtractor:
    """Extract a feature subgraph using personalized PageRank.

    Algorithm (Aider repomap-inspired):
    1. Load knowledge-graph.json as NetworkX DiGraph
    2. Find seed nodes matching the feature name (path + name + tags)
    3. Run personalized PageRank biased toward seed nodes (SEED_BOOST weight)
    4. Select top MAX_NODES_DEFAULT nodes by PageRank score
    5. Detect shared dependencies (high in-degree in full graph)
    6. Return subgraph nodes/edges + external dependency list

    Why PageRank over community detection:
    - We want relevance to a specific feature, not natural graph communities
    - PageRank with personalization gives ranked relevance scores
    - Community detection finds natural clusters, useful for exploration
      but not for targeted extraction
    """

    def __init__(self, graph_path: str):
        with open(graph_path, encoding="utf-8") as f:
            self.raw_graph = json.load(f)
        self.node_by_id: dict[str, dict] = {
            n["id"]: n for n in self.raw_graph.get("nodes", [])
        }
        self.G = self._build_networkx_graph()

    def _build_networkx_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        for node in self.raw_graph.get("nodes", []):
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
        for edge in self.raw_graph.get("edges", []):
            weight = self._edge_weight(edge.get("type", ""))
            G.add_edge(
                edge["source"], edge["target"],
                type=edge.get("type", ""),
                weight=weight,
                direction=edge.get("direction", "forward"),
            )
        return G

    def _edge_weight(self, edge_type: str) -> float:
        weights = {
            "calls": 1.0,
            "imports": 1.0,
            "depends_on": 0.8,
            "reads_from": 0.9,
            "writes_to": 0.9,
            "validates": 0.7,
            "contains": 0.5,
            "related": 0.3,
            "similar_to": 0.2,
        }
        return weights.get(edge_type, 0.5)

    def find_seed_nodes(self, feature_name: str) -> list[str]:
        """Find graph nodes matching the feature name in name, path, or tags."""
        feature_lower = feature_name.lower()
        seeds = []
        for node in self.raw_graph.get("nodes", []):
            name_match = feature_lower in node.get("name", "").lower()
            path_match = feature_lower in (node.get("filePath") or node.get("file_path") or "").lower()
            tag_match = any(feature_lower in tag for tag in node.get("tags", []))
            if name_match or path_match or tag_match:
                seeds.append(node["id"])
        return seeds

    def extract(
        self,
        feature_name: str,
        max_nodes: int = MAX_NODES_DEFAULT,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Extract feature subgraph.

        Returns:
            (subgraph_nodes, subgraph_edges, external_dependency_file_paths)
        """
        seed_ids = self.find_seed_nodes(feature_name)
        if not seed_ids:
            raise ValueError(
                f"No nodes found matching '{feature_name}'. "
                f"Try a broader term (e.g. 'auth', 'payment', 'user')."
            )

        all_nodes = list(self.G.nodes())
        if not all_nodes:
            raise ValueError("Knowledge graph has no nodes.")

        seed_set = set(seed_ids)
        personalization = {
            n: (SEED_BOOST if n in seed_set else 1.0)
            for n in all_nodes
        }
        total_weight = sum(personalization.values())
        personalization = {k: v / total_weight for k, v in personalization.items()}

        try:
            pr_scores = nx.pagerank(
                self.G,
                alpha=PAGERANK_ALPHA,
                personalization=personalization,
                weight="weight",
            )
        except nx.PowerIterationFailedConvergence:
            # Fall back to unweighted PageRank on convergence failure
            pr_scores = nx.pagerank(
                self.G,
                alpha=PAGERANK_ALPHA,
                personalization=personalization,
            )

        sorted_nodes = sorted(pr_scores.items(), key=lambda x: x[1], reverse=True)
        selected_ids = {node_id for node_id, _ in sorted_nodes[:max_nodes]}

        external_ids = self._detect_shared_dependencies(selected_ids)
        feature_ids = selected_ids - external_ids

        subgraph_nodes = [
            self.node_by_id[nid]
            for nid in feature_ids
            if nid in self.node_by_id
        ]
        subgraph_edges = [
            e for e in self.raw_graph.get("edges", [])
            if e["source"] in selected_ids and e["target"] in selected_ids
        ]
        external_files = list({
            self.node_by_id[nid].get("filePath")
            or self.node_by_id[nid].get("file_path")
            or nid
            for nid in external_ids
            if nid in self.node_by_id
        })

        return subgraph_nodes, subgraph_edges, external_files

    def _detect_shared_dependencies(self, selected_ids: set) -> set:
        """Nodes with in-degree >= SHARED_THRESHOLD in the FULL graph are shared utilities."""
        shared = set()
        for node_id in selected_ids:
            if self.G.in_degree(node_id) >= SHARED_THRESHOLD:
                shared.add(node_id)
        return shared
