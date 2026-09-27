"""Text embeddings, computed locally (no vendor: user questions never leave the server).

paraphrase-multilingual-MiniLM-L12-v2 via fastembed (ONNX, CPU): 384 dims, ~0.2 GB,
a few ms per query. Hinglish is handled by glossary expansion before embedding.
"""

import hashlib
import logging
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384

Vectors = NDArray[np.float32]


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: list[str]) -> Vectors:
        """Unit-length rows, one per text."""
        ...


def _unit(m: NDArray[np.float32]) -> Vectors:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.maximum(norms, 1e-12)).astype(np.float32)


class FastEmbedder:
    dim = DIM
    name = MODEL_NAME

    def __init__(self, cache_dir: str | Path) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(MODEL_NAME, cache_dir=str(cache_dir))

    def embed(self, texts: list[str]) -> Vectors:
        return _unit(np.array(list(self._model.embed(texts)), dtype=np.float32))


class HashEmbedder:
    """Deterministic bag-of-words embedding for tests: shared words ⇒ similar vectors."""

    dim = 64
    name = "hash-bow"

    def embed(self, texts: list[str]) -> Vectors:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.casefold().split():
                w = w.strip(".,:;!?|()")
                if len(w) > 2:
                    h = int(hashlib.md5(w.encode()).hexdigest()[:8], 16)
                    out[i, h % self.dim] += 1.0
        return _unit(out)


def fetch_model(cache_dir: str | Path) -> None:
    """Download the model files (build step), so workers start without network."""
    FastEmbedder(cache_dir).embed(["warm up"])
    log.info("embedding model ready in %s", cache_dir)
