"""Ring score (spec 11.3): noisy-OR of the strongest edges, discounted by the number of relay accounts."""

from __future__ import annotations

import heapq


def noisy_or(scores: list[float], k: int) -> float:
    p = 1.0
    for s in heapq.nlargest(k, scores):
        p *= 1.0 - min(max(s, 0.0), 1.0)
    return 1.0 - p


def ring_score(edge_scores: list[float], n_relays: int, k: int) -> float:
    """``noisy_or(top k) x (1 - 0.5 ** n_relays)``: zero relays give 0, one gives half, two three quarters."""
    return noisy_or(edge_scores, k) * (1.0 - 0.5**n_relays)
