"""Face templates: normalisation, hashing (for the mandate commitment) and matching."""
from __future__ import annotations

import numpy as np

from .. import crypto

# OpenCV SFace's recommended cosine-similarity threshold for "same person".
MATCH_THRESHOLD = 0.363


def normalize(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    return v / (np.linalg.norm(v) + 1e-9)


def template_hash(template: np.ndarray) -> str:
    return "sha256:" + crypto.sha256_hex(normalize(template).tobytes())


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(normalize(a), normalize(b)))


def to_blob(template: np.ndarray) -> bytes:
    return normalize(template).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def synthetic(seed: int, dim: int = 128) -> np.ndarray:
    """A deterministic stand-in template for a synthetic identity (no real face involved)."""
    return normalize(np.random.default_rng(seed).normal(size=dim))


def near(template: np.ndarray, similarity: float, seed: int = 0) -> np.ndarray:
    """A synthetic 'probe' with a chosen cosine similarity to ``template`` (for tests/eval)."""
    t = normalize(template)
    noise = np.random.default_rng(seed).normal(size=t.shape)
    noise = normalize(noise - np.dot(noise, t) * t)
    return normalize(similarity * t + np.sqrt(max(0.0, 1 - similarity**2)) * noise)
