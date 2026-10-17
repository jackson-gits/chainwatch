"""ChainWorld orchestration: population, campaigns, background, balance pass, files and world report."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

from chainwatch.config import Config, resolve_path, sha256_file
from chainwatch.data.canonical import ACCOUNT_COLUMNS, COLUMNS
from chainwatch.data.io import _fmt, _GzWriter
from chainwatch.data.world.background import generate_background
from chainwatch.data.world.balances import TYPE_NAMES, balance_pass
from chainwatch.data.world.population import DAY_S, build_population
from chainwatch.data.world.typologies import TYPOLOGIES, plan_campaigns

log = logging.getLogger(__name__)


def world_tag(preset: str, seed: int) -> str:
    return f"world_{preset}_seed{seed}"


def _quantiles(values: list[float], qs=(0.1, 0.25, 0.5, 0.75, 0.9)) -> dict:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=float)
    out = {"n": int(arr.size)}
    for q in qs:
        out[f"q{int(q * 100):02d}"] = float(np.quantile(arr, q))
    return out


def _fifo_dwell(seq: list[tuple[float, float]], lookback: float = DAY_S) -> tuple[list[float], float, float]:
    """FIFO dwell (amount-weighted, per debit) and total in/out for one account's (ts, signed amount) list."""
    credits: deque = deque()
    dwells, tot_in, tot_out = [], 0.0, 0.0
    for ts, amt in seq:
        if amt > 0:
            credits.append([ts, amt])
            tot_in += amt
            continue
        need = -amt
        tot_out += need
        matched, wsum = 0.0, 0.0
        while credits and need > 1e-9:
            c = credits[0]
            if ts - c[0] > lookback:
                credits.popleft()
                continue
            take = min(c[1], need)
            matched += take
            wsum += take * (ts - c[0])
            need -= take
            c[1] -= take
            if c[1] <= 0.01:
                credits.popleft()
        if matched > 0:
            dwells.append(wsum / matched)
    return dwells, tot_in, tot_out


