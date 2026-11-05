# SessionStart hook and context assembly didn't deliver retrieved, budgeted context

## Problems
- `hooks/hooks.json` lacked the required top-level `"hooks"` wrapper, so the plugin hook failed to load. It also called a different command than the hook `devkit init` wrote, and `init` registered nothing.
- `context inject` dumped the 50 newest facts with no hybrid retrieval.
- The budget dropped whole items, and all facts went in as one item, so an over-budget project got **nothing** injected.
- `context add/build` only printed; nothing consumed the assembled session.
- Blueprint items never loaded, because the code called `read_text()` on a directory.

## Acceptance criteria
- [x] Plugin schema is valid; one hook command; `init --register-hook` is idempotent
- [x] RRF over hybrid search, recency, and type; per-fact packing; output under the hook character cap
- [x] Pinned items from `add/build` are injected
- [x] Blueprint items render
