"""Plain-language reason codes (spec 12.2, 17.2): one positive and one negative fixture per code.

Every fixture is synthetic and uses opaque ids only (rule D1).
"""

from __future__ import annotations

import pytest

from chainwatch.decision.reasons import DENY_WORDS, ReasonConfigError, ReasonEngine
from chainwatch.model.profile import LEVELS, BenignProfile

#: benign profile so the ``rank>=`` clause of MANY_SENDERS_IN is evaluable (spec 10.5 format)
PROFILE = BenignProfile(
    {
        "levels": list(LEVELS),
        "features": {
            "d_in_uniq_1h": {"n": 100, "q": [0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0]},
        },
    }
)

#: code -> (positive features, negative features, forced codes); decision is "verify" for all
CASES: dict[str, tuple[dict, dict, tuple]] = {
    "RING_MEMBER_OUT": ({}, {}, ("RING_MEMBER_OUT",)),
    "RING_MEMBER_IN": ({}, {}, ("RING_MEMBER_IN",)),
    "FAST_PASS_ON": (
        {"s_matched_share": 0.9, "s_dwell_s": 300},
        {"s_matched_share": 0.2, "s_dwell_s": 300},
        (),
    ),
    "NEW_SENDERS_BURST": ({"d_in_uniq_new_1h": 4}, {"d_in_uniq_new_1h": 1}, ()),
    "MANY_SENDERS_IN": ({"d_in_uniq_1h": 6}, {"d_in_uniq_1h": 3}, ()),
    "FAN_OUT_FAST": (
        {"s_out_uniq_1h": 4, "s_pass_ratio_1h": 0.9},
        {"s_out_uniq_1h": 4, "s_pass_ratio_1h": 0.3},
        (),
    ),
    "DEEP_TRAIL": ({"g_trail_depth": 3}, {"g_trail_depth": 1}, ()),
    "NEIGHBOUR_PATTERN": (
        {"g_nbr_in_susp_cnt": 1, "g_nbr_out_susp_cnt": 1, "g_dst_nbr_in_susp_cnt": 1},
        {"g_nbr_in_susp_cnt": 0, "g_nbr_out_susp_cnt": 0, "g_dst_nbr_in_susp_cnt": 0},
        (),
    ),
    "HIGH_PASS_THROUGH": (
        {"s_pass_ratio_1h": 0.9, "s_in_amt_1h": 500},
        {"s_pass_ratio_1h": 0.9, "s_in_amt_1h": 0},
        (),
    ),
    "ROUND_TRIP": (
        {"e_reverse_recent": 1, "s_matched_share": 0.6},
        {"e_reverse_recent": 0, "s_matched_share": 0.6},
        (),
    ),
    "CONNECTED_GROUP": ({"g_flow_comp_size": 6}, {"g_flow_comp_size": 3}, ()),
    "NEW_RECEIVER_ACCOUNT": (
        {"d_age_s": 3600, "d_in_uniq_1h": 3},
        {"d_age_s": 3600, "d_in_uniq_1h": 1},
        (),
    ),
    "DORMANT_RECEIVER": ({"d_idle_s": 1300000}, {"d_idle_s": 1000}, ()),
    "DORMANT_SENDER": (
        {"s_idle_s": 1300000, "s_matched_share": 0.6},
        {"s_idle_s": 1300000, "s_matched_share": 0.2},
        (),
    ),
    "DRAIN_BALANCE": ({"t_s_bal_known": 1, "t_s_drain": 0.97}, {"t_s_bal_known": 1, "t_s_drain": 0.5}, ()),
    "NEAR_LIMIT_AMOUNT": ({"t_near_cap": 1}, {"t_near_cap": 0}, ()),
    "MODEL_SCORE": ({}, {"s_matched_share": 0.9, "s_dwell_s": 300}, ()),
}

RING_ID = "R0000007"


@pytest.fixture(scope="module")
def reasons_engine(tiny_cfg_session):
    """The shipped configs/reasons.toml through the real start-up checks."""
    return ReasonEngine(near_cap=float(tiny_cfg_session.features.near_cap_inr), profile=PROFILE)


