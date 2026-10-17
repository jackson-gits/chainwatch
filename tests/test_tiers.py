"""Recommended-action tiers (spec 12.1, 17.2): boundaries, ties, floor logic, effective score."""

from __future__ import annotations

import dataclasses

import pytest

from chainwatch.decision.tiers import (
    RECOMMENDED_ACTION,
    TIER_ORDER,
    Thresholds,
    decide,
    tier_from_score,
)
from chainwatch.schema import TIERS

THR = Thresholds(verify=0.5, hold=0.8, edge=0.5, ring_alert=0.4)


def test_tier_order_matches_schema():
    assert TIER_ORDER == {"allow": 0, "verify": 1, "hold": 2}
    assert set(TIER_ORDER) == set(TIERS)


def test_recommended_action_texts_match_spec():
    assert RECOMMENDED_ACTION["allow"] == "No action recommended."
    assert RECOMMENDED_ACTION["verify"] == (
        "Recommend a step-up check with the sender (for example a confirmation prompt) before this "
        "payment completes."
    )
    assert RECOMMENDED_ACTION["hold"] == (
        "Recommend holding this payment for review by a fraud analyst before the money leaves the account."
    )


def test_tier_from_score_boundaries():
    assert tier_from_score(0.0, THR) == "allow"
    assert tier_from_score(0.4999, THR) == "allow"
    assert tier_from_score(0.5, THR) == "verify"  # tie at verify
    assert tier_from_score(0.7999, THR) == "verify"
    assert tier_from_score(0.8, THR) == "hold"  # tie at hold
    assert tier_from_score(1.0, THR) == "hold"


def test_ties_go_to_the_higher_tier():
    # exactly at a threshold the transaction lands in the tier at or above it
    assert tier_from_score(THR.verify, THR) != "allow"
    assert tier_from_score(THR.hold, THR) == "hold"
    assert tier_from_score(THR.verify - 1e-12, THR) == "allow"


def test_decide_without_floor_is_passthrough():
    assert decide(0.3, THR, None) == ("allow", 0.3)
    assert decide(0.6, THR, None) == ("verify", 0.6)
    assert decide(0.9, THR, None) == ("hold", 0.9)


def test_decide_floor_hold_raises_tier_and_effective_score():
    tier, eff = decide(0.1, THR, "hold")
    assert tier == "hold"
    assert eff == pytest.approx(THR.hold)  # effective score floored to the hold threshold


def test_decide_floor_verify_raises_tier_and_effective_score():
    tier, eff = decide(0.1, THR, "verify")
    assert tier == "verify"
    assert eff == pytest.approx(THR.verify)


def test_decide_floor_never_lowers_a_tier():
    # already hold from the score: a verify floor changes nothing, effective score stays the base
    assert decide(0.9, THR, "verify") == ("hold", 0.9)
    # hold floor on an already-hold score: no change either
    assert decide(0.8, THR, "hold") == ("hold", 0.8)
    # verify floor on a verify score: unchanged
    assert decide(0.6, THR, "verify") == ("verify", 0.6)


def test_decide_allow_floor_is_a_noop():
    assert decide(0.3, THR, "allow") == ("allow", 0.3)


def test_thresholds_from_dict_defaults():
    thr = Thresholds.from_dict({"verify": 0.4, "hold": 0.7})
    assert thr.verify == 0.4
    assert thr.hold == 0.7
    assert thr.edge == 0.4  # edge defaults to verify (spec 10.4)
    assert thr.ring_alert == 1.0


def test_thresholds_from_dict_explicit_values_win():
    thr = Thresholds.from_dict({"verify": 0.4, "hold": 0.7, "edge": 0.35, "ring_alert": 0.55})
    assert thr.edge == 0.35
    assert thr.ring_alert == 0.55


def test_thresholds_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        THR.verify = 0.1  # type: ignore[misc]
