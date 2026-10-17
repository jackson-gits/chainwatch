"""Evaluation metrics (spec 14.3, 14.4). Train env (numpy).

Transaction level: PR-AUC, ROC-AUC, recall at FPR (curve and deployed forms), alert volume.
Ring level: caught, time to catch, caught before exit, hops before exit, prevented-cash-out bounds,
false rings.
Plus account-level false-alert rates by archetype, Wilson intervals and bootstrap helpers.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

ROLE_MULE, ROLE_COLLECTOR, ROLE_SINK = 2, 3, 4


# ---------------------------------------------------------------------------------------------- basics
def average_precision(y: np.ndarray, s: np.ndarray) -> float:
    """Average precision (step-wise, as scikit-learn); ties are grouped."""
    y = np.asarray(y).astype(np.int64)
    s = np.asarray(s, dtype=np.float64)
    n_pos = int(y.sum())
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    distinct = np.r_[np.flatnonzero(np.diff(s_sorted)), y.size - 1]
    tps = np.cumsum(y_sorted)[distinct]
    fps = (distinct + 1) - tps
    precision = tps / (tps + fps)
    recall = tps / n_pos
    r_prev = np.r_[0.0, recall[:-1]]
    return float(np.sum((recall - r_prev) * precision))


def roc_auc(y: np.ndarray, s: np.ndarray) -> float:
    y = np.asarray(y).astype(np.int64)
    s = np.asarray(s, dtype=np.float64)
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y, s))
    except ImportError:  # pragma: no cover - fallback with average ranks for ties
        pass
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(s.size, dtype=np.float64)
    s_sorted = s[order]
    i = 0
    while i < s.size:  # average ranks for ties
        j = i
        while j + 1 < s.size and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def recall_at_fpr_curve(y: np.ndarray, s: np.ndarray, fpr: float) -> dict:
    """Optimistic, threshold-free comparison: threshold chosen on the *test* negatives."""
    neg, pos = s[y == 0], s[y == 1]
    if neg.size == 0 or pos.size == 0:
        return {"recall": None, "fpr": None, "threshold": None}
    thr = float(np.quantile(neg, 1.0 - fpr, method="higher"))
    return {"recall": float(np.mean(pos >= thr)), "fpr": float(np.mean(neg >= thr)), "threshold": thr}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def prop(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "p": (k / n) if n else None, "ci95": [lo, hi]}


def transaction_metrics(y: np.ndarray, score: np.ndarray, tier: np.ndarray, fpr_points) -> dict:
    """Deployed form uses the final tiers (validation-fixed thresholds); curve form uses raw scores."""
    y = np.asarray(y).astype(np.int64)
    tier = np.asarray(tier)
    pos, neg = y == 1, y == 0
    n = y.size
    flagged = tier >= 1
    out = {
        "n": int(n),
        "n_pos": int(pos.sum()),
        "n_neg": int(neg.sum()),
        "pr_auc": average_precision(y, score),
        "roc_auc": roc_auc(y, score),
        "deployed": {
            "recall": prop(int((flagged & pos).sum()), int(pos.sum())),
            "fpr": float(np.mean(flagged[neg])) if neg.any() else None,
            "hold_fpr": float(np.mean(tier[neg] >= 2)) if neg.any() else None,
            "precision": float((flagged & pos).sum() / flagged.sum()) if flagged.any() else None,
        },
        "curve": {str(f): recall_at_fpr_curve(y, score, f) for f in fpr_points},
        "alerts_per_10k": {
            "verify": float(np.sum(tier == 1) / n * 1e4) if n else 0.0,
            "hold": float(np.sum(tier == 2) / n * 1e4) if n else 0.0,
        },
    }
    return out


# ---------------------------------------------------------------------------------------------- rings
def ring_outcomes(
    rings: list[dict],
    stream: dict,
    tier: np.ndarray,
    test_start: float,
    test_end: float,
    min_accounts: int = 2,
    min_tier: int = 1,
) -> list[dict]:
    """Per ground-truth ring whose first transaction lies in the test split: catch time and
    early-warning facts.

    ``stream`` holds aligned arrays: ``ts``, ``src``, ``dst``, ``ring_id``, ``hop``, ``dst_role``, ``amount``.
    A ring is caught when ``min_accounts`` distinct mule/collector accounts of the ring have appeared as
    sender or receiver of a transaction whose final tier is at least ``min_tier``; ``tau`` is the time of
    that transaction.
    """
    sel = [r for r in rings if test_start <= r["first_ts"] < test_end]
    members: dict[str, list[str]] = defaultdict(list)
    for r in sel:
        for a in r["accounts"].get("mules", []) + r["accounts"].get("collectors", []):
            members[a].append(r["ring_id"])
    got: dict[str, set] = {r["ring_id"]: set() for r in sel}
    tau: dict[str, float] = {}
    ts = stream["ts"]
    hit_idx = np.flatnonzero(np.asarray(tier) >= min_tier)
    src, dst = stream["src"], stream["dst"]
    for i in hit_idx.tolist():
        for acct in (src[i], dst[i]):
            for rid in members.get(acct, ()):
                if rid in tau:
                    continue
                got[rid].add(acct)
                if len(got[rid]) >= min_accounts:
                    tau[rid] = float(ts[i])
    # per-ring transactions (hop, ts, amounts, sink rows) from the stream labels
    rid_arr = stream["ring_id"]
    idx_by_ring: dict[str, np.ndarray] = {}
    ring_rows = np.flatnonzero(rid_arr >= 0)
    if ring_rows.size:
        rr = rid_arr[ring_rows]
        order = np.argsort(rr, kind="mergesort")
        rr_sorted = rr[order]
        bounds = np.flatnonzero(np.diff(rr_sorted)) + 1
        for chunk in np.split(ring_rows[order], bounds):
            idx_by_ring[str(int(rid_arr[chunk[0]]))] = np.sort(chunk)
    out = []
    for r in sel:
        rid = r["ring_id"]
        rows = idx_by_ring.get(rid, np.zeros(0, dtype=np.int64))
        t = tau.get(rid)
        exit_ts, exit_hop = r.get("exit_ts"), r.get("exit_hop")
        sink_rows = rows[stream["dst_role"][rows] == ROLE_SINK] if rows.size else rows
        sink_amt = float(stream["amount"][sink_rows].sum()) if sink_rows.size else 0.0
        hops_remaining = None
        if t is not None and exit_hop is not None and rows.size:
            done = rows[stream["ts"][rows] <= t]
            max_done = int(stream["hop"][done].max()) if done.size else -1
            hops_remaining = int(exit_hop) - max_done
        upper = lower = None
        if sink_amt > 0:
            after = sink_rows[stream["ts"][sink_rows] > t] if t is not None else sink_rows[:0]
            upper = float(stream["amount"][after].sum()) / sink_amt
            held = sink_rows[np.asarray(tier)[sink_rows] >= 2]
            lower = float(stream["amount"][held].sum()) / sink_amt
        n_acc = int(r.get("n_accounts", 0))
        out.append(
            {
                "ring_id": rid,
                "typology": r["typology"],
                "n_accounts": n_acc,
                "size_bin": "<=4" if n_acc <= 4 else ("5-8" if n_acc <= 8 else ">8"),
                "first_ts": r["first_ts"],
                "caught": t is not None,
                "tau": t,
                "ttc_min": (t - r["first_ts"]) / 60.0 if t is not None else None,
                "exit_ts": exit_ts,
                "has_exit": exit_ts is not None,
                "before_exit": (t is not None and exit_ts is not None and t < exit_ts)
                if exit_ts is not None
                else None,
                "min_before_exit": (exit_ts - t) / 60.0 if (t is not None and exit_ts is not None) else None,
                "hops_remaining": hops_remaining,
                "prevented_upper": upper,
                "prevented_lower": lower,
            }
        )
    return out


def summarize_rings(outcomes: list[dict]) -> dict:
    n = len(outcomes)
    caught = [o for o in outcomes if o["caught"]]
    ttc = np.array([o["ttc_min"] for o in caught], dtype=float)
    with_exit = [o for o in outcomes if o["has_exit"]]
    before = sum(1 for o in with_exit if o["before_exit"])
    hops = np.array([o["hops_remaining"] for o in caught if o["hops_remaining"] is not None], dtype=float)
    mbe = np.array([o["min_before_exit"] for o in caught if o["min_before_exit"] is not None], dtype=float)
    up = [o["prevented_upper"] for o in outcomes if o["prevented_upper"] is not None]
    lo = [o["prevented_lower"] for o in outcomes if o["prevented_lower"] is not None]
    return {
        "n_rings": n,
        "recall": prop(len(caught), n),
        "ttc_min": {
            "median": float(np.median(ttc)) if ttc.size else None,
            "q25": float(np.quantile(ttc, 0.25)) if ttc.size else None,
            "q75": float(np.quantile(ttc, 0.75)) if ttc.size else None,
        },
        "within_15min": prop(int(np.sum(ttc <= 15)) if ttc.size else 0, n),
        "within_1h": prop(int(np.sum(ttc <= 60)) if ttc.size else 0, n),
        "caught_before_exit": prop(before, len(with_exit)),
        "rings_without_exit": n - len(with_exit),
        "min_before_exit_median": float(np.median(mbe)) if mbe.size else None,
        "hops_remaining": {
            "median": float(np.median(hops)) if hops.size else None,
            "mean": float(np.mean(hops)) if hops.size else None,
            "hist": {str(int(k)): int(v) for k, v in zip(*np.unique(hops, return_counts=True), strict=False)}
            if hops.size
            else {},
        },
        "prevented_upper_mean": float(np.mean(up)) if up else None,
        "prevented_lower_mean": float(np.mean(lo)) if lo else None,
    }


def breakdown(outcomes: list[dict], key: str) -> dict:
    groups: dict[str, list] = defaultdict(list)
    for o in outcomes:
        groups[str(o[key])].append(o)
    return {k: summarize_rings(v) for k, v in sorted(groups.items())}


def false_rings(
    log_: dict, label_of: dict, accounts_legit: set, start: float, end: float, purity_min: float, days: float
) -> dict:
    """Ring alerts raised in [start, end) whose purity (share of flagged edges that are fraud) is below
    the bar."""
    alerts = [lg for lg in log_.values() if lg["alert_ts"] is not None and start <= lg["alert_ts"] < end]
    n_false = 0
    legit_touched_by_day: dict[int, set] = defaultdict(set)
    for lg in alerts:
        ids = lg["event_ids"]
        pur = sum(label_of.get(e, 0) for e in ids) / len(ids) if ids else 0.0
        if pur < purity_min:
            n_false += 1
        d = int(lg["alert_ts"] // 86400)
        legit_touched_by_day[d].update(a for a in lg["accounts"] if a in accounts_legit)
    return {
        "alerts": len(alerts),
        "false": n_false,
        "false_per_day": n_false / days if days else None,
        "alerts_per_day": len(alerts) / days if days else None,
        "legit_accounts_touched_per_day": (sum(len(v) for v in legit_touched_by_day.values()) / days)
        if days
        else None,
    }


def archetype_fpr(
    stream: dict, y: np.ndarray, tier: np.ndarray, archetype_of: dict, mule: set, days: float
) -> dict:
    """Account-level false-alert rate by archetype among legitimate accounts (both sides of legit txns)."""
    flagged = (np.asarray(tier) >= 1) & (np.asarray(y) == 0)
    active: dict[str, set] = defaultdict(set)
    hit: dict[str, set] = defaultdict(set)
    hit_days: dict[str, set] = defaultdict(set)
    src, dst, ts = stream["src"], stream["dst"], stream["ts"]
    legit_rows = np.flatnonzero(np.asarray(y) == 0)
    for i in legit_rows.tolist():
        for a in (src[i], dst[i]):
            if a in mule:
                continue
            active[archetype_of.get(a, "unknown")].add(a)
    for i in np.flatnonzero(flagged).tolist():
        d = int(ts[i] // 86400)
        for a in (src[i], dst[i]):
            if a in mule:
                continue
            arch = archetype_of.get(a, "unknown")
            hit[arch].add(a)
            hit_days[arch].add((a, d))
    out = {}
    for arch in sorted(active):
        n_act = len(active[arch])
        out[arch] = {
            "accounts_active": n_act,
            "accounts_alerted": len(hit[arch]),
            "share": len(hit[arch]) / n_act if n_act else None,
            "per_account_day": len(hit_days[arch]) / (n_act * days) if n_act and days else None,
        }
    return out


def block_counts(
    ts: np.ndarray, y: np.ndarray, tier: np.ndarray, start: float, block_s: float = 21600.0
) -> dict:
    """Per 6-hour block: positives, positives flagged, negatives, negatives flagged (for block bootstrap)."""
    b = ((np.asarray(ts) - start) // block_s).astype(np.int64)
    nb = int(b.max()) + 1 if b.size else 0
    y = np.asarray(y).astype(np.int64)
    f = (np.asarray(tier) >= 1).astype(np.int64)
    return {
        "pos": np.bincount(b, weights=y, minlength=nb).astype(int).tolist(),
        "pos_hit": np.bincount(b, weights=y * f, minlength=nb).astype(int).tolist(),
        "neg": np.bincount(b, weights=1 - y, minlength=nb).astype(int).tolist(),
        "neg_hit": np.bincount(b, weights=(1 - y) * f, minlength=nb).astype(int).tolist(),
    }


def curve_points(y: np.ndarray, s: np.ndarray, n_points: int = 300) -> dict:
    """Downsampled precision-recall and ROC points (for figures), at distinct score thresholds."""
    y = np.asarray(y).astype(np.int64)
    s = np.asarray(s, dtype=np.float64)
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    if n_pos == 0 or n_neg == 0:
        return {"recall": [], "precision": [], "fpr": []}
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    distinct = np.r_[np.flatnonzero(np.diff(s_sorted)), y.size - 1]
    tps = np.cumsum(y_sorted)[distinct]
    fps = (distinct + 1) - tps
    # log-spaced sampling in FPR keeps the low-FPR region visible
    fpr = fps / n_neg
    targets = np.unique(np.r_[np.geomspace(1e-5, 1.0, n_points), [1.0]])
    idx = np.unique(np.clip(np.searchsorted(fpr, targets, side="right") - 1, 0, fpr.size - 1))
    rec = tps[idx] / n_pos
    prec = tps[idx] / np.maximum(1, tps[idx] + fps[idx])
    return {
        "recall": rec.round(6).tolist(),
        "precision": prec.round(6).tolist(),
        "fpr": fpr[idx].round(8).tolist(),
    }


def ring_peaks(log_: dict, label_of: dict, start: float, end: float) -> list[list[float]]:
    """[peak score, purity] for every tracked ring whose first edge lies in [start, end)."""
    out = []
    for lg in log_.values():
        if not (start <= lg["first_ts"] < end) or lg["peak"] <= 0:
            continue
        ids = lg["event_ids"]
        pur = sum(label_of.get(e, 0) for e in ids) / len(ids) if ids else 0.0
        out.append([round(float(lg["peak"]), 6), round(pur, 4)])
    return out


# ---------------------------------------------------------------------------------------------- bootstrap
def bootstrap_ci(values: np.ndarray, stat, n_boot: int, seed: int, alpha: float = 0.05) -> list[float | None]:
    values = np.asarray(values)
    if values.size == 0:
        return [None, None]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.shape[0], size=(n_boot, values.shape[0]))
    stats = np.array([stat(values[i]) for i in idx])
    stats = stats[np.isfinite(stats)]
    if stats.size == 0:
        return [None, None]
    return [float(np.quantile(stats, alpha / 2)), float(np.quantile(stats, 1 - alpha / 2))]
