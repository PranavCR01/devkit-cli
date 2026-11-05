# Eval: ablation was never invoked, and the judge failed open to SAFE

## Problems
- `AblationWorker` was never instantiated and `enqueue()` had no caller, so no candidates could ever be produced.
- Even if it had been wired up, the replay sent only `messages`, dropping `system` and `tools`. Every Claude Code request would get a 400, return `""`, and score Jaccard 1.0. It could also send empty content.
- Any API or parse error in the judge returned "tie" in both orderings, which counted as **SAFE**.
- `verify` judged outputs truncated to 500 characters, with no task context and one sample per ordering.
- `eval learn` used `python -m headroom`. Port 9999 (docs) and 8787 (code) disagreed. The skill listed a nonexistent `compare` command. `save_version` was never called.

## Acceptance criteria
- [x] Judge errors produce INCONCLUSIVE, never SAFE
- [x] Replays keep system/tools; tool pairing is preserved; no empty content is sent
- [x] The cached prefix (explicit breakpoints or top-level auto caching) is never ablated
- [x] `devkit eval ablate` produces judged candidates end to end (fake client)
- [x] Port, `learn`, and skill docs are consistent
