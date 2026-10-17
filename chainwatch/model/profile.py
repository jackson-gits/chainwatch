"""Benign profile: per-feature quantile grids of legitimate validation transactions (spec 10.5).

``build_profile`` (train env, numpy) writes the grid; ``BenignProfile`` (pure Python, serve env) answers
``rank(feature, value)`` (approximate share of legitimate transactions at or below ``value``) and ``typical``.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

LEVELS: tuple[float, ...] = (0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999)
RANK_MIN, RANK_MAX = 0.0005, 0.9995


def build_profile(X_neg, names: list[str], meta: dict | None = None) -> dict:
    """Nearest-rank quantiles at ``LEVELS`` for every feature column (``X_neg``: legitimate rows only)."""
    import numpy as np

    X_neg = np.asarray(X_neg, dtype=np.float64)
    feats = {}
    for j, name in enumerate(names):
        col = X_neg[:, j]
        q = np.quantile(col, LEVELS, method="inverted_cdf") if col.size else np.zeros(len(LEVELS))
        feats[name] = {"n": int(col.size), "q": [float(v) for v in q]}
    return {"format": "chainwatch-profile-1", "levels": list(LEVELS), "features": feats, "meta": meta or {}}


class BenignProfile:
    def __init__(self, data: dict):
        self.data = data
        self.levels = [float(v) for v in data["levels"]]
        self.features = {k: [float(v) for v in d["q"]] for k, d in data["features"].items()}

    @classmethod
    def load(cls, path: str | Path) -> BenignProfile:
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def rank(self, feature: str, value: float) -> float:
        """Approximate share of legitimate transactions with this feature at or below ``value``."""
        q = self.features[feature]
        L = self.levels
        j = bisect_right(q, value)
        if j == 0:
            return RANK_MIN
        if j == len(q):
            return RANK_MAX if value > q[-1] else L[-1]
        q_lo, q_hi = q[j - 1], q[j]
        if q_hi <= q_lo:
            return L[j - 1]
        return L[j - 1] + (L[j] - L[j - 1]) * (value - q_lo) / (q_hi - q_lo)

    def typical(self, feature: str) -> float:
        """The median of the feature among legitimate transactions."""
        return self.features[feature][self.levels.index(0.5)]
