"""Ring score numbers (spec 11.3, 17.2): noisy-OR, relay discount, clamping, top-k."""

from __future__ import annotations

import random

import pytest

from chainwatch.rings.scoring import noisy_or, ring_score


def test_noisy_or_spec_example():
    # spec 11.3: scores 0.5, 0.5 -> noisy-OR 0.75
    assert noisy_or([0.5, 0.5], 5) == pytest.approx(0.75)


def test_ring_score_spec_example():
    # spec 11.3: scores 0.5, 0.5 and 1 relay -> ring score 0.375
    assert ring_score([0.5, 0.5], 1, 5) == pytest.approx(0.375)


def test_relay_discount_numbers():
    # zero relays 0, one 0.5, two 0.75, three 0.875 (spec 11.3)
    strong = [1.0]
    assert ring_score(strong, 0, 5) == pytest.approx(0.0)
    assert ring_score(strong, 1, 5) == pytest.approx(0.5)
    assert ring_score(strong, 2, 5) == pytest.approx(0.75)
    assert ring_score(strong, 3, 5) == pytest.approx(0.875)


def test_zero_relays_zeroes_even_strong_evidence():
    assert ring_score([0.9, 0.8], 0, 5) == pytest.approx(0.0)


def test_noisy_or_clamps_scores_into_0_1():
    assert noisy_or([1.5, -0.25], 5) == pytest.approx(1.0)  # 1.5 clamps to 1
    assert noisy_or([-1.0, -2.0], 5) == pytest.approx(0.0)  # negatives clamp to 0
    assert noisy_or([2.0], 5) == pytest.approx(1.0)


def test_noisy_or_uses_only_top_k():
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert noisy_or(scores, 3) == pytest.approx(1.0 - 0.3 * 0.4 * 0.5)
    assert noisy_or(scores, 1) == pytest.approx(0.7)
    assert noisy_or([0.2, 0.7], 1) == pytest.approx(0.7)


def test_noisy_or_empty_scores_and_zero_k():
    assert noisy_or([], 5) == pytest.approx(0.0)
    assert noisy_or([0.9, 0.8], 0) == pytest.approx(0.0)
    assert ring_score([], 2, 5) == pytest.approx(0.0)


def test_noisy_or_is_order_independent():
    scores = [0.13, 0.87, 0.42, 0.42, 0.99]
    rng = random.Random(11)
    for _ in range(5):
        shuffled = scores[:]
        rng.shuffle(shuffled)
        assert noisy_or(shuffled, 4) == pytest.approx(noisy_or(scores, 4))


def test_ring_score_top_k_with_relay_discount():
    # top 1 is 0.7, one relay halves it
    assert ring_score([0.7, 0.2], 1, 1) == pytest.approx(0.35)
