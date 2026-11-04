"""Delivery throughput + AI cost per merged PR.

PR data:   `gh pr list --json ...` (live) or a saved JSON export (--prs file).
AI usage:  Claude Code session transcripts (~/.claude/projects/*/*.jsonl).

Attribution: each assistant message's token usage is attributed to the git
branch recorded on that transcript line, and a branch to the PR whose head
ref it is. The transcript line format is internal to Claude Code and not a
stable API, so parsing is defensive and anything that can't be attributed is
reported as "unattributed" rather than silently dropped. Costs are estimates
from a list-price table (override with --prices).
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# USD per million tokens: (input, output, cache_read). Cache writes bill at
# 1.25x input (5-minute TTL). Prefix match on model id, longest prefix wins.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4": (1.0, 5.0, 0.10),
}
CACHE_WRITE_MULT = 1.25


def price_for(model: str, prices: dict = PRICES) -> tuple[float, float, float] | None:
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return prices[best] if best else None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class UsageRecord:
    timestamp: datetime
    branch: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    session_id: str | None
    cwd: str | None
    cost_usd: float | None


@dataclass
class PullRequest:
    number: int
    title: str
    branch: str
    created_at: datetime
    merged_at: datetime | None
    state: str


# ── loading ──────────────────────────────────────────────────────────────────

def load_usage(paths: Iterable[Path], prices: dict = PRICES,
               project_filter: str | None = None) -> tuple[list[UsageRecord], int]:
    """Parse transcript files. Returns (records, lines_skipped).

    Assistant messages can appear on several lines (one per content block)
    with the same message id and usage; each message id is counted once.
    """
    records: list[UsageRecord] = []
    seen: set[str] = set()
    skipped = 0
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            msg = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            mid = msg.get("id") or entry.get("uuid")
            if mid in seen:
                continue
            if mid:
                seen.add(mid)
            cwd = entry.get("cwd")
            if project_filter and (not cwd or Path(cwd).name != project_filter):
                continue
            ts = _parse_ts(entry.get("timestamp"))
            if ts is None:
                skipped += 1
                continue
            model = str(msg.get("model") or "")
            rec = UsageRecord(
                timestamp=ts,
                branch=entry.get("gitBranch") or None,
                model=model,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                session_id=entry.get("sessionId"),
                cwd=cwd,
                cost_usd=None,
            )
            p = price_for(model, prices)
            if p:
                inp, out, cread = p
                rec.cost_usd = (rec.input_tokens * inp + rec.output_tokens * out
                                + rec.cache_write_tokens * inp * CACHE_WRITE_MULT
                                + rec.cache_read_tokens * cread) / 1_000_000
            records.append(rec)
    return records, skipped


def default_transcript_paths() -> list[Path]:
    from devkit.paths import claude_home
    root = claude_home() / "projects"
    return sorted(root.glob("*/*.jsonl")) if root.exists() else []


def load_prs_from_json(data: list[dict]) -> list[PullRequest]:
    prs = []
    for d in data:
        created = _parse_ts(d.get("createdAt"))
        if created is None:
            continue
        merged = _parse_ts(d.get("mergedAt"))
        prs.append(PullRequest(
            number=int(d.get("number", 0)), title=str(d.get("title", "")),
            branch=str(d.get("headRefName", "")), created_at=created, merged_at=merged,
            state=str(d.get("state", "MERGED" if merged else "OPEN")).upper(),
        ))
    return prs


def fetch_prs_gh(repo: str | None, limit: int = 500) -> list[PullRequest]:
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI 'gh' not found; install it or pass --prs <export.json>")
    cmd = ["gh", "pr", "list", "--state", "all", "--limit", str(limit),
           "--json", "number,title,headRefName,createdAt,mergedAt,state"]
    if repo:
        cmd += ["--repo", repo]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh pr list failed")
    return load_prs_from_json(json.loads(out.stdout or "[]"))


# ── aggregation ──────────────────────────────────────────────────────────────

def period_key(dt: datetime, period: str) -> str:
    if period == "month":
        return dt.strftime("%Y-%m")
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


@dataclass
class PeriodStats:
    period: str
    prs_opened: int = 0
    prs_merged: int = 0
    cycle_hours: list[float] = field(default_factory=list)
    ai_cost_usd: float = 0.0
    ai_cost_merged_prs: float = 0.0
    tokens: int = 0
    sessions: set = field(default_factory=set)

    def row(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "prs_opened": self.prs_opened,
            "prs_merged": self.prs_merged,
            "median_cycle_hours": round(statistics.median(self.cycle_hours), 1) if self.cycle_hours else None,
            "ai_cost_usd": round(self.ai_cost_usd, 2),
            "ai_cost_per_merged_pr": round(self.ai_cost_merged_prs / self.prs_merged, 2)
            if self.prs_merged else None,
            "tokens": self.tokens,
            "sessions": len(self.sessions),
        }


def compute(prs: list[PullRequest], usage: list[UsageRecord], period: str = "week",
            since: datetime | None = None) -> dict[str, Any]:
    if since:
        prs = [p for p in prs if p.created_at >= since or (p.merged_at and p.merged_at >= since)]
        usage = [u for u in usage if u.timestamp >= since]

    pr_by_branch: dict[str, PullRequest] = {}
    for pr in sorted(prs, key=lambda p: p.created_at):
        if pr.branch:
            pr_by_branch[pr.branch] = pr  # latest PR for a reused branch name wins

    cost_by_pr: dict[int, float] = defaultdict(float)
    tokens_by_pr: dict[int, int] = defaultdict(int)
    unattributed_cost = 0.0
    unpriced_tokens = 0
    for u in usage:
        tok = u.input_tokens + u.output_tokens + u.cache_write_tokens + u.cache_read_tokens
        if u.cost_usd is None:
            unpriced_tokens += tok
        pr = pr_by_branch.get(u.branch or "")
        if pr is not None:
            cost_by_pr[pr.number] += u.cost_usd or 0.0
            tokens_by_pr[pr.number] += tok
        else:
            unattributed_cost += u.cost_usd or 0.0

    periods: dict[str, PeriodStats] = {}

    def ps(key: str) -> PeriodStats:
        return periods.setdefault(key, PeriodStats(key))

    for pr in prs:
        if since is None or pr.created_at >= since:
            ps(period_key(pr.created_at, period)).prs_opened += 1
        if pr.merged_at and (since is None or pr.merged_at >= since):
            st = ps(period_key(pr.merged_at, period))
            st.prs_merged += 1
            st.cycle_hours.append((pr.merged_at - pr.created_at).total_seconds() / 3600)
            st.ai_cost_merged_prs += cost_by_pr.get(pr.number, 0.0)
    for u in usage:
        st = ps(period_key(u.timestamp, period))
        st.ai_cost_usd += u.cost_usd or 0.0
        st.tokens += u.input_tokens + u.output_tokens + u.cache_write_tokens + u.cache_read_tokens
        if u.session_id:
            st.sessions.add(u.session_id)

    merged = [p for p in prs if p.merged_at]
    total_cost = sum(u.cost_usd or 0.0 for u in usage)
    top = sorted(((cost_by_pr[p.number], p) for p in merged if cost_by_pr.get(p.number)),
                 key=lambda x: x[0], reverse=True)[:5]
    return {
        "period": period,
        "rows": [periods[k].row() for k in sorted(periods)],
        "totals": {
            "prs_opened": sum(1 for p in prs if since is None or p.created_at >= since),
            "prs_merged": len(merged),
            "ai_cost_usd": round(total_cost, 2),
            "ai_cost_attributed_to_prs": round(sum(cost_by_pr.values()), 2),
            "ai_cost_unattributed": round(unattributed_cost, 2),
            "attribution_rate": round(sum(cost_by_pr.values()) / total_cost, 3) if total_cost else None,
            "unpriced_tokens": unpriced_tokens,
        },
        "top_prs_by_cost": [
            {"number": p.number, "title": p.title, "branch": p.branch,
             "ai_cost_usd": round(c, 2), "tokens": tokens_by_pr[p.number]}
            for c, p in top
        ],
    }


def to_markdown(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [f"## Delivery & AI cost ({report['period']}ly)", "",
             "| Period | PRs opened | PRs merged | Median cycle (h) | AI cost | Cost / merged PR | Sessions |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in report["rows"]:
        cpp = f"${r['ai_cost_per_merged_pr']:.2f}" if r["ai_cost_per_merged_pr"] is not None else "-"
        cyc = r["median_cycle_hours"] if r["median_cycle_hours"] is not None else "-"
        lines.append(f"| {r['period']} | {r['prs_opened']} | {r['prs_merged']} | {cyc} | "
                     f"${r['ai_cost_usd']:.2f} | {cpp} | {r['sessions']} |")
    lines += ["", f"Total AI cost ${t['ai_cost_usd']:.2f}; attributed to PRs ${t['ai_cost_attributed_to_prs']:.2f}"
              f" ({(t['attribution_rate'] or 0) * 100:.0f}%), unattributed ${t['ai_cost_unattributed']:.2f}.",
              "Costs are list-price estimates from Claude Code transcripts; branch-to-PR attribution."]
    return "\n".join(lines)


def since_from(value: str | None, period: str) -> datetime | None:
    if not value:
        return None
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    dt = _parse_ts(value)
    if dt is None:
        raise ValueError(f"--since must be YYYY-MM-DD or Nd, got {value!r}")
    return dt
