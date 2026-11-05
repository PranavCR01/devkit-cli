from render import Beat, CardScene, TermScene

T = "devkit -- acme-billing (synthetic sample app)"
SCENES = [
    CardScene(
        "DevKit · open-source CLI + plugin for Claude Code",
        "Context engineering and guardrails for coding agents",
        ["Injects ranked, token-budgeted project memory when a session starts",
         "Security scan that won't grade what it couldn't analyze",
         "Measures which context is waste, and benchmarks the judge that decides",
         "Hands tasks between agents and reports AI cost per merged PR"],
        "Live runs, real Claude API, synthetic sample project. Long waits are sped up and labelled.",
        "DevKit: a Python CLI that sits next to Claude Code.", 7),
    # ── act 1: memory + session-start context ──
    TermScene("memory", T, [
        Beat("devkit memory: decisions, bugs and patterns per project, embedded locally (MiniLM) for search.", 4,
             scroll_to=r"memory save"),
        Beat("Switching workstreams saves where you left off; new facts are tagged with the active one.", 4,
             tail=True),
    ], steps=[0, 2, 4, 5]),
    TermScene("inject", T, [
        Beat("On branch feature/stripe-webhooks, the SessionStart hook ranks facts by relevance to the branch and recent commits.", 5),
        Beat("Facts are packed one at a time under the token budget; what doesn't fit is counted, not silently dropped.", 4),
    ]),
    TermScene("claude", T, [
        Beat("A real Claude Code session in this repo, with every file tool disabled.", 3, scroll_to=r"claude -p"),
        Beat("It answers from the injected memory: org_id from the session, ? placeholders, the payments workstream.", 6,
             tail=True),
    ]),
    # ── act 2: scan ──
    TermScene("app", T, [Beat("The sample: a small Flask billing API with deliberate bugs, like this f-string SQL query.", 3)]),
    TermScene("scan", T, [
        Beat("devkit scan sends line-numbered code to Claude, so every finding points at an exact line.", 4,
             scroll_to=r"DevKit Security Scan"),
        Beat("Critical: SQL injection at app.py:21, an admin export with no auth, a Stripe webhook never verified.", 5,
             scroll_to=r"CRITICAL \("),
        Beat("Blast radius comes from the project's knowledge graph: which files call the vulnerable one.", 4,
             scroll_to=r"\[4\]"),
    ]),
    TermScene("scan_badkey", T, [
        Beat("Every Claude call failed on an invalid API key. Instead of a clean 'A', DevKit reports INCOMPLETE and says why.", 5,
             scroll_to=r"DevKit Security Scan"),
    ]),
    # ── act 3: eval ──
    TermScene("ablate_plan", T, [
        Beat("A request shaped like a Claude Code turn (built for this demo); the cached prefix is never ablated.", 5),
    ], prompt="demo $ "),
    TermScene("ablate_run", T, [
        Beat("Each chunk is swapped for a placeholder and the full request is replayed, tools included.", 3),
    ], prompt="demo $ "),
    TermScene("ablate_report", T, [
        Beat("pip freeze and the test log can go (~800 tokens). Without app.py the fix changed, so it stays.", 5),
    ], prompt="demo $ "),
    TermScene("bench", T, [
        Beat("Can the judge be trusted? 120 cases from 30 seeds; degraded labels come from construction, not a model.", 5,
             scroll_to=r"Judge benchmark"),
        Beat("Held-out half, 3 runs: false SAFE 0-3.3%. The one miss, a wildcard CORS origin, is reported, not hidden.", 5,
             scroll_to=r"Judge benchmark"),
        Beat("No other type of degraded answer was ever called SAFE. n is small, so the intervals are wide.", 5,
             scroll_to=r"By operator"),
    ], prompt="demo $ "),
    # ── act 4: agents + metrics ──
    TermScene("handoff_a", T, [
        Beat("devkit handoff: agent-a claims a task with acceptance criteria, then hands it over with a note.", 4),
    ]),
    TermScene("handoff_b", T, [
        Beat("agent-b takes over and gets the brief: what's checked, the history, and where to start.", 5),
    ]),
    TermScene("handoff_recover", T, [
        Beat("A crashed agent's lease expires and recover returns its task. Handoffs and recoveries are counted.", 5,
             tail=True),
    ]),
    TermScene("metrics", T, [
        Beat("Real data: this repo's PR and the Claude Code session that built it, priced at list rates.", 4),
        Beat("AI cost per merged PR, matched by branch. Cost on branches without a PR is shown, not hidden.", 4),
    ], prompt="demo $ "),
    CardScene(
        "DevKit · results from this recording",
        "A judge blind spot, found and reported",
        ["Judge benchmark: 120 cases from 30 seeds, labels by construction; held-out half x 3 runs",
         "False SAFE 0-3.3% (95% CI up to 17%); the only miss is a wildcard CORS origin",
         "Accuracy 95-97%, order agreement 95-98%; a mislabeled case was caught and fixed",
         "Claude Code answered from injected memory with every file tool disabled"],
        "github.com/PranavCR01/devkit-cli  ·  method and limits: docs/eval/judge-bench.md",
        "Small n, honest intervals. Extend the seeds to tighten them.", 9, title_size=52),
]
