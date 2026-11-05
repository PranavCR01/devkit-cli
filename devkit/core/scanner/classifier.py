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


def _path_tokens(path: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", path.replace("\\", "/"))
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]


def _name_hit(pattern: str, token: str) -> bool:
    if token in (pattern, pattern + "s"):
        return True
    # long patterns are specific enough to match as prefixes (database -> databases_util)
    return len(pattern) >= 5 and token.startswith(pattern)


def is_tier1(file_path: str, content: str) -> bool:
    """file_path must be relative to the scan root: matching an absolute path
    makes every file under /home/user or C:\\Users\\... look security-critical."""
    tokens = _path_tokens(file_path)
    if any(_name_hit(pat, tok) for pat in TIER1_NAME_PATTERNS for tok in tokens):
        return True
    for pkg in TIER1_IMPORT_PACKAGES:
        if f"'{pkg}'" in content or f'"{pkg}"' in content:
            return True
    for pkg in TIER1_PYTHON_IMPORTS:
        if re.search(rf"^\s*(import|from)\s+{re.escape(pkg)}\b", content, re.MULTILINE):
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
        rel = file.get("rel", file["path"])
        if is_tier3(rel):
            skipped += 1
        elif is_tier1(rel, file["content"]):
            tier1.append(file)
        else:
            tier2.append(file)
    return {"tier1": tier1, "tier2": tier2, "skipped": skipped}


class FileClassifier:
    """High-level classifier that walks a project directory."""

    def __init__(self, max_file_bytes: int | None = None) -> None:
        self.max_file_bytes = max_file_bytes or MAX_FILE_BYTES

    def classify(
        self,
        project_path: str,
        graph: dict | None = None,
        priority_paths: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Return (tier1_files, tier2_files) as path strings (as walked from project_path).

        priority_paths: project-relative paths that must be Tier 1 (e.g. graph
        nodes with high inbound degree from GraphGuide.get_tier1_files()).
        """
        files = self._collect_files(project_path)
        if graph or priority_paths:
            return self._graph_guided_tier1(files, graph or {}, priority_paths or [])
        return self._heuristic_classify(files)

    def _collect_files(self, project_path: str) -> list[dict]:
        root = Path(project_path)
        result: list[dict] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            if p.stat().st_size > self.max_file_bytes:
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            result.append({"path": str(p), "rel": p.relative_to(root).as_posix(), "content": content})
        return result

    def _heuristic_classify(self, files: list[dict]) -> tuple[list[str], list[str]]:
        result = classify_files(files)
        tier1 = [f["path"] for f in result["tier1"]]
        tier2 = [f["path"] for f in result["tier2"]]
        return tier1, tier2

    def _graph_guided_tier1(
        self, files: list[dict], graph: dict, priority_paths: list[str]
    ) -> tuple[list[str], list[str]]:
        """Graph node types (endpoint/schema/table) and high-inbound nodes are Tier 1.

        Graph filePaths are project-relative, so they are compared against each
        file's project-relative path (not the walk path).
        """
        priority = {normalize_rel(p) for p in priority_paths}
        for node in graph.get("nodes", []):
            if node.get("type") in ("endpoint", "schema", "table"):
                if fp := node.get("filePath"):
                    priority.add(normalize_rel(fp))

        tier1: list[str] = []
        tier2: list[str] = []
        for f in files:
            rel = f.get("rel", f["path"])
            if is_tier3(rel):
                continue
            if rel in priority or is_tier1(rel, f["content"]):
                tier1.append(f["path"])
            else:
                tier2.append(f["path"])
        return tier1, tier2


def normalize_rel(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/") if not re.match(r"^[A-Za-z]:/", p) else p


def to_project_relative(path: str, root: str | Path) -> str:
    """Best-effort conversion of a finding/graph path to a project-relative posix path."""
    root_p = Path(root).resolve()
    p = Path(path)
    candidates = [p] if p.is_absolute() else [Path.cwd() / p, root_p / p]
    for c in candidates:
        try:
            return c.resolve().relative_to(root_p).as_posix()
        except (ValueError, OSError):
            continue
    return normalize_rel(path)
