"""Replay a stream through the FeatureEngine once and write feature matrices (spec 9.9). Train env (numpy)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from chainwatch.config import Config, sha256_file
from chainwatch.data.io import iter_events
from chainwatch.features.engine import FeatureEngine
from chainwatch.features.registry import names_for_cfg, registry_hash, semantic_features_config
from chainwatch.schema import TYPE_CODE

log = logging.getLogger(__name__)

SPLITS = ("train", "val", "test")
ROLE_CODE = {"legit": 0, "victim": 1, "mule": 2, "collector": 3, "sink": 4}
BLOCK = 100_000


def split_bounds(cfg: Config, world_dir: Path) -> dict[str, float]:
    """Time bounds in seconds. A world's ``meta.json`` (PaySim) may override the config split."""
    meta_file = world_dir / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if "split_ts" in meta:
            return {k: float(v) for k, v in meta["split_ts"].items()}
    d = 86400.0
    s = cfg.eval.split_days
    return {
        "burn_end": float(cfg.eval.burn_in_days) * d,
        "train_end": s[0] * d,
        "val_end": s[1] * d,
        "test_end": s[2] * d,
    }


def world_tag_of(world_dir: Path) -> str:
    return world_dir.name


class _SplitBuffer:
    def __init__(self, n_feat: int):
        self.n_feat = n_feat
        self.rows: list[list[float]] = []
        self.blocks: list[np.ndarray] = []
        self.cols: dict[str, list] = {
            k: []
            for k in (
                "y",
                "ts",
                "event_id",
                "ring_id",
                "hop",
                "src",
                "dst",
                "amount",
                "type",
                "src_role",
                "dst_role",
            )
        }

    def add(self, row: list[float], le) -> None:
        ev = le.event
        self.rows.append(row)
        c = self.cols
        c["y"].append(le.is_fraud)
        c["ts"].append(ev.ts)
        c["event_id"].append(ev.event_id)
        rid = le.ring_id
        c["ring_id"].append(int(rid) if rid.lstrip("-").isdigit() else -2)
        c["hop"].append(le.hop)
        c["src"].append(ev.src)
        c["dst"].append(ev.dst)
        c["amount"].append(ev.amount)
        c["type"].append(TYPE_CODE[ev.type])
        c["src_role"].append(ROLE_CODE.get(le.src_role, 0))
        c["dst_role"].append(ROLE_CODE.get(le.dst_role, 0))
        if len(self.rows) >= BLOCK:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            self.blocks.append(np.asarray(self.rows, dtype=np.float64))
            self.rows = []

    def save(self, out: Path, split: str) -> int:
        self.flush()
        X = np.concatenate(self.blocks) if self.blocks else np.zeros((0, self.n_feat), dtype=np.float64)
        np.save(out / f"{split}_X.npy", X)
        c = self.cols
        np.save(out / f"{split}_y.npy", np.asarray(c["y"], dtype=np.int8))
        np.save(out / f"{split}_ts.npy", np.asarray(c["ts"], dtype=np.float64))
        np.save(out / f"{split}_event_id.npy", np.asarray(c["event_id"], dtype=np.int64))
        np.save(out / f"{split}_ring_id.npy", np.asarray(c["ring_id"], dtype=np.int32))
        np.save(out / f"{split}_hop.npy", np.asarray(c["hop"], dtype=np.int16))
        np.save(out / f"{split}_src.npy", np.asarray(c["src"], dtype="<U16"))
        np.save(out / f"{split}_dst.npy", np.asarray(c["dst"], dtype="<U16"))
        np.save(out / f"{split}_amount.npy", np.asarray(c["amount"], dtype=np.float64))
        np.save(out / f"{split}_type.npy", np.asarray(c["type"], dtype=np.int8))
        np.save(out / f"{split}_src_role.npy", np.asarray(c["src_role"], dtype=np.int8))
        np.save(out / f"{split}_dst_role.npy", np.asarray(c["dst_role"], dtype=np.int8))
        return int(X.shape[0])


