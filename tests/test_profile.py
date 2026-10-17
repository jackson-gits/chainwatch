"""Benign profile (spec 10.5, 17.2): quantile grid, rank() interpolation and clamping, typical()."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from chainwatch.model.profile import LEVELS, RANK_MAX, RANK_MIN, BenignProfile, build_profile


def test_build_profile_grid_levels_and_meta():
    X = np.arange(1001, dtype=np.float64).reshape(-1, 1)  # 0, 1, ..., 1000
    prof = build_profile(X, ["counter"], meta={"world": "toy"})
    assert prof["format"] == "chainwatch-profile-1"
    assert prof["levels"] == list(LEVELS)
    assert prof["meta"] == {"world": "toy"}
    q = prof["features"]["counter"]["q"]
    assert prof["features"]["counter"]["n"] == 1001
    assert len(q) == len(LEVELS)
    # nearest-rank (inverted_cdf) quantiles of 0..1000
    assert q == pytest.approx([1.0, 10.0, 50.0, 250.0, 500.0, 750.0, 900.0, 950.0, 990.0, 999.0])


def test_build_profile_constant_and_empty_columns():
    X = np.zeros((50, 2))
    prof = build_profile(X, ["a", "b"])
    assert prof["features"]["a"]["q"] == [0.0] * len(LEVELS)
    empty = build_profile(np.zeros((0, 1)), ["z"])
    assert empty["features"]["z"]["n"] == 0
    assert empty["features"]["z"]["q"] == [0.0] * len(LEVELS)


def test_rank_hits_grid_levels_exactly():
    X = np.arange(1001, dtype=np.float64).reshape(-1, 1)
    p = BenignProfile(build_profile(X, ["counter"]))
    assert p.rank("counter", 500.0) == pytest.approx(0.5)  # the median
    assert p.rank("counter", 250.0) == pytest.approx(0.25)
    assert p.rank("counter", 990.0) == pytest.approx(0.99)


def test_rank_interpolates_linearly_between_grid_points():
    X = np.arange(1001, dtype=np.float64).reshape(-1, 1)
    p = BenignProfile(build_profile(X, ["counter"]))
    # halfway between q(0.5) = 500 and q(0.75) = 750
    assert p.rank("counter", 625.0) == pytest.approx(0.5 + 0.25 * 125 / 250)
    # one unit above the median
    assert p.rank("counter", 501.0) == pytest.approx(0.5 + 0.25 / 250)


def test_rank_clamps_outside_the_grid():
    X = np.arange(1001, dtype=np.float64).reshape(-1, 1)
    p = BenignProfile(build_profile(X, ["counter"]))
    assert p.rank("counter", -10.0) == RANK_MIN  # below the lowest grid value
    assert p.rank("counter", 1e12) == RANK_MAX  # above the highest grid value


def test_rank_constant_feature_returns_lower_level():
    # all values equal: bisect_right finds every level at once; rank returns the level below the run
    X = np.full((100, 1), 3.0)
    p = BenignProfile(build_profile(X, ["c"]))
    assert p.rank("c", 3.0) == pytest.approx(LEVELS[-1])  # 0.999, since value == q[-1] so not strictly above
    assert p.rank("c", 2.0) == RANK_MIN


def test_typical_is_the_median():
    X = np.arange(1001, dtype=np.float64).reshape(-1, 1)
    p = BenignProfile(build_profile(X, ["counter"]))
    assert p.typical("counter") == pytest.approx(500.0)


def test_load_round_trip(tmp_path: Path):
    import json

    X = np.arange(100, dtype=np.float64).reshape(-1, 1)
    path = tmp_path / "benign_profile.json"
    path.write_text(json.dumps(build_profile(X, ["counter"])), encoding="utf-8")
    p = BenignProfile.load(path)
    assert p.typical("counter") == pytest.approx(49.0) or p.typical("counter") == pytest.approx(50.0)


def test_unknown_feature_raises():
    p = BenignProfile(build_profile(np.zeros((10, 1)), ["known"]))
    with pytest.raises(KeyError):
        p.rank("unknown", 1.0)
    with pytest.raises(KeyError):
        p.typical("unknown")
