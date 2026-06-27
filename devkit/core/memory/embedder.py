from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from rich.console import Console

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_console = Console()

_STRUCT = struct.Struct(f"{EMBEDDING_DIM}f")


class Embedder:
    """Local sentence-transformers embedder. Loaded once per process as a singleton.

    Model: all-MiniLM-L6-v2 (~22 MB, 384-dim, CPU-only, ~5-20 ms per sentence).
    First call downloads the model; subsequent calls use the HuggingFace cache.
    """

    _instance: "Embedder | None" = None
    _model = None

    @classmethod
    def get_instance(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        if not _model_cached():
            with _console.status(
                "Downloading embedding model (first run, ~22MB)..."
            ):
                self._model = SentenceTransformer(MODEL_NAME)
        else:
            self._model = SentenceTransformer(MODEL_NAME)

    def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns 384-dim normalized vector."""
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed. More efficient than looping over embed()."""
        vecs = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vecs.tolist()

    @staticmethod
    def to_bytes(vector: list[float]) -> bytes:
        """Serialize a 384-dim float32 vector to bytes for BLOB storage."""
        return _STRUCT.pack(*vector)

    @staticmethod
    def from_bytes(data: bytes) -> list[float]:
        """Deserialize bytes back to float list."""
        return list(_STRUCT.unpack(data))

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two pre-normalized vectors (dot product)."""
        return float(np.dot(a, b))


def _model_cached() -> bool:
    """Return True if the HuggingFace hub cache for all-MiniLM-L6-v2 exists."""
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    try:
        if hub.exists():
            return any(
                p.name.startswith("models--sentence-transformers--all-MiniLM")
                for p in hub.iterdir()
            )
    except OSError:
        pass
    return False
