"""Build the demo workspace that demo/capture.py records against.

    python demo/setup.py [--repo owner/name] [--fresh]

Creates $DEVKIT_DEMO_WORK (default demo/.work):
  acme-billing/              the synthetic sample app (deliberate vulnerabilities), as a
                             git repo on branch feature/stripe-webhooks
  claude-code-request.json   a request shaped like a Claude Code turn (built here, not captured)
  prs.json                   PR export for `devkit metrics` (needs the gh CLI and repo access)
  devkit-home/, claude/      isolated DevKit state and Claude Code config (hook registered)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("DEVKIT_DEMO_WORK", HERE / ".work")).resolve()
APP = WORK / "acme-billing"
GIT_ID = ["-c", "user.name=demo", "-c", "user.email=demo@example.com"]


def sh(args: list[str], cwd: Path, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, check=check, capture_output=True, text=True)


def build_app() -> None:
    shutil.copytree(HERE / "sample_app", APP)
    sh(["git", "init", "-q", "-b", "main"], APP)
    sh(["git", "add", "-A"], APP)
    sh(["git", *GIT_ID, "commit", "-qm", "Acme billing API"], APP)
    # Staging only: an empty commit on a feature branch gives the SessionStart hook a
    # branch name and commit subject to rank memory against. No code changes.
    sh(["git", "switch", "-q", "-c", "feature/stripe-webhooks"], APP)
    sh(["git", *GIT_ID, "commit", "-q", "--allow-empty", "-m",
        "Stripe webhooks: verify signatures and add retries"], APP)


def build_request() -> None:
    """A Claude Code-shaped turn: system prompt, tools, three tool results, cache breakpoint."""
    app = (APP / "app.py").read_text()
    pins = [("blinker", "1.8.2"), ("certifi", "2024.8.30"), ("charset-normalizer", "3.4.0"), ("click", "8.1.7"),
            ("coverage", "7.6.4"), ("Flask", "3.0.3"), ("gunicorn", "23.0.0"), ("idna", "3.10"),
            ("iniconfig", "2.0.0"), ("itsdangerous", "2.2.0"), ("Jinja2", "3.1.4"), ("MarkupSafe", "3.0.2"),
            ("packaging", "24.1"), ("pluggy", "1.5.0"), ("pytest", "8.3.3"), ("pytest-cov", "5.0.0"),
            ("python-dotenv", "1.0.1"), ("requests", "2.32.3"), ("stripe", "11.1.0"), ("urllib3", "2.2.3"),
            ("Werkzeug", "3.0.6")]
    freeze = "\n".join(f"{n}=={v}" for n, v in pins * 3)
    tests = "\n".join(f"tests/test_invoices.py::test_list_invoices_case_{i} PASSED [{i * 3}%]"
                      for i in range(1, 34))
    tests += "\n============================= 33 passed in 1.42s =============================="
    system = ("You are Claude Code, an interactive CLI coding agent. Use the tools to inspect and edit "
              "the repository. Follow the project's conventions. ") * 8
    tool = lambda name, desc, prop: {"name": name, "description": desc,  # noqa: E731
                                     "input_schema": {"type": "object", "properties": {prop: {"type": "string"}},
                                                      "required": [prop]}}
    body = {
        "model": "claude-opus-5-5", "max_tokens": 16000,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": [tool("Read", "Read a file", "file_path"), tool("Bash", "Run a shell command", "command")],
        "messages": [
            {"role": "user", "content": [{"type": "text", "cache_control": {"type": "ephemeral"},
                                          "text": "Repository: acme-billing (Flask, SQLite). Task: fix the SQL "
                                                  "injection in the /invoices endpoint without changing its "
                                                  "response format."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "I'll look at the endpoint first."},
                                              {"type": "tool_use", "id": "t1", "name": "Read",
                                               "input": {"file_path": "app.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": app}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                               "input": {"command": "pip freeze"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": freeze}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t3", "name": "Bash",
                                               "input": {"command": "pytest -q -v"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": tests}]},
            {"role": "assistant", "content": [{"type": "text", "text": "I have what I need."}]},
            {"role": "user", "content": [{"type": "text", "text": "Write the fix now as a unified diff for "
                                                                  "app.py. Respond with the diff only; do not "
                                                                  "call tools."}]},
        ],
    }
    (WORK / "claude-code-request.json").write_text(json.dumps(body, indent=1))


def export_prs(repo: str) -> None:
    """gh's `pr list` needs GraphQL; the REST API works more widely, so map it to the same shape."""
    jq = ('[.[] | {number, title, headRefName: .head.ref, createdAt: .created_at, mergedAt: .merged_at, '
          'state: (if .merged_at then "MERGED" else (.state|ascii_upcase) end)}]')
    if not shutil.which("gh"):
        print("[skip] gh not found: write prs.json yourself for the metrics scene")
        return
    out = sh(["gh", "api", f"repos/{repo}/pulls?state=all&per_page=100", "--jq", jq], WORK, check=False)
    if out.returncode != 0:
        print(f"[skip] gh api failed: {out.stderr.strip()}")
        return
    (WORK / "prs.json").write_text(out.stdout)


def init_devkit() -> None:
    env = {**os.environ, "DEVKIT_HOME": str(WORK / "devkit-home"), "CLAUDE_CONFIG_DIR": str(WORK / "claude")}
    sh(["devkit", "init", "--register-hook"], APP, env)
    # Download and cache the embedding model now, so capture.py can run with HF_HUB_OFFLINE=1.
    warm = sh(["devkit", "search", "warm up the embedding model"], APP, env, check=False)
    if warm.returncode != 0:
        print("[warn] could not warm the embedding model (needs the [memory] extra and huggingface.co):")
        print(warm.stdout[-400:], warm.stderr[-400:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="PranavCR01/devkit-cli", help="repo whose PRs feed the metrics scene")
    ap.add_argument("--fresh", action="store_true", help="delete an existing workspace first")
    a = ap.parse_args()
    if not shutil.which("devkit"):
        sys.exit("devkit is not on PATH: pip install -e '.[memory,graph]' from the repo root")
    if WORK.exists():
        if not a.fresh:
            sys.exit(f"{WORK} exists; pass --fresh to rebuild it")
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    build_app()
    build_request()
    export_prs(a.repo)
    init_devkit()
    print(f"[OK] workspace ready: {WORK}")
    print("Next: ANTHROPIC_API_KEY=... python demo/capture.py   then   python demo/render.py")


if __name__ == "__main__":
    main()
