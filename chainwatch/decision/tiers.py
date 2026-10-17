"""Recommended actions: allow, verify, hold (spec 12.1). Recommendations only; nothing is blocked
automatically."""

from __future__ import annotations

from dataclasses import dataclass

TIER_ORDER = {"allow": 0, "verify": 1, "hold": 2}
RECOMMENDED_ACTION = {
    "allow": "No action recommended.",
    "verify": "Recommend a step-up check with the sender (for example a confirmation prompt) before this "
    "payment completes.",
    "hold": "Recommend holding this payment for review by a fraud analyst before the money leaves "
    "the account.",
}


@dataclass(frozen=True)
class Thresholds:
    verify: float
    hold: float
    edge: float
    ring_alert: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> Thresholds:
        ra = d.get("ring_alert")
        return cls(
            float(d["verify"]),
            float(d["hold"]),
            float(d.get("edge", d["verify"])),
            1.0 if ra is None else float(ra),
        )


def tier_from_score(score: float, thr: Thresholds) -> str:
    """Ties go to the higher tier."""
    if score >= thr.hold:
        return "hold"
    if score >= thr.verify:
        return "verify"
    return "allow"


def decide(base_score: float, thr: Thresholds, floor: str | None) -> tuple[str, float]:
    """Return (final tier, effective score). A ring-escalation ``floor`` can only raise the tier."""
    tier = tier_from_score(base_score, thr)
    if floor and TIER_ORDER[floor] > TIER_ORDER[tier]:
        tier = floor
        base_score = max(base_score, thr.hold if floor == "hold" else thr.verify)
    return tier, base_score
