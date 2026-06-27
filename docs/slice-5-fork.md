# DevKit — Slice 5: `/fork`

## Overview

Slice 5 builds feature pattern transplanting. Extract a feature's subgraph and memory decisions from one project, register as a reusable blueprint, and apply as injectable context when building something similar in a new project. This is how DevKit turns past work into future leverage.

**Prerequisite: Slices 1–4 must be fully working. Slice 5 depends on both the knowledge graph (from Understand Anything) and the memory store (from Slice 2).**

---

## Goals

- `devkit fork <feature> --from <project>` extracts feature subgraph + memory decisions
- Seed-node personalized PageRank over Understand Anything's flat JSON graph
- Shared dependencies detected and marked "external" (not copied into blueprint)
- Blueprint format: subgraph JSON + memory facts + stack context bundled as a directory
- `devkit fork apply <blueprint-name>` injects blueprint as context into current session
- `devkit fork list` shows available blueprints with source project and token estimate

---

## Success Criteria

- `devkit fork auth --from swagath-central` completes in under 10 seconds
- Extracted subgraph is coherent — auth-related nodes present, unrelated nodes absent
- Shared utilities (supabase client, logger) correctly marked as external dependencies
- `devkit fork apply auth-pattern` injects useful context that Claude references
- Blueprint survives moving to a new project with different framework (pattern transfers, code adapts)
- Manifest updated after every fork (blueprint registered in `~/.devkit/manifest.json`)

---

## File Structure Additions

```
devkit/
├── commands/
│   └── fork.py                # /fork command
└── core/
    └── fork/
        ├── __init__.py
        ├── extractor.py       # PageRank-based subgraph extraction
        ├── blueprint.py       # Blueprint creation, serialization, loading
        └── applier.py         # Blueprint injection as context

~/.devkit/
└── blueprints/
    └── auth-pattern/
        ├── blueprint.json     # Metadata + memory facts + stack context
        ├── subgraph.json      # Extracted nodes/edges from knowledge graph
        └── exemplars/         # Optional: representative code snippets
            └── auth-handler.ts
```

---

## Blueprint Format

`~/.devkit/blueprints/<name>/blueprint.json`:

```json
{
    "version": "1",
    "name": "auth-pattern",
    "description": "Supabase JWT authentication with RLS and server-side middleware",
    "source_project": "swagath-central",
    "extracted_at": "2025-06-10T12:00:00Z",
    "seed_query": "auth",
    "seed_nodes": [
        "file:src/lib/auth.ts",
        "file:src/middleware/requireAuth.ts",
        "function:src/lib/auth.ts:validateToken"
    ],
    "external_dependencies": [
        "file:src/lib/supabase.ts",
        "file:src/utils/crypto.ts"
    ],
    "memory_facts": [
        {
            "content": "JWT validation happens server-side in middleware, never client-side",
            "fact_type": "decision",
            "valid_at": "2025-05-10T09:00:00Z"
        },
        {
            "content": "Supabase RLS must be enabled explicitly with ALTER TABLE users ENABLE ROW LEVEL SECURITY",
            "fact_type": "pattern",
            "valid_at": "2025-05-15T14:00:00Z"
        }
    ],
    "stack_context": {
        "language": "TypeScript",
        "framework": "Next.js",
        "auth_provider": "Supabase",
        "key_packages": ["@supabase/supabase-js", "jsonwebtoken"]
    },
    "transfer_notes": "Pattern is transferable to any JWT-based auth. Adapt RLS policies for your DB schema. Replace Supabase client init for other auth providers.",
    "node_count": 12,
    "edge_count": 18,
    "token_estimate": 1850
}
```

`~/.devkit/blueprints/<name>/subgraph.json` — same schema as Understand Anything's knowledge-graph.json but scoped to the feature:

```json
{
    "nodes": [...],    // GraphNode[] — only the selected feature nodes
    "edges": [...]     // GraphEdge[] — only edges between selected nodes
}
```

---

## Key Classes

### `devkit/core/fork/extractor.py`

