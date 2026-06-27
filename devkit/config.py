from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEVKIT_DIR = Path.home() / ".devkit"
CONFIG_FILE = DEVKIT_DIR / "config.json"

REQUIRED_KEYS = ["anthropic_api_key"]


class Config:
    """Manages ~/.devkit/config.json"""

    DEFAULTS: dict[str, Any] = {
        "anthropic_api_key": None,
        "default_model": "claude-sonnet-4-6",
        "fast_model": "claude-haiku-4-5",
        "semgrep_timeout": 120,
        "scan_max_file_size_kb": 500,
        "scan_tier2_line_cap": 8000,
    }

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        if CONFIG_FILE.exists():
            try:
                self._data = json.loads(CONFIG_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str) -> Any:
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._persist()

    def is_set(self, key: str) -> bool:
        """Return True if the key was explicitly set (not just a default)."""
        return key in self._data

    def all(self) -> dict[str, Any]:
        """Return defaults merged with any explicitly set values."""
        merged = dict(self.DEFAULTS)
        merged.update(self._data)
        return merged

    def validate(self) -> list[str]:
        """Return list of required keys that are missing or None."""
        return [k for k in REQUIRED_KEYS if not self.get(k)]

    def init_if_missing(self) -> bool:
        """Create an empty config.json if it does not exist. Returns True if created.

        Writes {} so that explicit sets are distinguishable from defaults in config list.
        """
        if CONFIG_FILE.exists():
            return False
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("{}\n")
        return True

    def _persist(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self._data, indent=2))