def _explain(engine: ReasonEngine, feats: dict, forced: tuple = ()) -> list[dict]:
    return engine.explain(feats, "verify", forced, ring_id=RING_ID, risk=55)


def test_shipped_rule_file_loads_at_start_up(reasons_engine):
    assert set(CASES) == {r["code"] for r in reasons_engine.rules}
    codes = [r["code"] for r in reasons_engine.rules]
    assert len(codes) == len(set(codes))  # no duplicates


@pytest.mark.parametrize("code", sorted(CASES))
def test_positive_fixture_fires_the_code(reasons_engine, code):
    feats, _neg, forced = CASES[code]
    fired = [r["code"] for r in _explain(reasons_engine, feats, forced)]
    assert code in fired, f"{code} should fire, got {fired}"


@pytest.mark.parametrize("code", sorted(CASES))
def test_negative_fixture_does_not_fire_the_code(reasons_engine, code):
    # ring-member codes are never fired by features alone: they come from escalation ("forced"),
    # so the negative fixture is simply "the engine was not asked to force this code"
    _pos, neg, _forced = CASES[code]
    fired = [r["code"] for r in _explain(reasons_engine, neg)]
    assert code not in fired, f"{code} should not fire, got {fired}"


def test_allow_has_no_reasons(reasons_engine):
    feats, _neg, forced = CASES["FAST_PASS_ON"]
    assert _explain(reasons_engine, feats, forced) != []  # sanity: the fixture fires for verify
    assert reasons_engine.explain(feats, "allow", forced, ring_id=RING_ID, risk=55) == []


def test_at_most_three_reasons_ordered_by_weight(reasons_engine):
    feats = {
        "s_matched_share": 0.9,
        "s_dwell_s": 300,  # FAST_PASS_ON, weight 90
        "d_in_uniq_new_1h": 4,  # NEW_SENDERS_BURST, weight 85
        "d_in_uniq_1h": 6,  # MANY_SENDERS_IN, weight 80
        "s_out_uniq_1h": 4,
        "s_pass_ratio_1h": 0.9,  # FAN_OUT_FAST, weight 80
        "g_trail_depth": 3,  # DEEP_TRAIL, weight 75
        "t_near_cap": 1,  # NEAR_LIMIT_AMOUNT, weight 50
    }
    codes = [r["code"] for r in _explain(reasons_engine, feats)]
    assert len(codes) <= 3
    assert codes == ["FAST_PASS_ON", "NEW_SENDERS_BURST", "MANY_SENDERS_IN"]


def test_equal_weights_keep_file_order(reasons_engine):
    # MANY_SENDERS_IN (80) is defined before FAN_OUT_FAST (80) in configs/reasons.toml
    feats = {"d_in_uniq_1h": 6, "s_out_uniq_1h": 4, "s_pass_ratio_1h": 0.9}
    codes = [r["code"] for r in _explain(reasons_engine, feats)]
    assert codes == ["MANY_SENDERS_IN", "FAN_OUT_FAST"]


def test_no_unresolved_placeholders_in_rendered_text(reasons_engine):
    for code, (feats, _neg, forced) in CASES.items():
        for reason in _explain(reasons_engine, feats, forced):
            assert "{" not in reason["text"] and "}" not in reason["text"], (
                f"{code} left a placeholder: {reason['text']}"
            )
            assert reason["text"].strip(), code


def test_no_accusatory_words_anywhere(reasons_engine):
    for rule in reasons_engine.rules:  # raw rule text in the shipped file
        lowered = rule.get("text", "").lower()
        assert not any(w in lowered for w in DENY_WORDS), rule["code"]
    for code, (feats, _neg, forced) in CASES.items():  # rendered output
        for reason in _explain(reasons_engine, feats, forced):
            lowered = reason["text"].lower()
            assert not any(w in lowered for w in DENY_WORDS), f"{code}: {reason['text']}"


def test_fallback_model_score_renders_risk(reasons_engine):
    reasons = _explain(reasons_engine, {})
    assert [r["code"] for r in reasons] == ["MODEL_SCORE"]
    assert "risk 55" in reasons[0]["text"]