```python
import json
import networkx as nx
from pathlib import Path

SHARED_THRESHOLD = 3   # referenced by this many distinct feature files = shared/external
PAGERANK_ALPHA = 0.85  # standard damping factor
SEED_BOOST = 50.0      # personalization weight for seed nodes vs non-seed (1.0)
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
    - Community detection (Leiden/Louvain) finds natural clusters,
      which is useful for exploring the graph but not for targeted extraction
    """

    def __init__(self, graph_path: str):
        with open(graph_path) as f:
            self.raw_graph = json.load(f)
        self.node_by_id: dict[str, dict] = {
            n["id"]: n for n in self.raw_graph["nodes"]
        }
        self.G = self._build_networkx_graph()

    def _build_networkx_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        for node in self.raw_graph["nodes"]:
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
        for edge in self.raw_graph["edges"]:
            weight = self._edge_weight(edge["type"])
            G.add_edge(
                edge["source"], edge["target"],
                type=edge["type"], weight=weight, direction=edge.get("direction", "forward")
            )
        return G

    def _edge_weight(self, edge_type: str) -> float:
        """Edge type weights for PageRank traversal."""
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
        """Find graph nodes matching the feature name.

        Checks: node name, file path, and tags.
        Returns IDs of matching nodes.
        """
        feature_lower = feature_name.lower()
        seeds = []
        for node in self.raw_graph["nodes"]:
            name_match = feature_lower in node.get("name", "").lower()
            path_match = feature_lower in (node.get("filePath") or "").lower()
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

        # Build personalization dict
        all_nodes = list(self.G.nodes())
        total_nodes = len(all_nodes)
        # Non-seed nodes get weight 1.0, seeds get SEED_BOOST
        seed_set = set(seed_ids)
        personalization = {
            n: (SEED_BOOST if n in seed_set else 1.0)
            for n in all_nodes
        }
        # Normalize
        total_weight = sum(personalization.values())
        personalization = {k: v / total_weight for k, v in personalization.items()}

        # Run personalized PageRank
        pr_scores = nx.pagerank(
            self.G,
            alpha=PAGERANK_ALPHA,
            personalization=personalization,
            weight="weight",
        )

        # Select top nodes
        sorted_nodes = sorted(pr_scores.items(), key=lambda x: x[1], reverse=True)
        selected_ids = set(node_id for node_id, _ in sorted_nodes[:max_nodes])

        # Detect shared dependencies
        external_ids = self._detect_shared_dependencies(selected_ids)
        feature_ids = selected_ids - external_ids

        # Build subgraph
        subgraph_nodes = [
            self.node_by_id[nid]
            for nid in feature_ids
            if nid in self.node_by_id
        ]
        subgraph_edges = [
            e for e in self.raw_graph["edges"]
            if e["source"] in selected_ids and e["target"] in selected_ids
        ]
        external_files = list({
            self.node_by_id[nid].get("filePath", nid)
            for nid in external_ids
            if nid in self.node_by_id
        })

        return subgraph_nodes, subgraph_edges, external_files

    def _detect_shared_dependencies(self, selected_ids: set) -> set:
        """Nodes with high in-degree in the FULL graph are shared utilities.

        A file referenced by SHARED_THRESHOLD or more other files
        is considered shared infrastructure, not feature-specific.

        Example: supabase.ts is imported by auth.ts, payments.ts, and users.ts
        → in-degree = 3 → marked as external dependency.
        """
        shared = set()
        for node_id in selected_ids:
            full_in_degree = self.G.in_degree(node_id)
            if full_in_degree >= SHARED_THRESHOLD:
                shared.add(node_id)
        return shared
```

### `devkit/core/fork/blueprint.py`

