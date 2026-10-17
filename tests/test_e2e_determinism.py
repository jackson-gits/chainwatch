"""End-to-end determinism (@slow, spec 8.2.4 / 17.1): generating the tiny world and replaying
its features twice with the same config and seed must produce byte-identical outputs."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.tiny import TINY_SEED, tiny_cfg


def _hashes(d: Path) -> dict[str, str]:
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.mark.slow
def test_world_generation_is_deterministic(tmp_path):
    from chainwatch.data.world.generate import generate_world

    cfg = tiny_cfg()
    w1 = tmp_path / "a" / "world_tiny_seed7"
    w2 = tmp_path / "b" / "world_tiny_seed7"
    generate_world(cfg, TINY_SEED, w1, progress=False)
    generate_world(cfg, TINY_SEED, w2, progress=False)
    h1, h2 = _hashes(w1), _hashes(w2)
    assert h1, "no files produced"
    # Data files are byte-identical. world_report.json records generation_seconds and
    # manifest.json hashes world_report.json, so those two differ by design.
    for key in ("events.csv.gz", "accounts.csv.gz", "rings.json"):
        assert h1[key] == h2[key], f"{key} differs between runs"
    assert h1["events.csv.gz"] == h2["events.csv.gz"]


@pytest.mark.slow
def test_world_report_metadata_differs_only_in_volatile_fields(tmp_path):
    import json

    from chainwatch.data.world.generate import generate_world

    cfg = tiny_cfg()
    w1 = tmp_path / "a" / "world_tiny_seed7"
    w2 = tmp_path / "b" / "world_tiny_seed7"
    generate_world(cfg, TINY_SEED, w1, progress=False)
    generate_world(cfg, TINY_SEED, w2, progress=False)
    r1 = json.loads((w1 / "world_report.json").read_text(encoding="utf-8"))
    r2 = json.loads((w2 / "world_report.json").read_text(encoding="utf-8"))
    assert r1["n_events"] == r2["n_events"]
    assert r1["rings_written"] == r2["rings_written"]
    assert r1["fraud_share"] == r2["fraud_share"]
    a = dict(r1)
    b = dict(r2)
    a.pop("generation_seconds", None)
    b.pop("generation_seconds", None)
    assert a == b


@pytest.mark.slow
def test_feature_replay_is_deterministic(tmp_path):
    from chainwatch.data.world.generate import generate_world
    from chainwatch.features.matrix import build_matrices

    cfg = tiny_cfg()
    wd = tmp_path / "world"
    generate_world(cfg, TINY_SEED, wd / "world_tiny_seed7", progress=False)
    f1 = tmp_path / "f1"
    f2 = tmp_path / "f2"
    build_matrices(cfg, wd / "world_tiny_seed7", f1)
    build_matrices(cfg, wd / "world_tiny_seed7", f2)
    h1, h2 = _hashes(f1), _hashes(f2)
    assert h1, "no feature files produced"
    # meta.json contains replay_seconds/us_per_event, which vary run to run; the data
    # matrices themselves must be byte-identical.
    for key in ("train_X.npy", "val_X.npy", "test_X.npy", "train_y.npy", "test_y.npy", "names.json"):
        assert h1[key] == h2[key], f"{key} differs between runs"
