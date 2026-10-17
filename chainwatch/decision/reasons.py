"""Plain-language reason codes (spec 12.2, 12.3).

Rules live in ``configs/reasons.toml`` and are run by a tiny interpreter (no ``eval``). Reasons describe
observed values; they never accuse anyone and never use model internals.
"""

from __future__ import annotations

import math
import re
import statistics
from pathlib import Path

from chainwatch.config import CONFIG_DIR, read_toml
from chainwatch.features.registry import ALL_NAMES

OPS = (">=", "<=", ">", "<", "==", "rank>=", "rank<=")
FILTERS = ("int", "pct", "duration", "days", "inr", "rank_pct", "rank_inv_pct")
DERIVED = ("g_nbr_total", "near_cap", "ring_id", "ring_score", "risk")
DENY_WORDS = ("criminal", "fraudster", "guilty", "thief", "scammer", "launder", "scam account")
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)(?:\|([a-z_]+))?\}")


class ReasonConfigError(ValueError):
    pass


def sig3(x: float) -> float:
    """Round to 3 significant digits."""
    if x == 0 or not math.isfinite(x):
        return float(x)
    return float(f"{x:.3g}")


def fmt_duration(s: float) -> str:
    s = max(0.0, float(s))
    if s < 90:
        return f"{int(round(s))} seconds"
    if s < 90 * 60:
        return f"{int(round(s / 60))} minutes"
    if s < 48 * 3600:
        return f"{s / 3600:.1f} hours"
    return f"{int(round(s / 86400))} days"


def fmt_inr(v: float) -> str:
    return f"INR {int(round(float(v))):,}"


def fmt_pct(v: float) -> str:
    return f"{int(round(float(v) * 100))}%"


class ReasonEngine:
    """Loads and checks rule definitions, then explains decisions."""

    def __init__(self, path: str | Path | None = None, near_cap: float = 49999.0, profile=None):
        path = Path(path) if path else CONFIG_DIR / "reasons.toml"
        self.rules = read_toml(path)["reason"]
        self.near_cap = float(near_cap)
        self.profile = profile
        self._check()

    def _check(self) -> None:
        known = set(ALL_NAMES) | set(DERIVED)
        problems = []
        codes = set()
        for r in self.rules:
            code = r.get("code", "?")
            if code in codes:
                problems.append(f"{code}: duplicate code")
            codes.add(code)
            for clause in list(r.get("when", [])) + list(r.get("when_any", [])):
                if len(clause) != 3:
                    problems.append(f"{code}: clause must have 3 parts: {clause}")
                    continue
                feat, op, _ = clause
                if feat not in known:
                    problems.append(f"{code}: unknown feature {feat!r}")
                if op not in OPS:
                    problems.append(f"{code}: unknown operator {op!r}")
                if op.startswith("rank") and self.profile is not None and feat not in self.profile.features:
                    problems.append(f"{code}: no benign profile for {feat!r}")
            for name, filt in _PLACEHOLDER.findall(r.get("text", "")):
                if name not in known:
                    problems.append(f"{code}: placeholder {name!r} does not resolve")
                if filt and filt not in FILTERS:
                    problems.append(f"{code}: unknown filter {filt!r}")
            for ev in r.get("evidence", []):
                if ev not in known:
                    problems.append(f"{code}: unknown evidence feature {ev!r}")
            lowered = r.get("text", "").lower()
            for w in DENY_WORDS:
                if w in lowered:
                    problems.append(f"{code}: text contains the word {w!r}")
        if problems:
            raise ReasonConfigError("reason rules invalid:\n  - " + "\n  - ".join(problems))

    def _rank(self, feat: str, value: float) -> float:
        if self.profile is None or feat not in self.profile.features:
            return 0.5
        return self.profile.rank(feat, value)

    def _clause(self, f: dict, clause) -> bool:
        feat, op, val = clause
        x = f[feat]
        if op == ">=":
            return x >= val
        if op == "<=":
            return x <= val
        if op == ">":
            return x > val
        if op == "<":
            return x < val
        if op == "==":
            return x == val
        if op == "rank>=":
            return self._rank(feat, x) >= val
        return self._rank(feat, x) <= val

    def _render(self, text: str, f: dict) -> str:
        def sub(m: re.Match) -> str:
            name, filt = m.group(1), m.group(2)
            v = f[name]
            if filt is None:
                return str(v)
            if filt == "int":
                return str(int(round(float(v))))
            if filt == "pct":
                return fmt_pct(v)
            if filt == "duration":
                return fmt_duration(v)
            if filt == "days":
                return str(int(round(float(v) / 86400)))
            if filt == "inr":
                return fmt_inr(v)
            if filt == "rank_pct":
                return str(int(round(self._rank(name, v) * 100)))
            return str(int(round((1.0 - self._rank(name, v)) * 100)))

        return _PLACEHOLDER.sub(sub, text)

    def values(self, features: dict, ring_id: str | None, ring_score: float | None, risk: int) -> dict:
        f = dict(features)
        f["g_nbr_total"] = (
            features.get("g_nbr_in_susp_cnt", 0.0)
            + features.get("g_nbr_out_susp_cnt", 0.0)
            + features.get("g_dst_nbr_in_susp_cnt", 0.0)
        )
        f["near_cap"] = self.near_cap
        f["ring_id"] = ring_id or "-"
        f["ring_score"] = ring_score or 0.0
        f["risk"] = risk
        return f

    def explain(
        self,
        features: dict,
        decision: str,
        forced: list[str] | tuple[str, ...] = (),
        ring_id: str | None = None,
        ring_score: float | None = None,
        risk: int = 0,
        max_reasons: int = 3,
    ) -> list[dict]:
        """Up to ``max_reasons`` reasons for a non-allow decision, highest weight first, then file order."""
        if decision == "allow":
            return []
        f = self.values(features, ring_id, ring_score, risk)
        hits = []
        fallback = None
        for order, r in enumerate(self.rules):
            if r.get("fallback"):
                fallback = r
                continue
            if r.get("forced_only"):
                if r["code"] in forced:
                    hits.append((-int(r["weight"]), order, r))
                continue
            when, when_any = r.get("when", []), r.get("when_any", [])
            missing = any(c[0] not in f for c in list(when) + list(when_any))
            if missing:
                continue
            if all(self._clause(f, c) for c in when) and (
                not when_any or any(self._clause(f, c) for c in when_any)
            ):
                hits.append((-int(r["weight"]), order, r))
        hits.sort(key=lambda h: (h[0], h[1]))
        chosen = [h[2] for h in hits[:max_reasons]]
        if not chosen and fallback is not None:
            chosen = [fallback]
        return [
            {
                "code": r["code"],
                "text": self._render(r["text"], f),
                "evidence": {e: sig3(float(f[e])) for e in r.get("evidence", []) if e in f},
            }
            for r in chosen
        ]


