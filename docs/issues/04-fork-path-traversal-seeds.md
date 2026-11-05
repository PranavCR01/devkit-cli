# Fork: path traversal in blueprint names; extraction dropped its own seeds

## Problems
- **Security:** `devkit fork delete ..` called `shutil.rmtree` on `~/.devkit`. `--name ../x` wrote outside `blueprints/`.
- Seeds with in-degree >= 3 were marked external and removed from their own blueprint.
- `seed_nodes` stored arbitrary nodes; tag matching was case-sensitive; `auth` matched `author`.
- The `[graph]` extra lacked scipy, so `nx.pagerank` crashed.

## Acceptance criteria
- [x] Names are validated everywhere (`blueprint_dir`)
- [x] Seeds are never marked external; actual seeds are recorded; matching is word-boundary
- [x] scipy is added to `[graph]`
