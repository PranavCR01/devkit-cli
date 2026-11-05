# Scan coverage: classifier matched absolute paths, no Python rules, no line numbers

## Problems
- Tier-1 patterns matched the full path. `user` appears in `/home/user` and `C:\\Users`, so every file became Tier 1.
- Graph-guided paths never matched; `get_tier1_files` was unused; blast radius had the same path mismatch.
- Semgrep rulesets were JS/TS only.
- Chunks had no line numbers; continuation chunks lost their file header; dedup keys never matched.

## Acceptance criteria
- [x] Classification uses relative-path words; graph paths are normalized; high-inbound nodes are included
- [x] Language rule packs are added from detected file extensions; OWASP 2025 numbering is used
- [x] Chunks are line-numbered and repeat their headers; dedup runs after path normalization
