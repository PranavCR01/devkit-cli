from __future__ import annotations
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

HEADROOM_PORT = 8787
HEADROOM_LOG = Path.home() / ".headroom" / "logs" / "proxy.log"
PROXY_SAVINGS_FILE = Path.home() / ".headroom" / "proxy_savings.json"
SKIP_HEADER = "x-headroom-bypass"
PID_FILE = Path.home() / ".devkit" / "eval" / "headroom.pid"


def _read_pid_file() -> tuple[int, int] | None:
    """Returns (pid, port) or None if file missing or malformed."""
    if not PID_FILE.exists():
        return None
    try:
        data = json.loads(PID_FILE.read_text())
        return int(data["pid"]), int(data["port"])
    except Exception:
        return None


def _write_pid_file(pid: int, port: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({"pid": pid, "port": port}))


class HeadroomBridge:
    def __init__(self, port: int = HEADROOM_PORT) -> None:
        self.port = port
        self._base = f"http://127.0.0.1:{port}"

    def is_running(self) -> bool:
        try:
            resp = httpx.get(f"{self._base}/stats", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> subprocess.Popen | None:
        headroom_exe = shutil.which("headroom")
        if not headroom_exe:
            return None
        process = subprocess.Popen(
            [headroom_exe, "proxy", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(json.dumps({"pid": process.pid, "port": self.port}))
        return process

    def stop(self) -> bool:
        """Kill Headroom via PID file. Returns True if a process was terminated."""
        pf = _read_pid_file()
        if pf is None:
            return False
        pid, _ = pf
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
                killed = result.returncode == 0
            else:
                os.kill(pid, signal.SIGTERM)
                killed = True
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            killed = False
        except Exception:
            killed = False
        finally:
            PID_FILE.unlink(missing_ok=True)
        return killed

    def get_stats(self) -> dict[str, Any]:
        try:
            return httpx.get(f"{self._base}/stats", timeout=5.0).json()
        except Exception:
            return {}

    def get_session_calls(self, limit: int = 50) -> list[dict]:
        # Primary: proxy.log (only if it has content)
        if HEADROOM_LOG.exists():
            try:
                lines = HEADROOM_LOG.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    calls = []
                    for line in reversed(lines[-limit:]):
                        try:
                            calls.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    return list(reversed(calls))
            except OSError:
                pass
        # Fallback: proxy_savings.json aggregated session data
        if PROXY_SAVINGS_FILE.exists():
            try:
                data = json.loads(PROXY_SAVINGS_FILE.read_text(encoding="utf-8"))
                sess = data.get("persistent_savings", {}).get("display_session", {})
                if sess:
                    return [{"_source": "proxy_savings", **sess}]
            except Exception:
                pass
        return []

    def compute_session_savings(self, calls: list[dict]) -> dict[str, Any]:
        # Primary: /stats endpoint
        try:
            stats = httpx.get(f"{self._base}/stats", timeout=5.0).json()
            sess = stats.get("persistent_savings", {}).get("display_session", {})
            if sess:
                total_input = sess.get("total_input_tokens", 0)
                tokens_saved = sess.get("tokens_saved", 0)
                total_compressed = total_input - tokens_saved
                ratio = (tokens_saved / total_input) if total_input > 0 else 0.0
                return {
                    "calls": sess.get("requests", len(calls)),
                    "total_input_tokens": total_input,
                    "total_compressed_tokens": total_compressed,
                    "tokens_saved": tokens_saved,
                    "compression_ratio": round(ratio, 4),
                    "estimated_cost_saved_usd": round(
                        sess.get("compression_savings_usd", tokens_saved * 0.000003), 6
                    ),
                }
        except Exception:
            pass
        # Fallback: proxy_savings.json synthetic entry
        if calls and calls[0].get("_source") == "proxy_savings":
            sess = calls[0]
            total_input = sess.get("total_input_tokens", 0)
            tokens_saved = sess.get("tokens_saved", 0)
            total_compressed = total_input - tokens_saved
            ratio = (tokens_saved / total_input) if total_input > 0 else 0.0
            return {
                "calls": sess.get("requests", 0),
                "total_input_tokens": total_input,
                "total_compressed_tokens": total_compressed,
                "tokens_saved": tokens_saved,
                "compression_ratio": round(ratio, 4),
                "estimated_cost_saved_usd": round(
                    sess.get("compression_savings_usd", tokens_saved * 0.000003), 6
                ),
            }
        # Last resort: raw proxy.log record math
        total_input = sum(c.get("input_tokens_original", 0) for c in calls)
        total_compressed = sum(c.get("input_tokens_optimized", 0) for c in calls)
        total_saved = total_input - total_compressed
        ratio = (total_saved / total_input) if total_input > 0 else 0.0
        return {
            "calls": len(calls),
            "total_input_tokens": total_input,
            "total_compressed_tokens": total_compressed,
            "tokens_saved": total_saved,
            "compression_ratio": round(ratio, 4),
            "estimated_cost_saved_usd": round(total_saved * 0.000003, 6),
        }

    def setup_instructions(self) -> str:
        p = self.port
        return (
            f"\nHeadroom proxy running on http://127.0.0.1:{p}\n"
            f"\nAdd to ~/.claude/settings.json:\n"
            f'  {{"env": {{"ANTHROPIC_BASE_URL": "http://127.0.0.1:{p}"}}}}\n'
            f"\nOr set in shell before starting Claude Code:\n"
            f"  Windows cmd:   set ANTHROPIC_BASE_URL=http://127.0.0.1:{p}\n"
            f'  PowerShell:    $env:ANTHROPIC_BASE_URL = "http://127.0.0.1:{p}"\n'
            f"\nRestart Claude Code after setting.\n"
        )