```python
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

BLUEPRINTS_DIR = Path.home() / ".devkit" / "blueprints"

class Blueprint:

    @staticmethod
    def create(
        name: str,
        feature_name: str,
        source_project: str,
        subgraph_nodes: list[dict],
        subgraph_edges: list[dict],
        external_dependencies: list[str],
        memory_facts: list[dict],
        stack_context: dict,
    ) -> Path:
        """Create a blueprint directory and save all components.

        Returns the path to the blueprint directory.
        """
        bp_dir = BLUEPRINTS_DIR / name
        bp_dir.mkdir(parents=True, exist_ok=True)

        # Subgraph JSON
        subgraph = {"nodes": subgraph_nodes, "edges": subgraph_edges}
        (bp_dir / "subgraph.json").write_text(json.dumps(subgraph, indent=2))

        # Estimate tokens (rough: 5 tokens per node summary + memory facts)
        token_estimate = len(subgraph_nodes) * 5 + sum(
            len(f["content"].split()) for f in memory_facts
        ) * 2

        # Blueprint metadata
        metadata = {
            "version": "1",
            "name": name,
            "description": f"{feature_name} pattern from {source_project}",
            "source_project": source_project,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "seed_query": feature_name,
            "seed_nodes": [n["id"] for n in subgraph_nodes[:5]],  # top 5 for reference
            "external_dependencies": external_dependencies,
            "memory_facts": memory_facts,
            "stack_context": stack_context,
            "transfer_notes": (
                f"Pattern extracted from {source_project}. "
                "Adapt implementation details to target project stack. "
                "Memory decisions transfer as-is."
            ),
            "node_count": len(subgraph_nodes),
            "edge_count": len(subgraph_edges),
            "token_estimate": token_estimate,
        }
        (bp_dir / "blueprint.json").write_text(json.dumps(metadata, indent=2))

        return bp_dir

    @staticmethod
    def load(name: str) -> dict:
        bp_dir = BLUEPRINTS_DIR / name
        if not bp_dir.exists():
            raise ValueError(f"Blueprint '{name}' not found in {BLUEPRINTS_DIR}")
        return json.loads((bp_dir / "blueprint.json").read_text())

    @staticmethod
    def load_subgraph(name: str) -> dict:
        bp_dir = BLUEPRINTS_DIR / name
        return json.loads((bp_dir / "subgraph.json").read_text())

    @staticmethod
    def list_all() -> list[dict]:
        if not BLUEPRINTS_DIR.exists():
            return []
        blueprints = []
        for bp_dir in BLUEPRINTS_DIR.iterdir():
            if bp_dir.is_dir() and (bp_dir / "blueprint.json").exists():
                meta = json.loads((bp_dir / "blueprint.json").read_text())
                blueprints.append(meta)
        return sorted(blueprints, key=lambda x: x["extracted_at"], reverse=True)

    @staticmethod
    def render_for_injection(name: str, context_only: bool = False) -> str:
        """Render blueprint as injectable context string.

        If context_only=True: inject memory facts only (no subgraph structure).
        Default: inject both subgraph summary + memory facts.
        """
        meta = Blueprint.load(name)
        parts = [f"<devkit-blueprint name='{name}' source='{meta['source_project']}'>"]

        # Memory facts (always included)
        if meta.get("memory_facts"):
            parts.append("\nDECISIONS AND PATTERNS:")
            for fact in meta["memory_facts"]:
                parts.append(f"  - [{fact['fact_type'].upper()}] {fact['content']}")

        # Stack context
        if meta.get("stack_context"):
            sc = meta["stack_context"]
            parts.append(f"\nSOURCE STACK: {sc.get('language')} / {sc.get('framework')} / {sc.get('auth_provider', 'N/A')}")
            parts.append(f"KEY PACKAGES: {', '.join(sc.get('key_packages', []))}")

        # Transfer notes
        if meta.get("transfer_notes"):
            parts.append(f"\nTRANSFER NOTES: {meta['transfer_notes']}")

        # External dependencies
        if meta.get("external_dependencies"):
            parts.append(f"\nEXTERNAL DEPENDENCIES (not included in blueprint):")
            for dep in meta["external_dependencies"]:
                parts.append(f"  - {dep}")

        if not context_only:
            # Subgraph summary (not full graph — too many tokens)
            subgraph = Blueprint.load_subgraph(name)
            parts.append(f"\nFEATURE STRUCTURE ({meta['node_count']} nodes, {meta['edge_count']} edges):")
            for node in subgraph["nodes"][:10]:  # top 10 nodes
                parts.append(f"  - [{node['type']}] {node['name']}: {node.get('summary', '')[:100]}")
            if len(subgraph["nodes"]) > 10:
                parts.append(f"  ... and {len(subgraph['nodes']) - 10} more nodes")

        parts.append("\n</devkit-blueprint>")
        parts.append(f"\nApply this {name} blueprint as context for the current task.")
        return "\n".join(parts)
```

