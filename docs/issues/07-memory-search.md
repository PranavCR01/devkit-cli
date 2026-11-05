# Memory & search: workstream switch was wrong; scoped search missed results

## Problems
- Switching back to an existing workstream made `main` active; workstreams saved nothing.
- Contradiction rows recorded the 0.85 constant instead of the measured similarity; `contradict(reason)` discarded the reason.
- `--project/--type` search filtered after a global KNN, so in-scope results went missing. Facts were fetched N+1.

## Acceptance criteria
- [x] Explicit active flag (migration); `switch` saves and restores note, branch, and fact ids; new facts are tagged with the active workstream
- [x] Measured similarity and reasons are persisted
- [x] Scoped search filters in SQL; facts are batch-fetched