def generate_world(
    cfg: Config, seed: int, out_dir: str | Path, progress: bool = True, name: str | None = None
) -> dict:
    """Generate one world into ``out_dir`` and return its report."""
    t_start = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wname = name or out_dir.name
    ss = np.random.SeedSequence([int(seed), 20261017])
    s_pop, s_camp, s_bg, s_bal = ss.spawn(4)
    pop = build_population(cfg, np.random.default_rng(s_pop))
    camp_intents, plans, extra_bg = plan_campaigns(cfg, pop, np.random.default_rng(s_camp))
    plain, specials = generate_background(cfg, pop, np.random.default_rng(s_bg), extra_bg)
    specials.extend(camp_intents)
    end_ts = float(cfg.world.days) * DAY_S
    bal_seed = int(s_bal.generate_state(1)[0])
    log.info(
        "planned %d plain and %d special intents (%.1fs)",
        plain.arrays()[0].size,
        len(specials),
        time.time() - t_start,
    )

    ids, arch = pop.ids, pop.archetype
    ring_ids = [str(p.ring_id) for p in plans]
    by_type: Counter = Counter()
    by_src_arch: Counter = Counter()
    by_dst_arch: Counter = Counter()
    n_fraud = 0
    ring_events: dict[int, list[int]] = defaultdict(list)
    ring_info: dict[int, dict] = {}
    watch = set(range(*pop.ranges["trader"])) | set(range(*pop.ranges["agent"]))
    flows: dict[int, list[tuple[float, float]]] = defaultdict(list)
    mule_flow: dict[tuple[int, int], dict] = {}
    seen = np.zeros(pop.n, dtype=bool)

    events_path = out_dir / "events.csv.gz"
    eid = 0
    with _GzWriter(events_path) as w:
        w.writerow(list(COLUMNS))
        for row in balance_pass(
            pop,
            plain,
            specials,
            end_ts,
            float(cfg.world.upi_txn_cap_inr),
            float(cfg.world.structuring_cap_inr),
            bal_seed,
        ):
            ts, src, dst, typ, amount, sbb, sba, dbb, dba, camp, hop, sr, dr = row
            tname = TYPE_NAMES[typ]
            fraud = 1 if camp >= 0 else 0
            w.writerow(
                [
                    eid,
                    _fmt(ts),
                    ids[src],
                    ids[dst],
                    _fmt(amount),
                    tname,
                    _fmt(sbb),
                    _fmt(sba),
                    _fmt(dbb),
                    _fmt(dba),
                    fraud,
                    ring_ids[camp] if fraud else "-1",
                    hop,
                    sr,
                    dr,
                ]
            )
            seen[src] = seen[dst] = True
            by_type[tname] += 1
            by_src_arch[arch[src]] += 1
            by_dst_arch[arch[dst]] += 1
            if src in watch:
                flows[src].append((ts, -amount))
            if dst in watch:
                flows[dst].append((ts, amount))
            if fraud:
                n_fraud += 1
                ring_events[camp].append(eid)
                info = ring_info.setdefault(
                    camp,
                    {
                        "first_ts": ts,
                        "last_ts": ts,
                        "max_hop": 0,
                        "inflow": 0.0,
                        "outflow": 0.0,
                        "exit_ts": None,
                        "exit_hop": None,
                        "accounts": set(),
                    },
                )
                info["last_ts"] = ts
                info["max_hop"] = max(info["max_hop"], hop)
                info["accounts"].update((src, dst))
                if hop == 0:
                    info["inflow"] += amount
                if dr == "sink":
                    info["outflow"] += amount
                    if info["exit_ts"] is None:
                        info["exit_ts"], info["exit_hop"] = ts, hop
                if sr in ("mule", "collector"):
                    mf = mule_flow.setdefault(
                        (camp, src), {"in": 0.0, "out": 0.0, "first_in": None, "first_out": None}
                    )
                    mf["out"] += amount
                    if mf["first_out"] is None:
                        mf["first_out"] = ts
                if dr in ("mule", "collector"):
                    mf = mule_flow.setdefault(
                        (camp, dst), {"in": 0.0, "out": 0.0, "first_in": None, "first_out": None}
                    )
                    mf["in"] += amount
                    if mf["first_in"] is None:
                        mf["first_in"] = ts
            eid += 1
            if progress and eid % 500_000 == 0:
                log.info("  wrote %d events (%.0fs)", eid, time.time() - t_start)
    n_events = eid

    # rings.json
    rings = []
    invalid = []
    for p in plans:
        info = ring_info.get(p.ring_id)
        if info is None:
            invalid.append(p.ring_id)
            continue
        roles = {
            "victims": sorted({ids[v] for v in p.victims if v in info["accounts"]}),
            "mules": sorted({ids[m] for m in p.mules if m in info["accounts"]}),
            "collectors": sorted({ids[m] for m in p.collectors if m in info["accounts"]}),
            "sinks": [ids[p.sink]] if p.sink in info["accounts"] else [],
        }
        n_acc = sum(len(v) for v in roles.values())
        n_hops = info["max_hop"] + 1
        if n_acc < 3 or n_hops < 2:
            invalid.append(p.ring_id)
        rings.append(
            {
                "ring_id": str(p.ring_id),
                "typology": p.typology,
                "accounts": roles,
                "n_accounts": n_acc,
                "event_ids": ring_events[p.ring_id],
                "first_ts": info["first_ts"],
                "last_ts": info["last_ts"],
                "exit_ts": info["exit_ts"],
                "exit_hop": info["exit_hop"],
                "n_hops": n_hops,
                "inflow": round(info["inflow"], 2),
                "outflow": round(info["outflow"], 2),
                "sink_type": p.sink_type,
                "reused_mules": sorted(ids[m] for m in p.reused),
            }
        )
    with open(out_dir / "rings.json", "w", encoding="utf-8") as fh:
        json.dump({"rings": rings}, fh, indent=1)

    # accounts.csv.gz (accounts that exist: all non-pool accounts plus pool accounts used by a campaign)
    with _GzWriter(out_dir / "accounts.csv.gz") as w:
        w.writerow(list(ACCOUNT_COLUMNS))
        for i in range(pop.n):
            if not pop.used[i]:
                continue
            fr = int(pop.first_ring[i])
            w.writerow(
                [ids[i], arch[i], int(pop.is_mule[i]), int(pop.is_unwitting[i]), str(fr) if fr >= 0 else "-1"]
            )

    # report
    dwell_mule = [
        mf["first_out"] - mf["first_in"]
        for mf in mule_flow.values()
        if mf["first_in"] is not None and mf["first_out"] is not None and mf["first_out"] >= mf["first_in"]
    ]
    pass_mule = [mf["out"] / mf["in"] for mf in mule_flow.values() if mf["in"] > 0]
    legit_stats = {}
    for name in ("trader", "agent"):
        a, b = pop.ranges[name]
        dw, pt = [], []
        for acct in range(a, b):
            seq = flows.get(acct)
            if not seq:
                continue
            d, ti, to = _fifo_dwell(seq)
            dw.extend(d)
            if ti > 0:
                pt.append(to / ti)
        legit_stats[name] = {"dwell_s": _quantiles(dw), "pass_through": _quantiles(pt)}
    share = n_fraud / max(1, n_events)
    band = list(cfg.world.fraud_target_share)
    archetype_counts = Counter(arch[i] for i in range(pop.n) if pop.used[i])
    report = {
        "world": wname,
        "provenance": "ChainWorld",
        "seed": int(seed),
        "preset": cfg.world.preset,
        "days": int(cfg.world.days),
        "config_hash": cfg.config_hash,
        "n_events": n_events,
        "n_accounts": int(pop.used.sum()),
        "n_accounts_seen": int(seen.sum()),
        "archetype_counts": dict(sorted(archetype_counts.items())),
        "counts_by_type": dict(sorted(by_type.items())),
        "counts_by_src_archetype": dict(sorted(by_src_arch.items())),
        "counts_by_dst_archetype": dict(sorted(by_dst_arch.items())),
        "fraud_events": n_fraud,
        "fraud_share": share,
        "fraud_target_share": band,
        "fraud_share_in_band": bool(band[0] <= share <= band[1]),
        "campaigns_planned": len(plans),
        "rings_written": len(rings),
        "rings_invalid": invalid,
        "rings_by_typology": dict(Counter(r["typology"] for r in rings)),
        "ring_accounts": _quantiles([r["n_accounts"] for r in rings]),
        "ring_hops": _quantiles([r["n_hops"] for r in rings]),
        "mule_accounts": int(pop.is_mule.sum()),
        "mule_kinds": dict(Counter(pop.mule_kind.values())),
        "dwell_s": {
            "ring_mules": _quantiles(dwell_mule),
            **{k: v["dwell_s"] for k, v in legit_stats.items()},
        },
        "pass_through": {
            "ring_mules": _quantiles(pass_mule),
            **{k: v["pass_through"] for k, v in legit_stats.items()},
        },
        "typologies": list(TYPOLOGIES),
        "generation_seconds": round(time.time() - t_start, 1),
    }
    with open(out_dir / "world_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    manifest = {
        "world": wname,
        "provenance": "ChainWorld",
        "seed": int(seed),
        "config_hash": cfg.config_hash,
        "config": cfg.to_dict(),
        "n_events": n_events,
        "files": {
            name: sha256_file(out_dir / name)
            for name in ("events.csv.gz", "accounts.csv.gz", "rings.json", "world_report.json")
        },
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    log.info(
        "world %s: %d events, %d fraud (share %.5f), %d rings, %.1fs",
        wname,
        n_events,
        n_fraud,
        share,
        len(rings),
        time.time() - t_start,
    )
    return report


DEMO_OVERRIDES = {
    "world.days": 6,
    "world.n_campaigns": 14,
    "world.force_typologies": list(TYPOLOGIES),
    "world.n_charity": 1,
    "world.campaign_start_min_day": 3.05,
    "world.campaign_end_margin_days": 0.6,
}
DEMO_SEED = 2026


def generate_demo(cfg: Config, out_dir: str | Path = "data/replay") -> dict:
    """Demo replay world: preset small, seed 2026, 6 days, 14 campaigns covering every typology
    (spec 8.2.6)."""
    import shutil
    import tempfile

    demo_cfg = cfg.with_overrides(DEMO_OVERRIDES)
    out_dir = resolve_path(out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        report = generate_world(demo_cfg, DEMO_SEED, tmp, name="demo_small_seed2026")
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(tmp) / "events.csv.gz", out_dir / "demo.csv.gz")
        shutil.copyfile(Path(tmp) / "accounts.csv.gz", out_dir / "demo_accounts.csv.gz")
        shutil.copyfile(Path(tmp) / "rings.json", out_dir / "demo_rings.json")
        shutil.copyfile(Path(tmp) / "world_report.json", out_dir / "demo_world_report.json")
    return report