---

## CLI Commands

```bash
# Extract blueprint
devkit fork <feature> --from <project>               # Extract and save blueprint
devkit fork auth --from swagath-central              # Example
devkit fork auth --from swagath-central --name my-auth-v2    # Custom name
devkit fork auth --from swagath-central --max-nodes 20       # Fewer nodes
devkit fork payment --from swagath-central --depth 3         # Deeper traversal

# Manage blueprints
devkit fork list                                      # List all blueprints
devkit fork inspect <blueprint-name>                  # Show blueprint contents
devkit fork delete <blueprint-name>                   # Remove blueprint

# Apply blueprint
devkit fork apply <blueprint-name>                    # Inject into current session
devkit fork apply auth-pattern --context-only         # Memory facts only (no structure)
```

### `devkit fork auth --from swagath-central` output

```
DevKit Fork — extracting 'auth' from swagath-central

Loading knowledge graph... 847 nodes, 1203 edges
Finding seed nodes for 'auth'... 6 found
  - file:src/lib/auth.ts
  - file:src/middleware/requireAuth.ts
  - function:src/lib/auth.ts:validateToken
  - function:src/middleware/requireAuth.ts:requireAuth
  - endpoint:src/api/auth/login.ts
  - endpoint:src/api/auth/logout.ts

Running personalized PageRank...
Selecting top 30 nodes by relevance...
Detecting shared dependencies...
  → file:src/lib/supabase.ts (in-degree: 14) — marked external
  → file:src/utils/logger.ts (in-degree: 8) — marked external

Loading memory facts for 'auth'...
  Found 3 relevant facts from swagath-central

Saving blueprint to ~/.devkit/blueprints/auth-pattern/
  ✓ subgraph.json (28 nodes, 41 edges)
  ✓ blueprint.json (3 memory facts, ~1,850 token estimate)

Blueprint 'auth-pattern' saved.
Run: devkit fork apply auth-pattern
```

### `devkit fork list` output

```
DevKit Blueprints
──────────────────────────────────────────
auth-pattern      from: swagath-central   28 nodes   3 facts   5d ago   ~1,850 tokens
payment-flow      from: swagath-central   19 nodes   1 fact    12d ago  ~1,200 tokens
──────────────────────────────────────────
2 blueprints available
```

---

## Open Decisions

1. **PageRank max_nodes default** — 30. Test on swagath-central auth feature. If important nodes are missing, increase to 40. If too many irrelevant nodes, decrease to 20. This is the primary tuning lever.

2. **Shared dependency threshold** — `SHARED_THRESHOLD = 3`. A file imported by 3+ other files = shared. On small projects this might be too low (everything seems shared). On large projects too high (real utilities not detected). Make it configurable: `devkit config set fork_shared_threshold 3`.

3. **Memory fact selection for blueprint** — currently grabs all facts for the project. Should filter to facts *relevant* to the feature. Either: (a) semantic search on feature name across memory, or (b) ask developer to confirm which facts to include during fork. Start with (b) — show all project facts and let developer choose.

4. **Exemplar code files** — not implemented in v1. The `exemplars/` directory is reserved for manually-added reference code. Auto-extracting exemplar code is a v2 feature.

5. **Cross-language transfer** — extracting a TypeScript blueprint and applying to a Python project works at the pattern/memory level. The subgraph structure is useful for understanding architecture but code exemplars won't transfer directly. The `transfer_notes` field should always acknowledge this.
