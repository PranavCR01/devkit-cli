"""Test isolation: every test gets a private DEVKIT_HOME and CLAUDE_CONFIG_DIR.

devkit.paths resolves locations at import time, so the env vars are set here,
before any devkit module is imported, and the directories are wiped per test.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(tempfile.mkdtemp(prefix="devkit-tests-"))
os.environ["DEVKIT_HOME"] = str(_ROOT / "devkit")
os.environ["CLAUDE_CONFIG_DIR"] = str(_ROOT / "claude")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def clean_home():
    for d in (_ROOT / "devkit", _ROOT / "claude"):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    yield _ROOT


class FakeEmbedder:
    """Deterministic bag-of-words embedder (384-dim, L2-normalized). No model download."""

    dim = 384

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in text.lower().split():
            tok = "".join(ch for ch in tok if ch.isalnum())
            if not tok:
                continue
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def backend(fake_embedder):
    pytest.importorskip("sqlite_vec")
    pytest.importorskip("numpy")
    from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
    return SQLiteBackend(MEMORY_DB, embedder=fake_embedder)


class _Block:
    def __init__(self, text: str, type: str = "text"):
        self.type = type
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block("", "thinking"), _Block(text)]
        self.stop_reason = "end_turn"


class FakeMessages:
    """Records calls; `responder(kwargs) -> str | Exception` decides each reply."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.responder(kwargs)
        if isinstance(out, Exception):
            raise out
        return _Resp(out)


class FakeClient:
    def __init__(self, responder):
        self.messages = FakeMessages(responder)


@pytest.fixture
def fake_client_factory():
    return FakeClient
