from __future__ import annotations
import json
import os
from typing import Any

from devkit.paths import CONFIG_FILE, DEVKIT_DIR  # noqa: F401

REQUIRED_KEYS = ["anthropic_api_key"]

DEFAULT_MODEL = "claude-sonnet-5-5"
FAST_MODEL = "claude-haiku-5-5"

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n", ""}


def coerce(value: Any, like: Any) -> Any:
    """Coerce a CLI string to the type of the default value it overrides."""
    if not isinstance(value, str) or like is None or isinstance(like, str):
        return value
    if isinstance(like, bool):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        raise ValueError(f"expected a boolean (true/false), got {value!r}")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    return value


class Config:
    """Manages ~/.devkit/config.json (or $DEVKIT_HOME/config.json)."""

    DEFAULTS: dict[str, Any] = {
        "anthropic_api_key": None,
        "default_model": DEFAULT_MODEL,
        "fast_model": FAST_MODEL,
        "semgrep_timeout": 120,
        "scan_max_file_size_kb": 500,
        "scan_tier2_line_cap": 8000,
        "scan_max_concurrency": 4,
        "auto_learn": False,
    }

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        if CONFIG_FILE.exists():
            try:
                self._data = json.loads(CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str) -> Any:
        if key in self._data:
            value = self._data[key]
            try:
                return coerce(value, self.DEFAULTS.get(key))
            except ValueError:
                return self.DEFAULTS.get(key)
        if key == "anthropic_api_key" and os.environ.get("ANTHROPIC_API_KEY"):
            return os.environ["ANTHROPIC_API_KEY"]
        return self.DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = coerce(value, self.DEFAULTS.get(key))
        self._persist()

    def is_set(self, key: str) -> bool:
        """Return True if the key was explicitly set (not just a default)."""
        return key in self._data

    def all(self) -> dict[str, Any]:
        """Return defaults merged with any explicitly set values."""
        return {k: self.get(k) for k in {**self.DEFAULTS, **self._data}}

    def validate(self) -> list[str]:
        """Return list of required keys that are missing or None."""
        return [k for k in REQUIRED_KEYS if not self.get(k)]

    def init_if_missing(self) -> bool:
        """Create an empty config.json if it does not exist. Returns True if created."""
        if CONFIG_FILE.exists():
            return False
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("{}\n")
        return True

    def _persist(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self._data, indent=2))
