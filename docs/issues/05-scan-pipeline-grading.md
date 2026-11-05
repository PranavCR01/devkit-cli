# Scan pipeline: false grades, memory self-matches, config ignored

## Problems
- When all Claude chunks failed, the scan reported **Grade A, 100/100**. Concurrency was unbounded, and malformed or truncated JSON was silently dropped.
- Findings were stored before enrichment, so each one matched itself and showed "[M] Seen before" on its first scan. Facts were stored under the absolute path instead of the project name.
- The `semgrep_timeout`, `scan_max_file_size_kb`, and `scan_tier2_line_cap` settings were ignored.
- `--no-claude` and `--dismiss` required an API key; a missing semgrep produced a traceback.
- Output contained non-ASCII characters (crashes cp1252).

## Acceptance criteria
- [x] Partial scans show INCOMPLETE and are not recorded in the manifest
- [x] Enrichment runs before storage; facts use the project name
- [x] Config is honored; the key is not required for `--no-claude`; output is ASCII
