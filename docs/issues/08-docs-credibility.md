# Docs credibility: README output was not real; agent prompt invited fabricated citations

## Problems
- The README "smoke test" cited a nonexistent fixture and a nonexistent `shell=True`, and showed impossible RRF scores (0.94).
- The README claimed `init` registers the hook, `pytest tests/` works, background 20% sampling, and port 9999. None of these were true.
- `scan-triage` asked for "a real breach (company + year)".
- `plugin.json` used `components` and a string `author`; skill names were double-namespaced (`devkit:scan`); the scan skill ran a nonexistent `--version` flag.

## Acceptance criteria
- [x] README examples come from real runs; a docs test checks that every documented command and flag exists
- [x] The agent prompt says "No verified example" instead of inventing one
- [x] The plugin manifest and skill frontmatter match the documented schema; `--version` exists
