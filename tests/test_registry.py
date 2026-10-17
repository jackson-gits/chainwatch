"""Registry tests (spec 17.2: test_registry.py): counts, order, defaults, detector sets, hash."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from chainwatch.features.registry import (
    ALL_NAMES,
    BY_NAME,
    DETECTOR_GROUPS,
    FEATURES,
    NON_SEMANTIC_KEYS,
    base_detector,
    default_value,
    feature_names,
    names_for_cfg,
    registry_hash,
    registry_hash_for_cfg,
    semantic_features_config,
)

#: canonical order = order of the spec 9.3 tables
EXPECTED_ORDER: list[str] = [
    # T (13)
    "t_amount",
    "t_log_amount",
    "t_type_transfer",
    "t_type_payment",
    "t_type_cash_out",
    "t_type_cash_in",
    "t_type_debit",
    "t_hour",
    "t_is_round",
    "t_near_cap",
    "t_s_drain",
    "t_s_bal_after_zero",
    "t_s_bal_known",
    # S (22)
    "s_age_s",
    "s_idle_s",
    "s_out_cnt_10m",
    "s_out_cnt_1h",
    "s_out_cnt_24h",
    "s_out_uniq_1h",
    "s_out_amt_1h",
    "s_in_cnt_1h",
    "s_in_cnt_24h",
    "s_in_uniq_1h",
    "s_in_amt_1h",
    "s_pass_ratio_1h",
    "s_pass_ratio_24h",
    "s_matched_share",
    "s_dwell_s",
    "s_dwell_ewma_s",
    "s_matched_n",
    "s_matched_uniq_src",
    "s_new_payee",
    "s_new_payee_share_24h",
    "s_round_share_24h",
    "s_amt_vs_median",
    # D (12)
    "d_age_s",
    "d_idle_s",
    "d_first_credit",
    "d_in_cnt_10m",
    "d_in_cnt_1h",
    "d_in_uniq_1h",
    "d_in_uniq_new_1h",
    "d_in_amt_1h",
    "d_out_cnt_1h",
    "d_out_uniq_1h",
    "d_pass_ratio_1h",
    "d_dwell_ewma_s",
    # E (3)
    "e_pair_out_cnt",
    "e_reverse_recent",
    "e_amt_vs_pair_mean",
    # G (9)
    "g_trail_depth",
    "g_funnel_in",
    "g_src_susp",
    "g_dst_susp",
    "g_nbr_in_susp_cnt",
    "g_nbr_out_susp_cnt",
    "g_dst_nbr_in_susp_cnt",
    "g_flow_comp_size",
    "g_flow_comp_edges",
]

#: defaults exactly as in the spec 9.3 tables (numbers, or the name of a [features] key)
EXPECTED_DEFAULTS: dict[str, object] = {
    "t_amount": 0.0,
    "t_log_amount": 0.0,
    "t_type_transfer": 0.0,
    "t_type_payment": 0.0,
    "t_type_cash_out": 0.0,
    "t_type_cash_in": 0.0,
    "t_type_debit": 0.0,
    "t_hour": 0.0,
    "t_is_round": 0.0,
    "t_near_cap": 0.0,
    "t_s_drain": 0.0,
    "t_s_bal_after_zero": 0.0,
    "t_s_bal_known": 0.0,
    "s_age_s": 0.0,
    "s_idle_s": 0.0,
    "s_out_cnt_10m": 1.0,
    "s_out_cnt_1h": 1.0,
    "s_out_cnt_24h": 1.0,
    "s_out_uniq_1h": 1.0,
    "s_out_amt_1h": "amount",
    "s_in_cnt_1h": 0.0,
    "s_in_cnt_24h": 0.0,
    "s_in_uniq_1h": 0.0,
    "s_in_amt_1h": 0.0,
    "s_pass_ratio_1h": 0.0,
    "s_pass_ratio_24h": 0.0,
    "s_matched_share": 0.0,
    "s_dwell_s": "dwell_default_s",
    "s_dwell_ewma_s": "dwell_default_s",
    "s_matched_n": 0.0,
    "s_matched_uniq_src": 0.0,
    "s_new_payee": 1.0,
    "s_new_payee_share_24h": 1.0,
    "s_round_share_24h": 0.0,
    "s_amt_vs_median": 1.0,
    "d_age_s": 0.0,
    "d_idle_s": 0.0,
    "d_first_credit": 1.0,
    "d_in_cnt_10m": 1.0,
    "d_in_cnt_1h": 1.0,
    "d_in_uniq_1h": 1.0,
    "d_in_uniq_new_1h": 1.0,
    "d_in_amt_1h": "amount",
    "d_out_cnt_1h": 0.0,
    "d_out_uniq_1h": 0.0,
    "d_pass_ratio_1h": 0.0,
    "d_dwell_ewma_s": "dwell_default_s",
    "e_pair_out_cnt": 0.0,
    "e_reverse_recent": 0.0,
    "e_amt_vs_pair_mean": 1.0,
    "g_trail_depth": 0.0,
    "g_funnel_in": 1.0,
    "g_src_susp": 0.0,
    "g_dst_susp": 0.0,
    "g_nbr_in_susp_cnt": 0.0,
    "g_nbr_out_susp_cnt": 0.0,
    "g_dst_nbr_in_susp_cnt": 0.0,
    "g_flow_comp_size": 1.0,
    "g_flow_comp_edges": 0.0,
}

#: the 27 P0 features (spec 9.3 counts line)
P0_BY_GROUP = {"T": 13, "S": 6, "D": 4, "E": 0, "G": 4}
EXPECTED_P0 = [n for n in EXPECTED_ORDER if n in {
    "t_amount", "t_log_amount", "t_type_transfer", "t_type_payment", "t_type_cash_out",
    "t_type_cash_in", "t_type_debit", "t_hour", "t_is_round", "t_near_cap", "t_s_drain",
    "t_s_bal_after_zero", "t_s_bal_known", "s_out_cnt_1h", "s_in_uniq_1h", "s_pass_ratio_1h",
    "s_matched_share", "s_dwell_s", "s_new_payee", "d_in_cnt_10m", "d_in_uniq_1h",
    "d_in_uniq_new_1h", "d_pass_ratio_1h", "g_trail_depth", "g_src_susp", "g_dst_susp",
    "g_nbr_in_susp_cnt",
}]


def test_total_and_group_counts():
    """Spec 9.3: T 13 + S 22 + D 12 + E 3 + G 9 = 59."""
    assert len(FEATURES) == 59
    counts = Counter(f.group for f in FEATURES)
    assert dict(counts) == {"T": 13, "S": 22, "D": 12, "E": 3, "G": 9}


def test_canonical_order_matches_spec_tables():
    assert [f.name for f in FEATURES] == EXPECTED_ORDER
    assert ALL_NAMES == tuple(EXPECTED_ORDER)


def test_p0_counts_per_group():
    """Spec 9.3: P0 = T 13, S 6, D 4, E 0, G 4 = 27."""
    p0 = [f for f in FEATURES if f.priority == "P0"]
    assert len(p0) == 27
    counts = Counter(f.group for f in p0)
    assert dict(counts) == {g: n for g, n in P0_BY_GROUP.items() if n}  # E has no P0 feature
    assert counts["E"] == 0


def test_detector_column_counts_p1():
    assert len(feature_names("B0")) == 13
    assert len(feature_names("B1")) == 50
    assert len(feature_names("G2")) == 59
    assert feature_names("B0") == [n for n in ALL_NAMES if BY_NAME[n].group == "T"]
    assert all(not n.startswith("g_") for n in feature_names("B1"))
    assert feature_names("B1") == [n for n in ALL_NAMES if BY_NAME[n].group in ("T", "S", "D", "E")]


def test_detector_column_counts_p0():
    assert len(feature_names("B0", priority="P0")) == 13
    assert len(feature_names("B1", priority="P0")) == 23  # T13 + S6 + D4 + E0
    assert feature_names("G2", priority="P0") == EXPECTED_P0
    assert len(feature_names("G2", priority="P0")) == 27


def test_detector_vectors_are_canonical_subsequences():
    for det in ("B0", "B1", "G2"):
        names = feature_names(det)
        assert names == [n for n in ALL_NAMES if n in set(names)]


def test_plus_r_variant_and_base_detector():
    assert base_detector("G2+R") == "G2"
    assert feature_names("G2+R") == feature_names("G2")
    assert feature_names("B1+R") == feature_names("B1")


def test_unknown_detector_or_flags_raise():
    with pytest.raises(ValueError):
        feature_names("B9")
    with pytest.raises(ValueError):
        feature_names("G2", disabled=["no_such_feature"])
    with pytest.raises(ValueError):
        feature_names("G2", lower_to_p1=["no_such_feature"])


def test_disabled_features_are_removed():
    names = feature_names("G2", disabled=["g_flow_comp_size", "g_flow_comp_edges"])
    assert "g_flow_comp_size" not in names
    assert len(names) == 57


def test_lower_to_p1_only_shrinks_p0():
    p0 = feature_names("G2", priority="P0")
    demoted = feature_names("G2", priority="P0", lower_to_p1=["s_matched_share"])
    assert "s_matched_share" not in demoted
    assert len(demoted) == len(p0) - 1
    assert feature_names("G2", lower_to_p1=["s_matched_share"]) == feature_names("G2")  # P1 keeps all 59


def test_defaults_registry_values():
    assert {f.name: f.default for f in FEATURES} == EXPECTED_DEFAULTS


def test_default_value_resolution(tiny_cfg_session):
    fcfg = dict(tiny_cfg_session.features)
    assert default_value("t_amount", fcfg) == 0.0
    assert default_value("s_dwell_s", fcfg) == float(tiny_cfg_session.features.dwell_default_s)
    assert default_value("s_out_amt_1h", fcfg, amount=42.5) == 42.5
    assert default_value("s_out_amt_1h", fcfg, amount=None) == 0.0
    assert default_value("d_in_amt_1h", fcfg, amount=7.0) == 7.0


def test_priorities_are_valid_and_docstrings_present():
    assert {f.priority for f in FEATURES} == {"P0", "P1"}
    assert all(f.doc for f in FEATURES)
    assert all(f.group in ("T", "S", "D", "E", "G") for f in FEATURES)


def test_detector_groups_mapping():
    assert DETECTOR_GROUPS == {"B0": ("T",), "B1": ("T", "S", "D", "E"), "G2": ("T", "S", "D", "E", "G")}


def test_registry_hash_is_stable_and_formatted():
    names = feature_names("G2")
    h = registry_hash(names, {"x": 1})
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", h)
    assert h == registry_hash(list(names), {"x": 1})


def test_registry_hash_changes_with_names_or_order():
    names = feature_names("G2")
    assert registry_hash(names, {"x": 1}) != registry_hash(names[:-1], {"x": 1})
    assert registry_hash(names, {"x": 1}) != registry_hash(list(reversed(names)), {"x": 1})


def test_registry_hash_changes_with_semantic_config(tiny_cfg_session):
    h0 = registry_hash_for_cfg(tiny_cfg_session, "G2")
    changed = tiny_cfg_session.with_overrides({"features.fifo_lookback_s": 43200})
    assert registry_hash_for_cfg(changed, "G2") != h0
    tz_changed = tiny_cfg_session.with_overrides({"project.tz_offset_s": 0})
    assert registry_hash_for_cfg(tz_changed, "G2") != h0  # tz feeds t_hour


def test_registry_hash_ignores_non_semantic_config(tiny_cfg_session):
    h0 = registry_hash_for_cfg(tiny_cfg_session, "G2")
    late = tiny_cfg_session.with_overrides({"features.max_lateness_s": 123})
    assert registry_hash_for_cfg(late, "G2") == h0
    assert "max_lateness_s" in NON_SEMANTIC_KEYS and "priority" in NON_SEMANTIC_KEYS


def test_registry_hash_changes_with_priority_and_flags(tiny_cfg_session):
    h1 = registry_hash_for_cfg(tiny_cfg_session, "G2")
    p0 = tiny_cfg_session.with_overrides({"features.priority": "P0"})
    assert registry_hash_for_cfg(p0, "G2") != h1  # the P0 column set differs
    flagged = tiny_cfg_session.with_overrides({"feature_flags": {"disabled": ["g_flow_comp_edges"]}})
    assert registry_hash_for_cfg(flagged, "G2") != h1


def test_names_for_cfg_matches_feature_names(tiny_cfg_session):
    assert names_for_cfg(tiny_cfg_session, "G2") == feature_names("G2")
    p0 = tiny_cfg_session.with_overrides({"features.priority": "P0"})
    assert names_for_cfg(p0, "G2") == feature_names("G2", priority="P0")


def test_semantic_features_config_excludes_non_semantic(tiny_cfg_session):
    sem = semantic_features_config(tiny_cfg_session)
    assert "priority" not in sem
    assert "max_lateness_s" not in sem
    assert "fifo_lookback_s" in sem
    assert sem["tz_offset_s"] == tiny_cfg_session.project.tz_offset_s
