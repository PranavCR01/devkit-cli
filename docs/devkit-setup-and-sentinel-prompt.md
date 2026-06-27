# DevKit — Claude Code Setup Prompt

## Answer to "Are we repurposing Sentinel?"

**No. Keep Sentinel exactly as-is.**

Sentinel is a live portfolio project at swagath-central.vercel.app with a deployed frontend, Railway backend, and Supabase DB. It demonstrates a different skill set (full-stack SaaS, SSE streaming, multi-tier scanning). Do not touch it.

DevKit is a new Python CLI repo: `PranavCR01/devkit-cli`.

---

## What to Port from Sentinel (Logic Only, Not Code)

Run this prompt in the **Sentinel Claude Code chat** to extract reusable logic:

```
I need to port specific logic from this TypeScript codebase to a new Python CLI project.
Please extract and output the following as Python-compatible content (rewrite in Python,
do NOT copy TypeScript):

1. SECURITY_SYSTEM_PROMPT from backend/src/services/claude/prompts.ts
   Output as a Python string. Include ALL 18 rules exactly as written.
   Also output the education prompt from the same file.

2. Tier 1 file classification patterns from backend/src/services/claude/analyzer.ts
   classifyFiles() function — list all string patterns used to identify Tier 1 files
   (auth, api, route, etc.). Output as a Python list of strings.

3. Scoring math from backend/src/services/scanner/scorer.ts
   - Severity deduction values (critical=?, high=?, medium=?, low=?)
   - Grade thresholds (what combined score maps to A/B/C/D/F?)
   - The weight formula (security × ? + quality × ?)
   Output as Python constants and a calculate_grade() function.

4. Semgrep JSON output parsing from backend/src/services/semgrep/parser.ts
   - What fields does Semgrep's JSON output have that you access?
   - How do you map Semgrep severity to your internal severity levels?
   Output as Python field names and a parse_semgrep_output() function stub.

Label each section clearly. Output only Python code, no TypeScript.
```

Save the output — you'll paste it into DevKit's `devkit/core/scanner/prompts.py`,
`devkit/core/scanner/classifier.py`, `devkit/core/scanner/scorer.py`, and
`devkit/core/scanner/semgrep_runner.py` during Slice 1.

---

## DevKit Initialization Prompt for Claude Code

Use this prompt to start the **DevKit Claude Code session** (new repo, fresh session):

```
I'm building DevKit — a local-first Python CLI developer tool with 6 commands:
/scan (security), /memory (temporal context), /search (cross-project), 
/context (discovery), /fork (feature transplanting), /eval (token optimization).

Reference documents:
- slice-1-foundation-scan.md — Slice 1 spec (start here)
- slice-2-memory-search.md — Slice 2 spec
- slices-3-to-7.md — Slices 3-7 specs

Before writing any code:
1. Read slice-1-foundation-scan.md completely
2. Create the full directory structure as specified
3. Create pyproject.toml with the dependencies listed
4. Create the CLAUDE.md for this repo (content in slice-1-foundation-scan.md)
5. Present the complete plan with all files you will create, then wait for approval

Rules:
- Always /plan before any code
- Show exact before/after diffs for any file changes
- Never write to ~/.devkit/ without explicit approval
- Keep CLAUDE.md under 150 lines
- All Claude API calls must use cache_control on stable system prompts
```

---

## Repo Initialization Commands

Run these in your terminal to create the DevKit repo:

```bash
# Create new repo
mkdir devkit-cli
cd devkit-cli
git init
git remote add origin https://github.com/PranavCR01/devkit-cli.git

# Create directory structure
mkdir -p devkit/commands
mkdir -p devkit/core/scanner
mkdir -p devkit/core/memory
mkdir -p devkit/core/search
mkdir -p devkit/core/context
mkdir -p devkit/core/fork
mkdir -p devkit/core/eval
mkdir -p devkit/utils
mkdir -p tests
mkdir -p .claude-plugin
mkdir -p skills/scan skills/memory skills/search skills/context skills/fork skills/eval
mkdir -p agents
mkdir -p hooks

# Create __init__.py files
touch devkit/__init__.py
touch devkit/commands/__init__.py
touch devkit/core/__init__.py
touch devkit/core/scanner/__init__.py
touch devkit/core/memory/__init__.py
touch devkit/core/search/__init__.py
touch devkit/core/context/__init__.py
touch devkit/core/fork/__init__.py
touch devkit/core/eval/__init__.py
touch devkit/utils/__init__.py

# Install in development mode after creating pyproject.toml
pip install -e ".[memory,graph,eval]"
```

---

## Slice Build Order

Build strictly in order. Do not start Slice N+1 until Slice N is working end-to-end.

| Slice | Command | Key Validation |
|-------|---------|----------------|
| 1 | `/scan` | `devkit scan .` runs on real codebase, produces findings |
| 2 | `/memory` + `/search` | `devkit memory save` + `devkit search` works cross-project |
| 3 | Self-improving loop | Scan findings appear in search results |
| 4 | `/context` | `devkit context list` shows all items, injection works in Claude Code |
| 5 | `/fork` | Blueprint extracted from swagath-central, applied to new project |
| 6 | `/eval` | Proxy running, heuristics firing, at least one suggestion verified |
| 7 | Plugin | `/devkit:scan` works inside Claude Code |

---

## Known Gotchas (from Research)

These are documented failure modes to avoid:

1. **Semgrep on Windows** — pip wheels may fail. Have Docker fallback ready.
2. **FalkorDB Lite** — does NOT exist as embedded. Full server required. We use SQLite in v1.
3. **Plugin `SessionStart` hook + additionalContext** — known bug #16538 where context doesn't surface. Test early. Fallback: echo to stdout.
4. **Cache breakpoint interaction** — if proxy removes content BEFORE a cache_control marker, net cost can INCREASE (cache miss + 1.25x write). Never touch cached prefix.
5. **tool_use IDs** — proxy must preserve every `id` field in message content. Dropping them breaks multi-turn tool calling.
6. **Graphiti RRF** — uses rank_const=1, not k=60. Non-standard. If you use Graphiti later, note that top results score dramatically higher than tail.
7. **Entity extraction docstrings** — Graphiti's extraction LLM only sees the class docstring, not field names. Write detailed docstrings on all custom entity types.
8. **Understand Anything is TypeScript** — cannot import directly. Shell out to their scripts or reimplement in Python using py-tree-sitter with WASM grammars.
