"""Issue: test infrastructure, packaging, hygiene."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_help_works_without_optional_extras():
    # Simulate a base install: numpy / sqlite_vec / networkx / sentence_transformers absent.
    code = (
        "import sys\n"
        "for m in ('numpy','sqlite_vec','networkx','sentence_transformers','tiktoken'):\n"
        "    sys.modules[m] = None\n"
        "from typer.testing import CliRunner\n"
        "from devkit.cli import app\n"
        "r = CliRunner().invoke(app, ['--help'])\n"
        "assert r.exit_code == 0, r.output\n"
        "r = CliRunner().invoke(app, ['memory', 'list'])\n"
        "assert 'pip install' in r.output, r.output\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ})
    assert out.returncode == 0, out.stderr + out.stdout
    assert "ok" in out.stdout


def test_devkit_home_isolates_state():
    from devkit import paths
    assert str(paths.DEVKIT_DIR) == os.environ["DEVKIT_HOME"]
    assert paths.MEMORY_DB.parent == paths.DEVKIT_DIR


def test_no_bytecode_or_egg_info_tracked():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    assert ".pyc" not in files
    assert "egg-info" not in files


def test_config_coerces_booleans_and_ints():
    from devkit.config import Config
    c = Config()
    c.set("auto_learn", "false")
    c.set("semgrep_timeout", "30")
    c2 = Config()
    assert c2.get("auto_learn") is False
    assert c2.get("semgrep_timeout") == 30
    c.set("auto_learn", "yes")
    assert Config().get("auto_learn") is True


def test_config_rejects_bad_boolean():
    import pytest
    from devkit.config import Config
    with pytest.raises(ValueError):
        Config().set("auto_learn", "maybe")


def test_api_key_falls_back_to_env(monkeypatch):
    from devkit.config import Config
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Config().get("anthropic_api_key") == "sk-test"


def test_models_are_configurable_current_generation():
    from devkit.config import Config
    assert Config().get("default_model") == "claude-sonnet-5-5"
    assert Config().get("fast_model") == "claude-haiku-5-5"
    src = "".join(p.read_text() for p in (ROOT / "devkit" / "core" / "scanner").rglob("*.py"))
    assert "claude-haiku-4-5" not in src and "claude-sonnet-4-6" not in src