def ring_reasons(snap: dict, relay_delays: list[float], relay_in: float, relay_out: float) -> list[dict]:
    """Ring-level reasons (spec 12.3): structure, relay speed, exit."""
    roles = [a["role"] for a in snap["accounts"]]
    span = max(0.0, snap["last_ts"] - snap["first_ts"])
    out = [
        {
            "code": "RING_STRUCTURE",
            "text": f"{snap['n_accounts']} accounts are linked by {snap['n_edges']} flagged transfers within "
            f"{fmt_duration(span)}: {roles.count('entry')} entry, {snap['n_relays']} relay and "
            f"{roles.count('exit')} exit account(s).",
            "evidence": {
                "n_accounts": float(snap["n_accounts"]),
                "n_edges": float(snap["n_edges"]),
                "n_relays": float(snap["n_relays"]),
            },
        }
    ]
    if relay_delays and relay_in > 0:
        med = statistics.median(relay_delays)
        out.append(
            {
                "code": "RING_RELAY_SPEED",
                "text": f"Relay accounts passed on {fmt_pct(relay_out / relay_in)} of the money they "
                f"received, a median of {fmt_duration(med)} after it arrived.",
                "evidence": {
                    "relay_pass_share": sig3(relay_out / relay_in),
                    "relay_median_delay_s": sig3(med),
                },
            }
        )
    out.append(
        {
            "code": "RING_EXIT",
            "text": f"{fmt_inr(snap['exit_amount'])} has reached exit accounts (cash-out or merchant "
            f"payment) so far; longest chain {snap['longest_chain']} transfers.",
            "evidence": {
                "exit_amount": sig3(float(snap["exit_amount"])),
                "longest_chain": float(snap["longest_chain"]),
            },
        }
    )
    return out
