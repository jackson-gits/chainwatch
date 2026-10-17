"""Feature registry: the single source of truth for names, order, groups, defaults and priorities (spec 9.8).

Canonical order is the order of the tables in spec section 9.3. ``B0`` uses group T, ``B1`` uses T+S+D+E and
``G2`` uses all groups.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    group: str  # "T", "S", "D", "E", "G"
    default: float | str  # a number, or the name of a [features] value such as "dwell_default_s"
    priority: str  # "P0" or "P1"
    doc: str


def _f(name, group, default, priority, doc):
    return FeatureSpec(name, group, default, priority, doc)


FEATURES: tuple[FeatureSpec, ...] = (
    # ---- T: transaction only (13) ----
    _f("t_amount", "T", 0.0, "P0", "amount"),
    _f("t_log_amount", "T", 0.0, "P0", "log1p(amount)"),
    _f("t_type_transfer", "T", 0.0, "P0", "one-hot type TRANSFER"),
    _f("t_type_payment", "T", 0.0, "P0", "one-hot type PAYMENT"),
    _f("t_type_cash_out", "T", 0.0, "P0", "one-hot type CASH_OUT"),
    _f("t_type_cash_in", "T", 0.0, "P0", "one-hot type CASH_IN"),
    _f("t_type_debit", "T", 0.0, "P0", "one-hot type DEBIT"),
    _f("t_hour", "T", 0.0, "P0", "local hour 0..23"),
    _f("t_is_round", "T", 0.0, "P0", "amount >= round_min_amount and a multiple of 100"),
    _f("t_near_cap", "T", 0.0, "P0", "near_cap_lo_frac x near_cap_inr <= amount <= near_cap_inr"),
    _f("t_s_drain", "T", 0.0, "P0", "clip(amount / src_bal_before, 0, 1.5) when known and > 0"),
    _f("t_s_bal_after_zero", "T", 0.0, "P0", "src_bal_after known and below 1.0"),
    _f("t_s_bal_known", "T", 0.0, "P0", "src_bal_before known"),
    # ---- S: sender account history (22) ----
    _f("s_age_s", "S", 0.0, "P1", "ts - first_seen(S); 0 if never seen"),
    _f("s_idle_s", "S", 0.0, "P1", "ts - last_seen(S); 0 if never seen"),
    _f("s_out_cnt_10m", "S", 1.0, "P1", "S debits in 10 min (+cur)"),
    _f("s_out_cnt_1h", "S", 1.0, "P0", "S debits in 1 h (+cur)"),
    _f("s_out_cnt_24h", "S", 1.0, "P1", "S debits in 24 h (+cur)"),
    _f("s_out_uniq_1h", "S", 1.0, "P1", "distinct receivers of S debits in 1 h (+cur)"),
    _f("s_out_amt_1h", "S", "amount", "P1", "sum of S debits in 1 h (+cur)"),
    _f("s_in_cnt_1h", "S", 0.0, "P1", "credits to S in 1 h (prior)"),
    _f("s_in_cnt_24h", "S", 0.0, "P1", "credits to S in 24 h (prior)"),
    _f("s_in_uniq_1h", "S", 0.0, "P0", "distinct payers of S in 1 h (prior)"),
    _f("s_in_amt_1h", "S", 0.0, "P1", "sum of credits to S in 1 h (prior)"),
    _f("s_pass_ratio_1h", "S", 0.0, "P0", "clip(s_out_amt_1h / s_in_amt_1h, 0, 2)"),
    _f("s_pass_ratio_24h", "S", 0.0, "P1", "same over 24 h (out +cur)"),
    _f("s_matched_share", "S", 0.0, "P0", "share of this debit covered by earlier credits (FIFO)"),
    _f("s_dwell_s", "S", "dwell_default_s", "P0", "amount-weighted age of consumed credits"),
    _f("s_dwell_ewma_s", "S", "dwell_default_s", "P1", "EWMA of S previous matched dwell (prior)"),
    _f("s_matched_n", "S", 0.0, "P1", "credits consumed by this debit"),
    _f("s_matched_uniq_src", "S", 0.0, "P1", "distinct senders of consumed credits"),
    _f("s_new_payee", "S", 1.0, "P0", "S never paid this receiver before (within payee memory)"),
    _f("s_new_payee_share_24h", "S", 1.0, "P1", "share of S debits in 24 h (+cur) to payees new at the time"),
    _f("s_round_share_24h", "S", 0.0, "P1", "share of S debits in 24 h (+cur) with t_is_round"),
    _f("s_amt_vs_median", "S", 1.0, "P1", "clip(amount / median of last debit amounts, 0, 50)"),
    # ---- D: receiver account history (12) ----
    _f("d_age_s", "D", 0.0, "P1", "ts - first_seen(D); 0 if never seen"),
    _f("d_idle_s", "D", 0.0, "P1", "ts - last_seen(D); 0 if never seen"),
    _f("d_first_credit", "D", 1.0, "P1", "D never received a credit before"),
    _f("d_in_cnt_10m", "D", 1.0, "P0", "credits to D in 10 min (+cur)"),
    _f("d_in_cnt_1h", "D", 1.0, "P1", "credits to D in 1 h (+cur)"),
    _f("d_in_uniq_1h", "D", 1.0, "P0", "distinct payers of D in 1 h (+cur)"),
    _f("d_in_uniq_new_1h", "D", 1.0, "P0", "first-time payers of D in 1 h (+cur)"),
    _f("d_in_amt_1h", "D", "amount", "P1", "sum of credits to D in 1 h (+cur)"),
    _f("d_out_cnt_1h", "D", 0.0, "P1", "debits by D in 1 h (prior)"),
    _f("d_out_uniq_1h", "D", 0.0, "P1", "distinct receivers of D debits in 1 h (prior)"),
    _f("d_pass_ratio_1h", "D", 0.0, "P0", "clip(D out amount 1 h / D credit amount 1 h, 0, 2), prior"),
    _f("d_dwell_ewma_s", "D", "dwell_default_s", "P1", "D dwell EWMA (prior)"),
    # ---- E: the pair (3) ----
    _f("e_pair_out_cnt", "E", 0.0, "P1", "prior S-to-D transactions in S counterparty memory"),
    _f("e_reverse_recent", "E", 0.0, "P1", "S received from D within the last 24 h"),
    _f("e_amt_vs_pair_mean", "E", 1.0, "P1", "clip(amount / mean prior S-to-D amount, 0, 50)"),
    # ---- G: graph (9), G2 only ----
    _f("g_trail_depth", "G", 0.0, "P0", "depth of this transaction in a money trail"),
    _f("g_funnel_in", "G", 1.0, "P1", "estimated origin senders feeding this debit"),
    _f("g_src_susp", "G", 0.0, "P0", "S currently flagged suspicious"),
    _f("g_dst_susp", "G", 0.0, "P0", "D currently flagged suspicious"),
    _f("g_nbr_in_susp_cnt", "G", 0.0, "P0", "S payers in 1 h that are suspicious"),
    _f("g_nbr_out_susp_cnt", "G", 0.0, "P1", "S payees in 1 h (excluding D) that are suspicious"),
    _f("g_dst_nbr_in_susp_cnt", "G", 0.0, "P1", "D payers in 1 h (excluding S) that are suspicious"),
    _f("g_flow_comp_size", "G", 1.0, "P1", "max flow-component size of S and D"),
    _f("g_flow_comp_edges", "G", 0.0, "P1", "flow edges in the larger component"),
)

ALL_NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES)
BY_NAME: dict[str, FeatureSpec] = {f.name: f for f in FEATURES}
DETECTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "B0": ("T",),
    "B1": ("T", "S", "D", "E"),
    "G2": ("T", "S", "D", "E", "G"),
}
# [features] keys that do not change feature semantics (excluded from the registry hash)
NON_SEMANTIC_KEYS = frozenset({"max_lateness_s", "priority"})
# caps that only bound memory; changing them changes the hash too (they change saturation behaviour)


def base_detector(name: str) -> str:
    """``"G2+R"`` -> ``"G2"``."""
    return name.split("+", 1)[0]


def feature_names(
    detector: str,
    priority: str = "P1",
    disabled: tuple[str, ...] | list[str] = (),
    lower_to_p1: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Ordered feature names used by a detector (``B0``, ``B1``, ``G2`` or a ``+R`` variant)."""
    det = base_detector(detector)
    if det not in DETECTOR_GROUPS:
        raise ValueError(f"unknown detector {detector!r}")
    groups = DETECTOR_GROUPS[det]
    disabled_s, lower_s = set(disabled), set(lower_to_p1)
    unknown = (disabled_s | lower_s) - set(ALL_NAMES)
    if unknown:
        raise ValueError(f"unknown features in feature flags: {sorted(unknown)}")
    out = []
    for f in FEATURES:
        if f.group not in groups or f.name in disabled_s:
            continue
        prio = "P1" if f.name in lower_s else f.priority
        if priority == "P0" and prio != "P0":
            continue
        out.append(f.name)
    return out


def names_for_cfg(cfg, detector: str) -> list[str]:
    flags = cfg.get("feature_flags", {}) or {}
    return feature_names(
        detector, cfg.features.priority, flags.get("disabled", []), flags.get("lower_to_p1", [])
    )


def default_value(name: str, cfg_features: dict, amount: float | None = None) -> float:
    d = BY_NAME[name].default
    if d == "amount":
        return float(amount or 0.0)
    if isinstance(d, str):
        return float(cfg_features[d])
    return float(d)


def semantic_features_config(cfg) -> dict[str, Any]:
    """The configuration values that change feature semantics (input of the registry hash)."""
    out = {k: v for k, v in dict(cfg.features).items() if k not in NON_SEMANTIC_KEYS}
    out["tz_offset_s"] = cfg.project.tz_offset_s
    return out


def registry_hash(names: list[str], cfg_features: dict[str, Any]) -> str:
    """SHA-256 over the ordered names and the semantic [features] values."""
    payload = json.dumps(
        {"names": list(names), "features": cfg_features}, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def registry_hash_for_cfg(cfg, detector: str) -> str:
    return registry_hash(names_for_cfg(cfg, detector), semantic_features_config(cfg))
