"""PaySim adapter (spec 8.3): Kaggle ``paysim1`` CSV to the canonical stream. Train env (pandas).

Mapping: ``ts = (step - 1) * 3600 + u`` with ``u ~ U(0, 3600)`` seeded by ``project.seed`` (PaySim has hour
resolution); ``event_id`` = row index; balances from the four balance columns, receiver balances null when
``nameDest`` starts with ``M``; ``is_fraud = isFraud``; no ring ground truth. ``isFlaggedFraud`` is never a
feature; it is kept in a side file as the simulator's own rule baseline. Split by step: train 1 to 400,
validation 401 to 500, test 501 to 744, burn-in the first 72 steps.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from chainwatch.config import Config, resolve_path, sha256_file
from chainwatch.data.canonical import COLUMNS, normalize_type
from chainwatch.data.io import _fmt, _GzWriter

log = logging.getLogger(__name__)
PAYSIM_COLUMNS = (
    "step",
    "type",
    "amount",
    "nameOrig",
    "oldbalanceOrg",
    "newbalanceOrig",
    "nameDest",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
    "isFlaggedFraud",
)
SPLIT_STEPS = {"burn_end": 72, "train_end": 400, "val_end": 500, "test_end": 744}


def find_csv(src: str | Path) -> Path:
    src = resolve_path(src)
    if src.is_file():
        return src
    files = sorted(src.glob("*.csv"))
    if not files:
        raise FileNotFoundError(
            f"no PaySim CSV in {src}. Download 'paysim1' from Kaggle (free account) and place the CSV there; "
            f"it is about 470 MB and must never be committed."
        )
    return files[0]


def read_paysim(path: Path, chunksize: int = 500_000):
    import pandas as pd

    parts = []
    for chunk in pd.read_csv(path, chunksize=chunksize):
        missing = [c for c in PAYSIM_COLUMNS if c not in chunk.columns]
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")
        parts.append(chunk[list(PAYSIM_COLUMNS)])
    return pd.concat(parts, ignore_index=True)


def to_canonical(df, seed: int):
    """Return a canonical DataFrame sorted by (ts, event_id) plus the isFlaggedFraud side column."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    out["event_id"] = np.arange(len(out), dtype=np.int64)
    out["ts"] = np.round((out["step"].astype(np.int64) - 1) * 3600.0 + rng.uniform(0.0, 3600.0, len(out)), 3)
    out["type"] = [normalize_type(t) for t in out["type"].astype(str)]
    out["src"] = out["nameOrig"].astype(str)
    out["dst"] = out["nameDest"].astype(str)
    merchant = out["dst"].str.startswith("M")
    out["src_bal_before"] = out["oldbalanceOrg"].astype(float)
    out["src_bal_after"] = out["newbalanceOrig"].astype(float)
    out["dst_bal_before"] = out["oldbalanceDest"].astype(float).where(~merchant)
    out["dst_bal_after"] = out["newbalanceDest"].astype(float).where(~merchant)
    out["is_fraud"] = out["isFraud"].astype(int)
    out["ring_id"] = "-1"
    out["hop"] = -1
    out["src_role"] = "legit"
    out["dst_role"] = "legit"
    out = out[out["amount"] > 0]
    out = out.sort_values(["ts", "event_id"], kind="mergesort").reset_index(drop=True)
    return out


def convert(
    cfg: Config, src: str | Path = "data/raw/paysim", out: str | Path = "data/processed/paysim"
) -> dict:
    path = find_csv(src)
    out = resolve_path(out)
    out.mkdir(parents=True, exist_ok=True)
    log.info("reading %s", path)
    df = to_canonical(read_paysim(path), int(cfg.project.seed))
    n = len(df)
    cols = list(COLUMNS)

    def rows():
        vals = df[cols].itertuples(index=False, name=None)
        for r in vals:
            yield [None if (isinstance(x, float) and x != x) else x for x in r]

    with _GzWriter(out / "events.csv.gz") as w:
        w.writerow(cols)
        for r in rows():
            w.writerow([_fmt(x) for x in r])
    with _GzWriter(out / "flagged.csv.gz") as w:
        w.writerow(["event_id", "isFlaggedFraud"])
        for eid, f in zip(df["event_id"].tolist(), df["isFlaggedFraud"].astype(int).tolist(), strict=False):
            w.writerow([eid, f])
    accounts = sorted(set(df["src"]) | set(df["dst"]))
    with _GzWriter(out / "accounts.csv.gz") as w:
        w.writerow(["account_id", "archetype", "is_mule", "is_unwitting", "first_ring_id"])
        for a in accounts:
            w.writerow([a, "merchant" if a.startswith("M") else "customer", 0, 0, "-1"])
    with open(out / "rings.json", "w", encoding="utf-8") as fh:
        json.dump({"rings": [], "note": "PaySim has no ring ground truth"}, fh)
    split_ts = {k: float(v) * 3600.0 for k, v in SPLIT_STEPS.items()}
    meta = {
        "world": "paysim",
        "provenance": "PaySim",
        "source_file": path.name,
        "n_events": n,
        "fraud_events": int(df["is_fraud"].sum()),
        "split_steps": SPLIT_STEPS,
        "split_ts": split_ts,
        "ts_mapping": "(step - 1) * 3600 + U(0, 3600) seeded by project.seed",
        "step_semantics": "treated as UTC hours (UNVERIFIED)",
    }
    with open(out / "meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    manifest = {
        "world": "paysim",
        "provenance": "PaySim",
        "n_events": n,
        "config_hash": cfg.config_hash,
        "files": {f: sha256_file(out / f) for f in ("events.csv.gz", "accounts.csv.gz")},
    }
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    return meta
