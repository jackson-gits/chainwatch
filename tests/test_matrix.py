"""Feature matrix builder (spec 9.9): time splits, burn-in, alignment, dtypes, determinism, names.

The fast tests drive ``build_matrices`` over a hand-written synthetic world where every event's
split is known by construction; the ``@slow`` test checks the structural invariants of the real
tiny-world matrices produced by the session ``tiny_features`` fixture.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from chainwatch.data.io import iter_events, write_events
from chainwatch.features.matrix import build_matrices, load_split, split_bounds
from chainwatch.features.registry import feature_names, names_for_cfg, registry_hash_for_cfg
from chainwatch.schema import TYPE_CODE, Event, LabeledEvent

DAY = 86400.0
# tiny cfg: burn_in_days=1, split_days=[4, 6, 8]
BURN_END, TRAIN_END, VAL_END, TEST_END = DAY, 4 * DAY, 6 * DAY, 8 * DAY

#: dtypes written by ``_SplitBuffer.save`` (spec 9.9)
EXPECTED_DTYPES = {
    "y": np.dtype(np.int8),
    "ts": np.dtype(np.float64),
    "event_id": np.dtype(np.int64),
    "ring_id": np.dtype(np.int32),
    "hop": np.dtype(np.int16),
    "src": np.dtype("<U16"),
    "dst": np.dtype("<U16"),
    "amount": np.dtype(np.float64),
    "type": np.dtype(np.int8),
    "src_role": np.dtype(np.int8),
    "dst_role": np.dtype(np.int8),
}
ROLE_CODE = {"legit": 0, "victim": 1, "mule": 2, "collector": 3, "sink": 4}


def _labeled(
    eid,
    ts,
    src,
    dst,
    amount,
    typ="TRANSFER",
    is_fraud=0,
    ring="-1",
    hop=-1,
    src_role="legit",
    dst_role="legit",
) -> LabeledEvent:
    """Synthetic labeled event; all account ids are opaque test ids (no real data)."""
    ev = Event(eid, float(ts), src, dst, float(amount), typ, None, None, None, None)
    return LabeledEvent(ev, is_fraud, ring, hop, src_role, dst_role)


def _world_events() -> list[LabeledEvent]:
    """9 events: 2 burn-in, 2 train, 2 val, 2 test and 1 at ``test_end`` that is dropped."""
    return [
        _labeled(1, 1000.0, "burn_p1", "B01", 100.0),  # burn-in credit funds the first train event
        _labeled(2, 86399.0, "burn_p2", "B01", 50.0, "PAYMENT"),  # last burn-in second
        _labeled(3, BURN_END, "B01", "C01", 100.0, "TRANSFER", 1, "3", 0, "mule", "collector"),
        _labeled(4, 200000.0, "C01", "D01", 40.0, "PAYMENT"),
        _labeled(5, TRAIN_END, "D01", "E01", 10.0, "CASH_OUT"),
        _labeled(6, 400000.0, "E01", "F01", 20.0, "CASH_IN", 1, "7", 2, "mule", "sink"),
        _labeled(7, VAL_END, "F01", "G01", 30.0, "DEBIT"),
        _labeled(8, 600000.0, "G01", "H01", 40.0),
        _labeled(9, TEST_END, "H01", "I01", 50.0),  # ts >= test_end -> never written
    ]


def _build(cfg, tmp_workdir, name, events=None, limit=None):
    root = tmp_workdir / name
    world = root / "world_synth"
    write_events(world / "events.csv.gz", events if events is not None else _world_events())
    out = root / "features"
    meta = build_matrices(cfg, world, out, limit=limit)
    return world, out, meta


def _names(out) -> list[str]:
    return json.loads((out / "names.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ split bounds
def test_split_bounds_follow_config(tiny_cfg_session, tmp_workdir):
    world = tmp_workdir / "bounds_world"
    world.mkdir(parents=True, exist_ok=True)
    assert split_bounds(tiny_cfg_session, world) == {
        "burn_end": BURN_END,
        "train_end": TRAIN_END,
        "val_end": VAL_END,
        "test_end": TEST_END,
    }


def test_split_bounds_honour_world_meta_override(tiny_cfg_session, tmp_workdir):
    world = tmp_workdir / "bounds_meta"
    world.mkdir(parents=True, exist_ok=True)
    (world / "meta.json").write_text(
        json.dumps({"split_ts": {"burn_end": 1.0, "train_end": 2.0, "val_end": 3.0, "test_end": 4.0}}),
        encoding="utf-8",
    )
    assert split_bounds(tiny_cfg_session, world) == {
        "burn_end": 1.0,
        "train_end": 2.0,
        "val_end": 3.0,
        "test_end": 4.0,
    }


# ------------------------------------------------------------------ time splits / burn-in
def test_splits_by_time_and_excludes_burn_in(tiny_cfg_session, tmp_workdir):
    _, out, meta = _build(tiny_cfg_session, tmp_workdir, "split")
    bounds = {"train": (BURN_END, TRAIN_END), "val": (TRAIN_END, VAL_END), "test": (VAL_END, TEST_END)}
    expected_ids = {"train": [3, 4], "val": [5, 6], "test": [7, 8]}
    for split, (lo, hi) in bounds.items():
        ts = np.load(out / f"{split}_ts.npy")
        ids = np.load(out / f"{split}_event_id.npy")
        assert list(ids) == expected_ids[split]
        assert np.all(ts >= lo) and np.all(ts < hi)
        assert np.all(np.diff(ts) >= 0.0)  # stream order preserved
    all_ids = np.concatenate([np.load(out / f"{s}_event_id.npy") for s in ("train", "val", "test")])
    assert 9 not in all_ids  # ts == test_end is dropped
    assert meta["burn_in_rows"] == 2
    assert meta["events_replayed"] == 9
    assert meta["rows"] == {"train": 2, "val": 2, "test": 2}
    assert sum(meta["rows"].values()) + meta["burn_in_rows"] + 1 == meta["events_replayed"]


def test_burn_in_events_still_build_engine_state(tiny_cfg_session, tmp_workdir):
    _, out, _ = _build(tiny_cfg_session, tmp_workdir, "burnstate")
    names = _names(out)
    X = np.load(out / "train_X.npy")
    row = X[0]  # event 3, funded by the burn-in credit of event 1
    assert row[names.index("s_matched_share")] == 1.0
    assert row[names.index("s_new_payee")] == 1.0
    assert row[names.index("t_amount")] == 100.0


def test_limit_caps_the_replay(tiny_cfg_session, tmp_workdir):
    _, out, meta = _build(tiny_cfg_session, tmp_workdir, "limit", limit=3)
    assert meta["events_replayed"] == 3
    assert meta["burn_in_rows"] == 2  # events 1-2 are burn-in; event 3 (ts == burn_end) is train
    assert meta["rows"] == {"train": 1, "val": 0, "test": 0}
    assert np.load(out / "train_X.npy").shape == (1, len(_names(out)))


# ------------------------------------------------------------------ array alignment / dtypes
def test_arrays_are_aligned_and_typed(tiny_cfg_session, tmp_workdir):
    _, out, meta = _build(tiny_cfg_session, tmp_workdir, "aligned")
    names = _names(out)
    by_id = {le.event.event_id: le for le in _world_events()}
    for split in ("train", "val", "test"):
        X = np.load(out / f"{split}_X.npy")
        cols = {k: np.load(out / f"{split}_{k}.npy") for k in EXPECTED_DTYPES}
        n_rows = X.shape[0]
        assert n_rows == meta["rows"][split]
        assert X.shape[1] == len(names) == 59
        assert X.dtype == np.dtype(np.float64)
        for k, arr in cols.items():
            assert arr.shape == (n_rows,) and arr.dtype == EXPECTED_DTYPES[k], k
        assert np.isfinite(X).all()  # spec 9.4: features never contain NaN
        for i, eid in enumerate(cols["event_id"]):
            le = by_id[int(eid)]
            assert cols["ts"][i] == le.event.ts
            assert cols["amount"][i] == le.event.amount
            assert cols["type"][i] == TYPE_CODE[le.event.type]
            assert cols["y"][i] == le.is_fraud
            assert cols["hop"][i] == le.hop
            assert cols["src_role"][i] == ROLE_CODE[le.src_role]
            assert cols["dst_role"][i] == ROLE_CODE[le.dst_role]
            assert str(cols["src"][i]) == le.event.src
            assert str(cols["dst"][i]) == le.event.dst
            ring = le.ring_id
            assert cols["ring_id"][i] == (int(ring) if ring.lstrip("-").isdigit() else -2)
        assert meta["positives"][split] == int(cols["y"].sum())


def test_load_split_supports_detector_column_subsets(tiny_cfg_session, tmp_workdir):
    _, out, _ = _build(tiny_cfg_session, tmp_workdir, "subset")
    full = load_split(out, "train")
    b0_names = feature_names("B0")
    b0 = load_split(out, "train", names=b0_names)
    assert b0["names"] == b0_names
    assert b0["X"].shape == (full["X"].shape[0], 13)
    idx = [full["names"].index(n) for n in b0_names]
    assert np.array_equal(b0["X"], np.asarray(full["X"])[:, idx])


# ------------------------------------------------------------------ determinism / registry
def test_build_is_deterministic(tiny_cfg_session, tmp_workdir):
    _, out_a, _ = _build(tiny_cfg_session, tmp_workdir, "det_a")
    _, out_b, _ = _build(tiny_cfg_session, tmp_workdir, "det_b")
    for split in ("train", "val", "test"):
        for k in ("X", "y", "ts", "event_id", "amount", "type", "src", "dst"):
            a = np.load(out_a / f"{split}_{k}.npy")
            b = np.load(out_b / f"{split}_{k}.npy")
            assert np.array_equal(a, b), f"{split}_{k}"
    assert _names(out_a) == _names(out_b)


def test_names_and_meta_match_registry(tiny_cfg_session, tmp_workdir):
    world, out, meta = _build(tiny_cfg_session, tmp_workdir, "registry")
    names = _names(out)
    assert names == names_for_cfg(tiny_cfg_session, "G2")
    assert names == feature_names("G2")
    assert meta["world"] == world.name
    assert meta["priority"] == "P1"
    assert meta["registry_hash"] == registry_hash_for_cfg(tiny_cfg_session, "G2")
    assert meta["config_hash"] == tiny_cfg_session.config_hash
    assert meta["split_ts"] == {
        "burn_end": BURN_END,
        "train_end": TRAIN_END,
        "val_end": VAL_END,
        "test_end": TEST_END,
    }
    assert meta["engine_stats"]["events"] == meta["events_replayed"]
    assert meta["limit"] is None


# ------------------------------------------------------------------ tiny world (session fixture)
@pytest.mark.slow
def test_tiny_world_matrices_are_self_consistent(tiny_cfg_session, tiny_world, tiny_features):
    meta = json.loads((tiny_features / "meta.json").read_text(encoding="utf-8"))
    assert meta["world"] == tiny_world.name
    assert meta["registry_hash"] == registry_hash_for_cfg(tiny_cfg_session, "G2")
    assert _names(tiny_features) == names_for_cfg(tiny_cfg_session, "G2")
    assert meta["rows"] == {
        s: int(np.load(tiny_features / f"{s}_X.npy", mmap_mode="r").shape[0])
        for s in ("train", "val", "test")
    }
    n_events = 0
    for le in iter_events(tiny_world / "events.csv.gz"):
        n_events += 1
        assert le.event.amount > 0.0
    assert meta["events_replayed"] == n_events
    assert sum(meta["rows"].values()) + meta["burn_in_rows"] <= n_events
    bounds = {"train": (DAY, TRAIN_END), "val": (TRAIN_END, VAL_END), "test": (VAL_END, TEST_END)}
    for split, (lo, hi) in bounds.items():
        data = load_split(tiny_features, split)
        disk_X = np.load(tiny_features / f"{split}_X.npy", mmap_mode="r")
        assert data["X"].shape == disk_X.shape
        assert data["X"].shape[1] == 59
        assert len(data["ts"]) == data["X"].shape[0] > 0
        ts = np.asarray(data["ts"])
        assert np.all(ts >= lo) and np.all(ts < hi)
        assert np.all(np.diff(ts) >= 0.0)
        assert int(np.asarray(data["y"]).sum()) == meta["positives"][split]
        assert np.isfinite(np.asarray(data["X"])).all()
