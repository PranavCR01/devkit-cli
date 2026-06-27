from __future__ import annotations
import json
import subprocess
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "info"]

RULESETS: dict[str, list[str]] = {
    "web": ["p/owasp-top-ten", "p/javascript", "p/typescript", "p/react", "p/nodejs"],
    "api": ["p/owasp-top-ten", "p/javascript", "p/typescript", "p/nodejs"],
    "ai":  ["p/owasp-top-ten", "p/secrets"],
    "all": ["p/owasp-top-ten", "p/secrets", "p/javascript", "p/typescript", "p/react", "p/nodejs"],
}

SEMGREP_SEVERITY_MAP: dict[str, Severity] = {
    "ERROR":   "critical",
    "WARNING": "high",
    "INFO":    "medium",
}


def _map_severity(semgrep_severity: str) -> Severity:
    return SEMGREP_SEVERITY_MAP.get(semgrep_severity.upper(), "low")


def _map_refs(check_id: str) -> tuple[str | None, str | None]:
    """Infer (owasp_ref, cwe_ref) from the check_id string."""
    cid = check_id.lower()
    if "sql" in cid:
        return ("A03:2025", "CWE-89")
    if "xss" in cid:
        return ("A03:2025", "CWE-79")
    if any(k in cid for k in ("secret", "key", "token")):
        return ("A02:2025", "CWE-798")
    if "auth" in cid:
        return ("A01:2025", "CWE-306")
    if "injection" in cid:
        return ("A03:2025", "CWE-74")
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
        configs = self._get_configs(mode)
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

    def _get_configs(self, mode: str) -> list[str]:
        return RULESETS.get(mode, RULESETS["all"])

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
