"""Single source of truth for DevKit's on-disk locations.

Set DEVKIT_HOME to relocate all state (used by tests and for multi-profile setups).
Paths are resolved once at import time; set the env var before importing devkit.
"""
from __future__ import annotations

import os
from pathlib import Path


def devkit_home() -> Path:
    override = os.environ.get("DEVKIT_HOME")
    return Path(override).expanduser() if override else Path.home() / ".devkit"


def claude_home() -> Path:
    """Claude Code config dir (honors CLAUDE_CONFIG_DIR like Claude Code does)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


DEVKIT_DIR = devkit_home()
CONFIG_FILE = DEVKIT_DIR / "config.json"
STATE_DB = DEVKIT_DIR / "state.db"
MEMORY_DB = DEVKIT_DIR / "memory.db"
MANIFEST_PATH = DEVKIT_DIR / "manifest.json"
BLUEPRINTS_DIR = DEVKIT_DIR / "blueprints"
EVAL_DIR = DEVKIT_DIR / "eval"
SUGGESTIONS_DIR = EVAL_DIR / "suggestions"
PID_FILE = EVAL_DIR / "headroom.pid"
SESSION_FILE = DEVKIT_DIR / "context_session.json"
HOOKS_DIR = DEVKIT_DIR / "hooks"
HANDOFF_DIR = DEVKIT_DIR / "handoff"
