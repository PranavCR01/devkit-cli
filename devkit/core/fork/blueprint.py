import json
from datetime import datetime, timezone
from pathlib import Path

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

        subgraph = {"nodes": subgraph_nodes, "edges": subgraph_edges}
        (bp_dir / "subgraph.json").write_text(
            json.dumps(subgraph, indent=2), encoding="utf-8"
        )

        token_estimate = len(subgraph_nodes) * 5 + sum(
            len(f["content"].split()) for f in memory_facts
        ) * 2

        metadata = {
            "version": "1",
            "name": name,
            "description": f"{feature_name} pattern from {source_project}",
            "source_project": source_project,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "seed_query": feature_name,
            "seed_nodes": [n["id"] for n in subgraph_nodes[:5]],
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
        (bp_dir / "blueprint.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        return bp_dir

    @staticmethod
    def load(name: str) -> dict:
        bp_dir = BLUEPRINTS_DIR / name
        if not bp_dir.exists():
            raise ValueError(f"Blueprint '{name}' not found in {BLUEPRINTS_DIR}")
        return json.loads((bp_dir / "blueprint.json").read_text(encoding="utf-8"))

    @staticmethod
    def load_subgraph(name: str) -> dict:
        bp_dir = BLUEPRINTS_DIR / name
        return json.loads((bp_dir / "subgraph.json").read_text(encoding="utf-8"))

    @staticmethod
    def list_all() -> list[dict]:
        if not BLUEPRINTS_DIR.exists():
            return []
        blueprints = []
        for bp_dir in BLUEPRINTS_DIR.iterdir():
            if bp_dir.is_dir() and (bp_dir / "blueprint.json").exists():
                try:
                    meta = json.loads(
                        (bp_dir / "blueprint.json").read_text(encoding="utf-8")
                    )
                    blueprints.append(meta)
                except Exception:
                    pass
        return sorted(blueprints, key=lambda x: x.get("extracted_at", ""), reverse=True)

    @staticmethod
    def render_for_injection(name: str, context_only: bool = False) -> str:
        """Render blueprint as injectable context string.

        context_only=True: memory facts only (no subgraph structure).
        Default: both subgraph summary + memory facts.
        """
        meta = Blueprint.load(name)
        parts = [f"<devkit-blueprint name='{name}' source='{meta['source_project']}'>"]

        if meta.get("memory_facts"):
            parts.append("\nDECISIONS AND PATTERNS:")
            for fact in meta["memory_facts"]:
                parts.append(f"  - [{fact['fact_type'].upper()}] {fact['content']}")

        if meta.get("stack_context"):
            sc = meta["stack_context"]
            lang = sc.get("language", "")
            fw = sc.get("framework", "")
            auth = sc.get("auth_provider", "")
            stack_line = " / ".join(filter(None, [lang, fw, auth]))
            if stack_line:
                parts.append(f"\nSOURCE STACK: {stack_line}")
            pkgs = sc.get("key_packages", [])
            if pkgs:
                parts.append(f"KEY PACKAGES: {', '.join(pkgs)}")

        if meta.get("transfer_notes"):
            parts.append(f"\nTRANSFER NOTES: {meta['transfer_notes']}")

        if meta.get("external_dependencies"):
            parts.append("\nEXTERNAL DEPENDENCIES (not included in blueprint):")
            for dep in meta["external_dependencies"]:
                parts.append(f"  - {dep}")

        if not context_only:
            subgraph = Blueprint.load_subgraph(name)
            node_count = meta["node_count"]
            edge_count = meta["edge_count"]
            parts.append(f"\nFEATURE STRUCTURE ({node_count} nodes, {edge_count} edges):")
            for node in subgraph["nodes"][:10]:
                ntype = node.get("type", "")
                nname = node.get("name", node.get("id", ""))
                summary = (node.get("summary") or "")[:100]
                parts.append(f"  - [{ntype}] {nname}: {summary}")
            if len(subgraph["nodes"]) > 10:
                parts.append(f"  ... and {len(subgraph['nodes']) - 10} more nodes")

        parts.append("\n</devkit-blueprint>")
        parts.append(f"\nApply this {name} blueprint as context for the current task.")
        return "\n".join(parts)
