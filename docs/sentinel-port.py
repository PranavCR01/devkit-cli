  # ============================================================
  # SECTION 1: Claude prompts
  # ============================================================

  SECURITY_SYSTEM_PROMPT = """You are an expert application security engineer specializing in AI-generated code vulnerabilities. You understand
  OWASP Top 10, CWE Top 25, and the specific anti-patterns LLMs introduce.

  Analyze the provided code for these 18 AI anti-pattern categories:
  1. Hardcoded secrets/API keys (incl. placeholder strings like 'supersecretkey', 'your-secret-here', 'changeme')
  2. Missing server-side authentication on API routes
  3. Client-side-only authorization checks
  4. SQL/NoSQL injection via string interpolation or template literals
  5. Insecure Direct Object References (sequential IDs, no ownership check)
  6. Over-permissive CORS (Access-Control-Allow-Origin: *)
  7. Missing Supabase/Firebase Row Level Security
  8. Missing input validation on user-facing endpoints
  9. Missing CSRF protection on state-changing routes
  10. Missing rate limiting on authentication/payment endpoints
  11. Exposed environment variables in client bundles (NEXT_PUBLIC_ leaks, VITE_ leaks)
  12. Hallucinated or nonexistent package dependencies
  13. Deprecated or known-vulnerable dependency versions
  14. Missing security headers (CSP, HSTS, X-Frame-Options)
  15. Insecure session management (weak tokens, no expiry)
  16. Dangerous functions with unsanitized input (eval, exec, os.system)
  17. Debug mode or verbose error messages left enabled
  18. Missing HTTPS enforcement or insecure cookie configuration

  Also check for general code quality issues:
  - Architecture: separation of concerns, single responsibility
  - DRY violations: duplicated logic
  - Error handling: empty catch blocks, unhandled promises
  - Complexity: overly complex functions

  IMPORTANT — Many apps use Supabase directly from the frontend with no separate backend. For these apps pay special attention to:
  - Direct table queries with no row-level filtering: .from('table').select('*') with no .eq('user_id', userId) — any authenticated user can
  read all rows if RLS is disabled
  - Auth checks done only on the client: if (user) { ... } with no server enforcement — easily bypassed
  - Supabase anon key used for operations that should be admin-only
  - Missing .eq('user_id', session.user.id) on insert/update/delete — users can mutate other users' data
  - Relying on frontend routing (/admin, /dashboard) as an access control mechanism
  - Storage bucket access with no RLS policies

  Respond ONLY with valid JSON in this exact format — no markdown, no explanation:
  {
    "findings": [
      {
        "category": "security" | "quality" | "ai_antipattern",
        "severity": "critical" | "high" | "medium" | "low" | "info",
        "title": "Short plain-English title (max 60 chars)",
        "plain_english_desc": "What this means for the app in 1-2 plain sentences",
        "business_impact": "What could happen if this is exploited",
        "fix_snippet": "The corrected code snippet",
        "file_path": "relative/path/to/file",
        "line_start": 42,
        "owasp_ref": "A01:2025" | null,
        "cwe_ref": "CWE-89" | null
      }
    ]
  }
  If no issues found, return: {"findings": []}"""

  EDUCATION_SYSTEM_PROMPT = """You are a security educator explaining vulnerabilities to non-technical builders.
  Given a security finding, explain it in an educational way.
  Respond ONLY with valid JSON:
  {
    "why_it_happens": "Why AI tools generate this pattern (2-3 sentences)",
    "real_world_example": "A real breach caused by this vulnerability with company name and year",
    "learn_more_links": ["https://owasp.org/..."]
  }"""


  # ============================================================
  # SECTION 2: Tier 1 file classification patterns
  # ============================================================

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

  # Extensions and filename patterns that are always skipped (Tier 3 — never scanned).
  TIER3_EXTENSIONS: set[str] = {".css", ".scss", ".less"}

  TIER3_FILENAME_PATTERNS = [
      lambda name: name.endswith(".d.ts"),
      lambda name: bool(__import__("re").search(r"\.(test|spec)\.(ts|tsx|js|jsx)$", name)),
      lambda name: bool(__import__("re").search(r"\.stories\.(ts|tsx|js|jsx)$", name)),
      lambda name: bool(__import__("re").search(r"\.(config|rc)\.(ts|js|cjs|mjs)$", name)),
      lambda name: bool(__import__("re").match(
          r"^(vite|tailwind|eslint|prettier|postcss|jest|babel|webpack|rollup|next|nuxt)\.config\.",
          name, __import__("re").IGNORECASE,
      )),
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
      import re
      lower = file_path.lower().replace("\\", "/")
      basename = lower.split("/")[-1]
      ext = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""

      if ext in TIER3_EXTENSIONS:
          return True
      if basename.endswith(".d.ts"):
          return True
      if re.search(r"\.(test|spec)\.(ts|tsx|js|jsx)$", basename):
          return True
      if re.search(r"\.stories\.(ts|tsx|js|jsx)$", basename):
          return True
      if re.search(r"\.(config|rc)\.(ts|js|cjs|mjs)$", basename):
          return True
      if re.match(
          r"^(vite|tailwind|eslint|prettier|postcss|jest|babel|webpack|rollup|next|nuxt)\.config\.",
          basename, re.IGNORECASE,
      ):
          return True
      if any(frag in lower for frag in TIER3_PATH_FRAGMENTS):
          return True
      return False


  def is_tier1(file_path: str, content: str) -> bool:
      lower = file_path.lower().replace("\\", "/")
      if any(pattern in lower for pattern in TIER1_NAME_PATTERNS):
          return True
      for pkg in TIER1_IMPORT_PACKAGES:
          if f"'{pkg}'" in content or f'"{pkg}"' in content:
              return True
      return False


  def classify_files(
      files: list[dict],  # each dict: {"path": str, "content": str}
  ) -> dict:
      """
      Returns {"tier1": [...], "tier2": [...], "skipped": int}
      where tier1/tier2 items are the original dicts.
      """
      tier1, tier2 = [], []
      skipped = 0
      for file in files:
          if is_tier3(file["path"]):
              skipped += 1
          elif is_tier1(file["path"], file["content"]):
              tier1.append(file)
          else:
              tier2.append(file)
      return {"tier1": tier1, "tier2": tier2, "skipped": skipped}


  # ============================================================
  # SECTION 3: Scoring math
  # ============================================================

  # Security score: start 100, subtract per security/ai_antipattern finding severity.
  SECURITY_DEDUCTIONS: dict[str, int] = {
      "critical": 25,
      "high":     10,
      "medium":    3,
      "low":       1,
      "info":      0,
  }

  # Quality score: same deduction table applied to quality-category findings only.
  QUALITY_DEDUCTIONS: dict[str, int] = {
      "high":   10,
      "medium":  5,
      "low":     2,
  }

  # Grade thresholds applied to the combined weighted score.
  GRADE_THRESHOLDS: list[tuple[float, str]] = [
      (90.0, "A"),
      (75.0, "B"),
      (60.0, "C"),
      (45.0, "D"),
      (0.0,  "F"),
  ]

  # Combined score = security_score * 0.7 + quality_score * 0.3
  SECURITY_WEIGHT = 0.7
  QUALITY_WEIGHT  = 0.3


  def calculate_security_score(findings: list[dict]) -> int:
      deductions = sum(
          SECURITY_DEDUCTIONS.get(f["severity"], 0)
          for f in findings
          if f.get("category") != "quality"
      )
      return max(0, 100 - deductions)


  def calculate_quality_score(findings: list[dict]) -> int:
      deductions = sum(
          QUALITY_DEDUCTIONS.get(f["severity"], 0)
          for f in findings
          if f.get("category") == "quality"
      )
      return max(0, 100 - deductions)


  def calculate_grade(security_score: int, quality_score: int) -> str:
      combined = security_score * SECURITY_WEIGHT + quality_score * QUALITY_WEIGHT
      for threshold, grade in GRADE_THRESHOLDS:
          if combined >= threshold:
              return grade
      return "F"


  # ============================================================
  # SECTION 4: Semgrep JSON output parsing
  # ============================================================
  #
  # Semgrep --json output shape accessed by the parser:
  #
  # {
  #   "results": [
  #     {
  #       "check_id": str,          # e.g. "python.lang.security.audit.eval-detected"
  #       "path":     str,          # relative file path
  #       "start":  { "line": int },
  #       "end":    { "line": int },
  #       "extra":  {
  #         "message":  str,        # human-readable description
  #         "severity": str         # "ERROR" | "WARNING" | "INFO" (uppercase)
  #       }
  #     }
  #   ]
  # }
  #
  # Semgrep exits with code 1 when findings exist (not an error);
  # stdout is still valid JSON in that case.

  from __future__ import annotations
  import json
  import re
  from typing import Literal

  Severity = Literal["critical", "high", "medium", "low", "info"]

  SEMGREP_SEVERITY_MAP: dict[str, Severity] = {
      "ERROR":   "critical",
      "WARNING": "high",
      "INFO":    "medium",
  }


  def _map_severity(semgrep_severity: str) -> Severity:
      return SEMGREP_SEVERITY_MAP.get(semgrep_severity.upper(), "low")


  def _map_refs(check_id: str) -> tuple[str | None, str | None]:
      """Returns (owasp_ref, cwe_ref) inferred from the check_id string."""
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
      Parses Semgrep --json stdout into a list of normalized Finding dicts.
      Returns [] on parse error or empty results.

      Each returned dict matches the internal Finding schema:
          category:           "security"
          severity:           "critical" | "high" | "medium" | "low" | "info"
          title:              str   (check_id)
          plain_english_desc: str   (semgrep message)
          business_impact:    ""    (empty — Claude enriches this later)
          fix_snippet:        ""    (empty — Claude enriches this later)
          file_path:          str
          line_start:         int
          line_end:           int
          owasp_ref:          str | None
          cwe_ref:            str | None
          source:             "semgrep"
      """
      try:
          parsed = json.loads(stdout)
      except json.JSONDecodeError:
          return []

      results = parsed.get("results", [])
      seen: set[str] = set()
      findings: list[dict] = []

      for result in results:
          check_id  = result.get("check_id", "")
          path      = result.get("path", "")
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

  A few things worth flagging for the Python port:

  Section 2 — TIER1_IMPORT_PACKAGES: The TS version searches for '@supabase/supabase-js' and "@supabase/supabase-js" (quote-delimited strings
  in JS/TS source). In Python source files the equivalent pattern would be different (import supabase, from supabase import). You'll likely
  want a separate Python-specific import pattern list alongside this one.

  Section 3 — calculate_grade: The GRADE_THRESHOLDS list is ordered highest-first and returns on the first match, so the final "F" entry at
  threshold 0.0 is effectively a catch-all. max(0, 100 - deductions) already floors at zero so the "F" branch always fires rather than
  requiring a separate check.

  Section 4 — Semgrep exit code: Semgrep exits 1 when findings exist (not a crash). In Python you'll want to catch
  subprocess.CalledProcessError and check err.stdout rather than treating non-zero exit as a fatal error — same logic as the TS version's
  error.stdout && error.stdout.trim().startsWith('{') check.