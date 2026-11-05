"""Issue 08: docs must describe the code that exists."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]


def _doc_commands():
    cmds = set()
    files = [ROOT / "README.md", *(ROOT / "skills").glob("*/SKILL.md")]
    for f in files:
        for block in re.findall(r"```(?:bash)?\n(.*?)```", f.read_text(), re.S):
            for line in block.splitlines():
                line = re.sub(r"^\s*(DEVKIT_AGENT=\S+\s+)?", "", line).split("#")[0].strip()
                if line.startswith("devkit "):
                    cmds.add(line.rstrip("\\").strip())
    return sorted(cmds)


@pytest.mark.parametrize("line", _doc_commands())
def test_documented_commands_and_flags_exist(line):
    from devkit.cli import app
    words = shlex.split(line.replace("<", "").replace(">", ""))[1:]
    path = []
    for w in words:
        if w.startswith("-"):
            break
        path.append(w)
        r = CliRunner().invoke(app, [*path, "--help"])
        if r.exit_code != 0:  # positional argument, not a subcommand
            path.pop()
            break
    help_text = CliRunner().invoke(app, [*path, "--help"]).output
    flags = [w.split("=")[0] for w in words if w.startswith("--")]
    for flag in flags:
        assert flag in help_text, f"{line!r}: {flag} not in `devkit {' '.join(path)} --help`"


def test_readme_has_no_fabricated_claims():
    text = (ROOT / "README.md").read_text()
    for bad in ("config_sample.json", "shell=True", "score: 0.94", "port 9999", "localhost:9999",
                "sampling 20%", "pytest tests/ -v"):
        assert bad not in text, bad


def test_skills_reference_real_subcommands():
    eval_skill = (ROOT / "skills" / "eval" / "SKILL.md").read_text()
    assert "compare" not in eval_skill and "9999" not in eval_skill


def test_scan_triage_agent_does_not_demand_named_breaches():
    text = (ROOT / "agents" / "scan-triage.md").read_text()
    assert "No verified example" in text and "company + year" not in text


def test_skill_names_are_not_double_namespaced():
    for f in (ROOT / "skills").glob("*/SKILL.md"):
        m = re.search(r"^name:\s*(.+)$", f.read_text(), re.M)
        assert m and ":" not in m.group(1), f