def test_ring_member_reasons_carry_the_ring_id_and_weight_order(reasons_engine):
    reasons = reasons_engine.explain({}, "hold", ("RING_MEMBER_IN", "RING_MEMBER_OUT"), ring_id=RING_ID)
    assert [r["code"] for r in reasons] == ["RING_MEMBER_OUT", "RING_MEMBER_IN"]  # 100 before 95
    assert all(RING_ID in r["text"] for r in reasons)


def test_evidence_is_rounded_to_three_significant_digits(reasons_engine):
    reasons = _explain(reasons_engine, {"s_matched_share": 0.87654, "s_dwell_s": 300})
    fast = next(r for r in reasons if r["code"] == "FAST_PASS_ON")
    assert fast["evidence"]["s_matched_share"] == pytest.approx(0.877)
    assert fast["evidence"]["s_dwell_s"] == pytest.approx(300.0)


def test_derived_values_render(reasons_engine):
    near = _explain(reasons_engine, {"t_near_cap": 1})[0]
    assert "INR 49,999" in near["text"]  # near_cap from the tiny config
    nbr = _explain(
        reasons_engine,
        {"g_nbr_in_susp_cnt": 1, "g_nbr_out_susp_cnt": 1, "g_dst_nbr_in_susp_cnt": 1},
    )[0]
    assert nbr["text"].startswith("3 connected accounts")  # g_nbr_total is summed for the rule


def test_when_any_still_requires_every_feature_to_be_present(reasons_engine):
    # actual behaviour: rules are skipped outright when any named clause feature is missing,
    # even for when_any clauses (the serving engine always passes the full feature dict)
    fired = [r["code"] for r in _explain(reasons_engine, {"g_nbr_out_susp_cnt": 1})]
    assert fired == ["MODEL_SCORE"]
    assert "NEIGHBOUR_PATTERN" not in fired


def test_explain_is_deterministic_and_order_stable(reasons_engine, tiny_cfg_session):
    feats = {"s_matched_share": 0.9, "s_dwell_s": 300, "g_trail_depth": 3, "t_near_cap": 1}
    first = _explain(reasons_engine, feats)
    assert _explain(reasons_engine, feats) == first
    fresh = ReasonEngine(near_cap=float(tiny_cfg_session.features.near_cap_inr), profile=PROFILE)
    assert _explain(fresh, feats) == first


# ------------------------------------------------------------------ rule file errors at start-up

BAD_RULE_FILES = {
    "unknown_feature": (
        '[[reason]]\ncode = "BAD"\nweight = 10\nwhen = [["no_such_feature", ">=", 1]]\ntext = "ok"\n',
        "unknown feature",
    ),
    "unknown_filter": (
        '[[reason]]\ncode = "BAD"\nweight = 10\nwhen = [["t_amount", ">=", 1]]\n'
        'text = "value {t_amount|bogus}"\n',
        "unknown filter",
    ),
    "unknown_operator": (
        '[[reason]]\ncode = "BAD"\nweight = 10\nwhen = [["t_amount", "~~", 1]]\ntext = "ok"\n',
        "unknown operator",
    ),
    "deny_word": (
        '[[reason]]\ncode = "BAD"\nweight = 10\nwhen = [["t_amount", ">=", 1]]\n'
        'text = "the fraudster did it"\n',
        "contains the word",
    ),
    "unresolved_placeholder": (
        '[[reason]]\ncode = "BAD"\nweight = 10\nwhen = [["t_amount", ">=", 1]]\n'
        'text = "money from {no_such_feature}"\n',
        "does not resolve",
    ),
}


@pytest.mark.parametrize("name", sorted(BAD_RULE_FILES))
def test_rule_file_errors_fail_at_start_up(tmp_workdir, name):
    body, fragment = BAD_RULE_FILES[name]
    path = tmp_workdir / f"reasons_{name}.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ReasonConfigError) as excinfo:
        ReasonEngine(path=path)
    assert fragment in str(excinfo.value)
