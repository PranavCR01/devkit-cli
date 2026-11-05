# Issues

GitHub issue creation was blocked (403: the Claude GitHub App lacks Issues permission), so the issues are kept here. Each file is ready to paste into a GitHub issue.

- [Test infrastructure, packaging, and repo hygiene](01-test-infra-packaging-hygiene.md)
- [Eval: ablation was never invoked, and the judge failed open to SAFE](02-eval-ablation-dead-judge-fails-open.md)
- [SessionStart hook and context assembly didn't deliver retrieved, budgeted context](03-sessionstart-hook-context.md)
- [Fork: path traversal in blueprint names; extraction dropped its own seeds](04-fork-path-traversal-seeds.md)
- [Scan pipeline: false grades, memory self-matches, config ignored](05-scan-pipeline-grading.md)
- [Scan coverage: classifier matched absolute paths, no Python rules, no line numbers](06-scan-coverage.md)
- [Memory & search: workstream switch was wrong; scoped search missed results](07-memory-search.md)
- [Docs credibility: README output was not real; agent prompt invited fabricated citations](08-docs-credibility.md)

## JD-driven features
- `devkit eval bench`: judge benchmark (accuracy, false-SAFE rate, order agreement)
- `devkit metrics`: PR throughput and AI cost per merged PR
- `devkit handoff`: multi-agent task ledger
