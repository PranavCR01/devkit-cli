from __future__ import annotations
import re
from pathlib import Path

# Files whose path contains any of these substrings are always scanned (Tier 1).
TIER1_NAME_PATTERNS: list[str] = [
    "auth",
    "api",
    "route",
    "middleware",
    "supabase",
    "firebase",
    "database",
    "db",
    "server",
    "backend",
    "admin",
    "payment",
    "stripe",
    "token",
    "secret",
    "key",
    "user",
    "session",
    "jwt",
    "webhook",
    "handler",
    "controller",
]

# Files whose content contains an import/require of any of these packages are Tier 1.
TIER1_IMPORT_PACKAGES: list[str] = [
    "express",
    "fastapi",
    "@supabase/supabase-js",
    "firebase",
    "prisma",
    "drizzle",
    "stripe",
    "jsonwebtoken",
]

# Python-specific: import statements that flag a file as Tier 1.
TIER1_PYTHON_IMPORTS: list[str] = [
    "fastapi",
    "flask",
    "django",
    "sqlalchemy",
    "supabase",
    "stripe",
    "jwt",
    "passlib",
    "bcrypt",
    "cryptography",
]

TIER3_EXTENSIONS: set[str] = {".css", ".scss", ".less"}

# Regex patterns matched against the lowercased basename to skip a file.
TIER3_FILENAME_PATTERNS: list[str] = [
    r"\.d\.ts$",
    r"\.(test|spec)\.(ts|tsx|js|jsx|py)$",
    r"\.stories\.(ts|tsx|js|jsx)$",
    r"\.(config|rc)\.(ts|js|cjs|mjs)$",
    r"^(vite|tailwind|eslint|prettier|postcss|jest|babel|webpack|rollup|next|nuxt)\.config\.",
]

TIER3_PATH_FRAGMENTS: set[str] = {"/__tests__/", "/.storybook/", "/stories/"}

SKIP_DIRS: set[str] = {
    "node_modules", ".git", "dist", "build", ".next", "out",
    "coverage", ".nuxt", ".output", "vendor", "__pycache__", ".venv", "venv",
}

SKIP_FILES: set[str] = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "composer.lock", "Gemfile.lock", "Cargo.lock", "poetry.lock",
}

SKIP_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav",
    ".map",
}

MAX_FILE_BYTES = 100 * 1024   # 100 KB
TIER2_MIN_LINES = 80
TIER2_LINE_BUDGET = 8_000
MAX_CHUNK_LINES = 2_000


def is_tier3(file_path: str) -> bool:
    lower = file_path.lower().replace("\\", "/")
    basename = lower.split("/")[-1]
    ext = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""

    if ext in TIER3_EXTENSIONS | SKIP_EXTENSIONS:
        return True
    if basename in SKIP_FILES:
        return True
    if any(frag in lower for frag in TIER3_PATH_FRAGMENTS):
        return True
    for pattern in TIER3_FILENAME_PATTERNS:
        if re.search(pattern, basename, re.IGNORECASE):
            return True
    return False


def is_tier1(file_path: str, content: str) -> bool:
    lower = file_path.lower().replace("\\", "/")
    if any(pattern in lower for pattern in TIER1_NAME_PATTERNS):
        return True
    for pkg in TIER1_IMPORT_PACKAGES:
        if f"'{pkg}'" in content or f'"{pkg}"' in content:
            return True
    for pkg in TIER1_PYTHON_IMPORTS:
        if f"import {pkg}" in content or f"from {pkg}" in content:
            return True
    return False


def classify_files(
    files: list[dict],  # each dict: {"path": str, "content": str}
) -> dict:
    """Returns {"tier1": [...], "tier2": [...], "skipped": int}."""
    tier1: list[dict] = []
    tier2: list[dict] = []
    skipped = 0
    for file in files:
        if is_tier3(file["path"]):
            skipped += 1
        elif is_tier1(file["path"], file["content"]):
            tier1.append(file)
        else:
            tier2.append(file)
    return {"tier1": tier1, "tier2": tier2, "skipped": skipped}


class FileClassifier:
    """High-level classifier that walks a project directory."""

    def classify(
        self,
        project_path: str,
        graph: dict | None = None,
    ) -> tuple[list[str], list[str]]:
        """Return (tier1_files, tier2_files) as lists of absolute path strings."""
        files = self._collect_files(project_path)
        if graph:
            return self._graph_guided_tier1(files, graph)
        return self._heuristic_classify(files)

    def _collect_files(self, project_path: str) -> list[dict]:
        root = Path(project_path)
        result: list[dict] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            result.append({"path": str(p), "content": content})
        return result

    def _heuristic_classify(self, files: list[dict]) -> tuple[list[str], list[str]]:
        result = classify_files(files)
        tier1 = [f["path"] for f in result["tier1"]]
        tier2 = [f["path"] for f in result["tier2"]]
        return tier1, tier2

    def _graph_guided_tier1(
        self, files: list[dict], graph: dict
    ) -> tuple[list[str], list[str]]:
        """Use knowledge graph node types and layers to identify high-priority files."""
        priority_paths: set[str] = set()
        for node in graph.get("nodes", []):
            if node.get("type") in ("endpoint", "schema", "table"):
                if fp := node.get("filePath"):
                    priority_paths.add(fp)

        tier1: list[str] = []
        tier2: list[str] = []
        for f in files:
            if is_tier3(f["path"]):
                continue
            if f["path"] in priority_paths or is_tier1(f["path"], f["content"]):
                tier1.append(f["path"])
            else:
                tier2.append(f["path"])
        return tier1, tier2
