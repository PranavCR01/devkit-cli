# DevKit — Slice 3: Self-Improving Loop

## Overview

Slice 3 wires `/scan` findings into `/memory` automatically. No new commands. Just pipeline integration and the correction mechanism. This is the compounding value of DevKit — every scan makes future scans smarter.

**Prerequisite: Slices 1 and 2 must be fully working before starting this slice.**

---

## Goals

- Confirmed scan findings automatically stored as `fact_type=vulnerability_pattern`
- Developer dismissing a finding writes an invalidating fact (correction mechanism)
- `/search` now returns learned vulnerability patterns alongside manual decisions
- `devkit scan` shows `[M] Learned from past scan` badge on recurring patterns
- Auto-learn is opt-in via config (default OFF)

---

## Success Criteria

- Run `devkit scan` on Project A → high/critical findings stored in memory automatically
- Run `devkit scan` on Project B with similar code → same pattern surfaced with "seen before" context
- Developer runs `devkit scan . --dismiss <finding-id>` → that pattern marked invalid, not surfaced again
- `devkit search "hardcoded API key"` returns both manual facts AND past scan findings
- Auto-learn OFF by default — must be enabled via `devkit config set auto_learn true`

---

## No New Files

All changes are modifications to existing Slice 1 and Slice 2 files. No new modules needed.

---

## Changes to Existing Files

### `devkit/core/scanner/orchestrator.py`

Add after the scoring step in `ScanOrchestrator.run()`:

```python
async def run(self, path: str, mode: str, output_format: str, use_graph: bool = True) -> ScanResult:
    # ... existing scan logic (semgrep + claude + merge + score + blast radius) ...

    # NEW: Auto-store confirmed findings in memory (if enabled)
    if self.config.get("auto_learn"):
        await self._store_findings_in_memory(result)

    # NEW: Enrich findings with memory matches (always, not just when auto_learn is on)
    result.findings = self._enrich_with_memory(result.findings, path)

    return result


async def _store_findings_in_memory(self, result: ScanResult) -> None:
    """Store high-confidence findings as learned vulnerability patterns.

    Only stores critical and high severity automatically.
    Medium/low are surfaced to developer for manual confirmation via prompt.

    Uses MemoryStore.save() with source="scan" so they can be filtered separately.
    """
    if not self.memory_store:
        return

    for finding in result.findings:
        if finding.severity not in ("critical", "high"):
            continue

        content = (
            f"Vulnerability pattern: {finding.title}. "
            f"Found in {finding.file_path}. "
            f"Description: {finding.plain_english_desc}. "
            f"Fix: {finding.fix_snippet[:200]}. "
            f"OWASP: {finding.owasp_ref}. CWE: {finding.cwe_ref}."
        )

        self.memory_store.save(
            content=content,
            fact_type="vulnerability_pattern",
            project=result.project_path,
            source="scan",
        )


def _enrich_with_memory(self, findings: list[Finding], project: str) -> list[Finding]:
    """For each finding, check if a similar pattern was seen in a past scan.

    Attaches memory_match to the finding for display purposes.
    Threshold: 0.80 cosine similarity on title.
    """
    if not self.memory_store:
        return findings

    for finding in findings:
        similar = self.memory_store.search(
            query=finding.title,
            fact_types=["vulnerability_pattern"],
            limit=1,
        )
        if similar and similar[0].score > 0.80:
            finding.memory_match = similar[0]  # attach for display

    return findings
```

### `devkit/commands/scan.py`

Add `--dismiss` flag and `--save` flag:

```python
@app.command()
def scan(
    path: str = typer.Argument(".", help="Directory to scan"),
    mode: str = typer.Option("all", "--mode", help="web|api|ai|all"),
    output: str = typer.Option("text", "--output", help="text|json"),
    no_graph: bool = typer.Option(False, "--no-graph"),
    severity: str = typer.Option(None, "--severity", help="Minimum severity to show"),
    save: bool = typer.Option(False, "--save", help="Save findings to memory"),
    dismiss: str = typer.Option(None, "--dismiss", help="Dismiss a finding by ID"),
):
    # Handle dismiss before running scan
    if dismiss:
        memory_store = get_memory_store()
        memory_store.contradict(
            fact_id=dismiss,
            reason=f"Developer dismissed finding during scan on {datetime.now().isoformat()}"
        )
        typer.echo(f"✓ Finding {dismiss[:8]}... dismissed. Will not resurface in future scans.")
        return

    # ... existing scan logic ...

    # After scan: prompt for medium/low if auto_learn is on
    if config.get("auto_learn") and result.findings:
        medium_low = [f for f in result.findings if f.severity in ("medium", "low")]
        if medium_low:
            store = typer.confirm(
                f"\nStore {len(medium_low)} medium/low findings as learned patterns?",
                default=False,
            )
            if store:
                for finding in medium_low:
                    # store each medium/low finding
                    pass
```

### Output display — add `[M]` badge

In your output formatter, when a finding has `memory_match` attached:

```python
def format_finding(finding: Finding) -> str:
    memory_badge = " [M] Seen before" if hasattr(finding, "memory_match") and finding.memory_match else ""
    return f"[{finding.severity.upper()}] {finding.title}{memory_badge}\n  {finding.file_path}:{finding.line_start}"
```

---

## New CLI Behavior

### `devkit scan . --dismiss <finding-id>`

```
devkit scan . --dismiss a3f8c2b1
✓ Finding a3f8c2b1... dismissed. Will not resurface in future scans.
```

### `devkit scan .` with auto_learn ON and memory match

```
HIGH   Missing Rate Limiting on Auth Endpoint [M] Seen before
       src/api/auth.ts:45
       Anyone can attempt unlimited password guesses.
       Previously seen: sentinel (2025-04-28, confidence: 0.91)
```

### Enable auto-learn:

```bash
devkit config set auto_learn true
```

---

## Open Decisions

1. **Auto-learn default** — OFF. Developers should understand what's being stored. Turn on explicitly via config.

2. **Medium/low confirmation prompt** — shows after scan if auto_learn is on. Adds UX friction but prevents noise in memory. Start with this, remove the prompt if it's annoying in practice.

3. **Pattern deduplication** — if the same pattern fires on 3 different files in one scan, store only once (deduplicate by title before storing). Prevents memory bloat.

4. **Cross-project badge threshold** — 0.80 cosine similarity. Too low = badge shows everywhere; too high = misses real matches. Tune on real projects.

5. **`fact_type=vulnerability_pattern`** — distinct from `decision` and `pattern` so you can filter scan-learned facts separately from manually entered ones.