def build_matrices(
    cfg: Config, world_dir: str | Path, out_dir: str | Path | None = None, limit: int | None = None
) -> dict:
    """Replay ``world_dir/events.csv.gz`` (including burn-in) and write ``features_<world_tag>/``."""
    world_dir = Path(world_dir)
    tag = world_tag_of(world_dir)
    out = Path(out_dir) if out_dir else world_dir.parent / f"features_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    events_file = world_dir / "events.csv.gz"
    bounds = split_bounds(cfg, world_dir)
    names = names_for_cfg(cfg, "G2")
    eng = FeatureEngine(cfg, ("G2",))
    total = None
    man = world_dir / "manifest.json"
    if man.exists():
        total = json.loads(man.read_text(encoding="utf-8")).get("n_events")
    if limit:
        total = min(total or limit, limit)
    bufs = {s: _SplitBuffer(len(names)) for s in SPLITS}
    burn_end, train_end, val_end, test_end = (
        bounds[k] for k in ("burn_end", "train_end", "val_end", "test_end")
    )
    t0 = time.time()
    n = 0
    n_burn = 0
    for le in iter_events(events_file):
        if limit and n >= limit:
            break
        f = eng.step(le.event)
        ts = le.event.ts
        n += 1
        if n % 100_000 == 0:
            rate = n / max(1e-9, time.time() - t0)
            eta = (total - n) / rate if total else float("nan")
            log.info("  %s: %d events, %.0f ev/s, ETA %.0fs", tag, n, rate, eta)
        if ts < burn_end:
            n_burn += 1
            continue
        split = (
            "train" if ts < train_end else ("val" if ts < val_end else ("test" if ts < test_end else None))
        )
        if split is None:
            continue
        bufs[split].add([f[k] for k in names], le)
    seconds = time.time() - t0
    counts = {s: bufs[s].save(out, s) for s in SPLITS}
    pos = {s: int(np.load(out / f"{s}_y.npy").sum()) for s in SPLITS}
    with open(out / "names.json", "w", encoding="utf-8") as fh:
        json.dump(names, fh, indent=1)
    world_manifest = json.loads(man.read_text(encoding="utf-8")) if man.exists() else {}
    meta = {
        "world": tag,
        "world_dir": str(world_dir.as_posix()),
        "provenance": world_manifest.get("provenance", "ChainWorld"),
        "config_hash": cfg.config_hash,
        "registry_hash": registry_hash(names, semantic_features_config(cfg)),
        "priority": cfg.features.priority,
        "world_events_sha256": sha256_file(events_file),
        "split_ts": bounds,
        "rows": counts,
        "positives": pos,
        "burn_in_rows": n_burn,
        "events_replayed": n,
        "replay_seconds": round(seconds, 1),
        "us_per_event": round(seconds / max(1, n) * 1e6, 1),
        "engine_stats": eng.stats(),
        "limit": limit,
    }
    with open(out / "meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    log.info("features %s: %s rows, positives %s, %.0fs", tag, counts, pos, seconds)
    return meta


def load_split(feat_dir: str | Path, split: str, names: list[str] | None = None, mmap: bool = True) -> dict:
    """Load one split. ``names`` selects columns (B0/B1 are column subsets of the G2 matrix)."""
    feat_dir = Path(feat_dir)
    all_names = json.loads((feat_dir / "names.json").read_text(encoding="utf-8"))
    X = np.load(feat_dir / f"{split}_X.npy", mmap_mode="r" if mmap else None)
    if names is not None:
        idx = [all_names.index(n) for n in names]
        X = np.asarray(X[:, idx], dtype=np.float64)
    out = {"X": X, "names": names or all_names}
    for k in (
        "y",
        "ts",
        "event_id",
        "ring_id",
        "hop",
        "src",
        "dst",
        "amount",
        "type",
        "src_role",
        "dst_role",
    ):
        p = feat_dir / f"{split}_{k}.npy"
        if p.exists():
            out[k] = np.load(p)
    return out
