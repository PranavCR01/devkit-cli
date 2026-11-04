from __future__ import annotations
import json
import os
import subprocess
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "info"]

# Mode-level packs; language packs are added for languages actually present.
RULESETS: dict[str, list[str]] = {
    "web": ["p/owasp-top-ten"],
    "api": ["p/owasp-top-ten"],
    "ai":  ["p/owasp-top-ten", "p/secrets"],
    "all": ["p/owasp-top-ten", "p/secrets"],
}

LANGUAGE_RULESETS: dict[str, list[str]] = {
    "javascript": ["p/javascript", "p/nodejs"],
    "typescript": ["p/typescript", "p/nodejs"],
    "react": ["p/react"],
    "python": ["p/python"],
    "go": ["p/golang"],
    "java": ["p/java"],
    "ruby": ["p/ruby"],
    "php": ["p/php"],
    "csharp": ["p/csharp"],
}

_EXT_LANG = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "react",
    ".ts": "typescript", ".tsx": "react", ".py": "python", ".go": "go", ".java": "java",
    ".rb": "ruby", ".php": "php", ".cs": "csharp",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "vendor", "__pycache__", ".venv", "venv"}


def detect_languages(path: str, max_files: int = 20000) -> set[str]:
    langs: set[str] = set()
    seen = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            seen += 1
            ext = os.path.splitext(name)[1].lower()
            if ext in _EXT_LANG:
                langs.add(_EXT_LANG[ext])
            if seen >= max_files:
                return langs
    if "react" in langs:
        langs.add("javascript")
    return langs


def rulesets_for(mode: str, languages: set[str]) -> list[str]:
    configs = list(RULESETS.get(mode, RULESETS["all"]))
    for lang in sorted(languages):
        if mode == "ai" and lang == "react":
            continue
        for rs in LANGUAGE_RULESETS.get(lang, []):
            if rs not in configs:
                configs.append(rs)
    return configs


SEMGREP_SEVERITY_MAP: dict[str, Severity] = {
    "ERROR":   "critical",
    "WARNING": "high",
    "INFO":    "medium",
}


def _map_severity(semgrep_severity: str) -> Severity:
    return SEMGREP_SEVERITY_MAP.get(semgrep_severity.upper(), "low")


def _map_refs(check_id: str) -> tuple[str | None, str | None]:
    """Infer (owasp_ref, cwe_ref) from the check_id string (OWASP Top 10 2025 numbering)."""
    cid = check_id.lower()
    if "sql" in cid:
        return ("A05:2025", "CWE-89")
    if "xss" in cid:
        return ("A05:2025", "CWE-79")
    if any(k in cid for k in ("secret", "api-key", "apikey", "hardcoded", "token")):
        return ("A07:2025", "CWE-798")
    if "auth" in cid:
        return ("A07:2025", "CWE-306")
    if "injection" in cid or "exec" in cid:
        return ("A05:2025", "CWE-74")
    return (None, None)


def parse_semgrep_output(stdout: str) -> list[dict]:
    """
    Parse Semgrep --json stdout into normalized finding dicts.

    Semgrep exits with code 1 when findings exist — that is not an error.
    Callers should pass stdout regardless of exit code and check that it
    starts with '{' before calling this function.
    """
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    results = parsed.get("results", [])
    seen: set[str] = set()
    findings: list[dict] = []

    for result in results:
        check_id   = result.get("check_id", "")
        path       = result.get("path", "")
        line_start = result.get("start", {}).get("line", 1)
        line_end   = result.get("end",   {}).get("line", line_start)
        message    = result.get("extra", {}).get("message", "")
        severity   = result.get("extra", {}).get("severity", "")

        key = f"{path}:{line_start}:{check_id}"
        if key in seen:
            continue
        seen.add(key)

        owasp_ref, cwe_ref = _map_refs(check_id)

        findings.append({
            "category":           "security",
            "severity":           _map_severity(severity),
            "title":              check_id,
            "plain_english_desc": message,
            "business_impact":    "",
            "fix_snippet":        "",
            "file_path":          path,
            "line_start":         line_start,
            "line_end":           line_end,
            "owasp_ref":          owasp_ref,
            "cwe_ref":            cwe_ref,
            "source":             "semgrep",
        })

    return findings


class SemgrepRunner:
    def run(self, path: str, mode: str, timeout: int = 120) -> list[dict]:
        """Run semgrep as a subprocess and return parsed findings."""
        configs = self._get_configs(mode, path)
        cmd = ["semgrep", "--json", "--quiet", "--timeout", str(timeout)]
        for config in configs:
            cmd += ["--config", config]
        cmd.append(path)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "semgrep not found on PATH. Install it: pip install semgrep"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"semgrep timed out after {timeout + 10}s")

        # Exit code 1 = findings found (not a crash); stdout is still valid JSON.
        stdout = result.stdout.strip()
        if not stdout.startswith("{"):
            return []

        return parse_semgrep_output(stdout)

    def _get_configs(self, mode: str, path: str = ".") -> list[str]:
        return rulesets_for(mode, detect_languages(path))

    def check_installed(self) -> bool:
        """Return True if semgrep is available on PATH."""
        try:
            subprocess.run(
                ["semgrep", "--version"],
                capture_output=True,
                timeout=10,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
