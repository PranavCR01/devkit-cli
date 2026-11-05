"""Run real DevKit commands for the demo video and record their output + wall time.

Usage:  python demo/setup.py            # once: build the workspace (see demo/README.md)
        python demo/capture.py [scene ...]   # no args = all scenes
Writes demo/captures/<scene>.json : {"steps": [{"cmd", "display", "output", "seconds", "code"}]}
Nothing is mocked: every command runs against the real CLI and the real Claude API.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("DEVKIT_DEMO_WORK", HERE / ".work")).resolve()
DEMO = WORK
APP = WORK / "acme-billing"
OUT = Path(os.environ.get("DEVKIT_DEMO_CAPTURES", HERE / "captures"))
TRANSCRIPTS = os.environ.get("DEVKIT_DEMO_TRANSCRIPTS", "~/.claude/projects")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

ENV = {
    **os.environ,
    "DEVKIT_HOME": str(WORK / "devkit-home"),
    "CLAUDE_CONFIG_DIR": str(WORK / "claude"),
    "FORCE_COLOR": "1",
    "COLUMNS": "104",
    "TERM": "xterm-256color",
    # Hide Hugging Face's per-command "Loading weights" bar and hub warnings.
    # A default install prints them; setup.py caches the model so offline mode works.
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "TRANSFORMERS_VERBOSITY": "error",
    "HF_HUB_VERBOSITY": "error",
    "HF_HUB_OFFLINE": "1",
}

# (scene id, cwd, [ (command, display-or-None) ])  display overrides what is "typed" on screen
SCENES: dict[str, tuple[Path, list[tuple[str, str | None]]]] = {
    "memory": (APP, [
        ("devkit memory save \"Invoices are multi-tenant: every query filters by org_id taken from the session, never from the request\" --type decision", None),
        ("devkit memory save \"All SQL uses sqlite3 ? placeholders; no f-string SQL anywhere\" --type decision", None),
        ("devkit memory save \"Stripe webhooks verify the Stripe-Signature header before parsing the body\" --type architecture", None),
        ("devkit memory save \"mark_paid's UPDATE was never committed; use 'with db() as conn'\" --type bug", None),
        ("devkit memory switch payments --note \"webhook retries half-done\"", None),
        ("devkit memory save \"Webhook retries reuse one Idempotency-Key per event\" --type decision", None),
    ]),
    "inject": (APP, [
        ("git log --oneline -1 && git branch --show-current", None),
        ("devkit context inject --token-cap 60", None),
    ]),
    "claude": (APP, [
        ("claude -p \"Without reading any files: what rules must a new query on the invoices table follow here, and what workstream am I on?\" "
         "--model claude-sonnet-5-5 --disallowedTools Read Grep Glob Bash Edit Write < /dev/null", None),
    ]),
    "app": (APP, [("ls", None), ("sed -n 17,24p app.py", None)]),
    "scan": (APP, [("devkit scan . --no-semgrep", "devkit scan . --no-semgrep")]),
    "scan_badkey": (APP, [
        ("devkit config set anthropic_api_key sk-ant-revoked-demo-key", None),
        ("devkit scan . --no-semgrep", None),
        # hidden: drop the bad key so the env key is used again (not shown on screen)
        ("python3 -c \"import json,os;p=os.environ['DEVKIT_HOME']+'/config.json';d=json.load(open(p));d.pop('anthropic_api_key',None);json.dump(d,open(p,'w'))\"", ""),
    ]),
    "ablate_plan": (DEMO, [("devkit eval ablate claude-code-request.json --dry-run", None)]),
    "ablate_run": (DEMO, [("rm -f $DEVKIT_HOME/eval/suggestions/*", ""),
                          ("devkit eval ablate claude-code-request.json", None)]),
    "ablate_report": (DEMO, [("devkit eval report 2>&1 | sed -n '/Ablation Suggestions/,$p'", "devkit eval report")]),
    # ~9 minutes and 360 judge calls (Haiku); the video shows this run sped up
    "bench": (DEMO, [("devkit eval bench --split test --runs 3", None)]),
    "handoff_a": (APP, [
        ("devkit handoff create \"Parameterize the /invoices query\" -a \"no f-string SQL in app.py\" -a \"tests pass\"", None),
        ("DEVKIT_AGENT=agent-a devkit handoff claim $(devkit handoff list --status open | grep -o '[0-9a-f]\\{6\\}' | head -1) | head -1",
         "DEVKIT_AGENT=agent-a devkit handoff claim <id>"),
        ("T=$(devkit handoff list | grep -o '[0-9a-f]\\{6\\}' | head -1); DEVKIT_AGENT=agent-a devkit handoff check $T 1 && "
         "DEVKIT_AGENT=agent-a devkit handoff release $T -m 'query uses ? params now; tests not run yet'",
         "DEVKIT_AGENT=agent-a devkit handoff release <id> -m \"query uses ? params now; tests not run yet\""),
    ]),
    "handoff_b": (APP, [
        ("T=$(devkit handoff list | grep -o '[0-9a-f]\\{6\\}' | head -1); DEVKIT_AGENT=agent-b devkit handoff claim $T",
         "DEVKIT_AGENT=agent-b devkit handoff claim <id>"),
    ]),
    "handoff_recover": (APP, [
        ("devkit handoff create \"Verify Stripe webhook signatures\" >/dev/null; "
         "T=$(devkit handoff list --status open | grep -o '[0-9a-f]\\{6\\}' | head -1); "
         "DEVKIT_AGENT=agent-c devkit handoff claim $T --lease 0 | head -1",
         "DEVKIT_AGENT=agent-c devkit handoff claim <id> --lease 0   # then agent-c crashes"),
        ("devkit handoff recover", None),
        ("devkit handoff stats", None),
    ]),
    "metrics": (DEMO, [
        (f"devkit metrics --prs prs.json --transcripts {TRANSCRIPTS} --since 30d",
         "devkit metrics --prs prs.json --since 30d"),
    ]),
}


def run(scene: str) -> None:
    cwd, steps = SCENES[scene]
    recorded = []
    for cmd, display in steps:
        t0 = time.monotonic()
        p = subprocess.run(["bash", "-c", cmd], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=900)
        dt = time.monotonic() - t0
        out = p.stdout + p.stderr
        if API_KEY:
            out = out.replace(API_KEY, "****")
        if display == "":
            print(f"[{scene}] (hidden step, exit {p.returncode})")
            continue
        recorded.append({"cmd": cmd, "display": display or cmd, "output": out, "seconds": round(dt, 1),
                         "code": p.returncode})
        print(f"[{scene}] {display or cmd}  ({dt:.1f}s, exit {p.returncode})")
    OUT.mkdir(exist_ok=True)
    (OUT / f"{scene}.json").write_text(json.dumps({"scene": scene, "steps": recorded}, indent=1))


if __name__ == "__main__":
    if not APP.exists():
        sys.exit(f"workspace missing: run python {HERE / 'setup.py'} first")
    if not API_KEY:
        sys.exit("set ANTHROPIC_API_KEY (scan, ablate, bench and the claude scene call the real API)")
    for s in sys.argv[1:] or SCENES:
        run(s)
