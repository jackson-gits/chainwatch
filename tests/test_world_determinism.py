"""Spec 17.2 (test_world_determinism): determinism and opaque-id checks on the tiny world."""

from __future__ import annotations

import bisect
import hashlib
import json
import re

from chainwatch.data.io import read_accounts
from chainwatch.data.world.generate import generate_world
from tests.tiny import tiny_cfg


def _auc(scores, labels):
    pairs = sorted(zip(scores, labels, strict=False))
    n1 = sum(1 for _, lab in pairs if lab == 1)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return 0.5
    vals = sorted({s for s, _ in pairs})

    def avg_rank(v):
        lo = bisect.bisect_left(vals, v)
        hi = bisect.bisect_right(vals, v)
        return (lo + hi + 1) / 2.0

    u = sum(avg_rank(s) for s, lab in pairs if lab == 1) - n1 * (n1 + 1) / 2.0
    return u / (n1 * n0)


def test_same_seed_gives_byte_identical_files(tmp_workdir) -> None:
    cfg = tiny_cfg()
    a = tmp_workdir / "a" / "w7"
    b = tmp_workdir / "b" / "w7"
    generate_world(cfg, 7, a, progress=False)
    generate_world(cfg, 7, b, progress=False)
    # world_report.json embeds the output dir name and generation_seconds, so it is not
    # byte-comparable; the data files and their manifest hashes must be identical (rule E3).
    for f in ["events.csv.gz", "accounts.csv.gz", "rings.json"]:
        assert (a / f).read_bytes() == (b / f).read_bytes()
    man_a = json.loads((a / "manifest.json").read_text())
    man_b = json.loads((b / "manifest.json").read_text())
    assert man_a["files"]["events.csv.gz"] == man_b["files"]["events.csv.gz"]
    assert man_a["files"]["accounts.csv.gz"] == man_b["files"]["accounts.csv.gz"]
    assert man_a["files"]["rings.json"] == man_b["files"]["rings.json"]
    assert man_a["config_hash"] == man_b["config_hash"]
    assert man_a["seed"] == man_b["seed"]


def test_different_seed_differs(tmp_workdir) -> None:
    cfg = tiny_cfg()
    a = tmp_workdir / "a"
    b = tmp_workdir / "b"
    generate_world(cfg, 7, a, progress=False)
    generate_world(cfg, 11, b, progress=False)
    h1 = hashlib.sha256((a / "events.csv.gz").read_bytes()).hexdigest()
    h2 = hashlib.sha256((b / "events.csv.gz").read_bytes()).hexdigest()
    assert h1 != h2
    acc1 = {a["account_id"] for a in read_accounts(a / "accounts.csv.gz")}
    acc2 = {a["account_id"] for a in read_accounts(b / "accounts.csv.gz")}
    assert acc1 != acc2


def test_ids_carry_no_archetype_information(tmp_workdir) -> None:
    cfg = tiny_cfg()
    wd = tmp_workdir / "w"
    generate_world(cfg, 7, wd, progress=False)
    acc = read_accounts(wd / "accounts.csv.gz")
    assert all(re.fullmatch(r"A\d{8}", a["account_id"]) for a in acc)
    idnums = [int(a["account_id"][1:]) for a in acc]
    mule = [a["is_mule"] for a in acc]
    merch = [1 if a["archetype"] == "merchant" else 0 for a in acc]
    agent = [1 if a["archetype"] == "agent" else 0 for a in acc]
    auc_m = _auc(idnums, mule)
    auc_k = _auc(idnums, merch)
    auc_ag = _auc(idnums, agent)
    assert 0.4 <= auc_m <= 0.6
    assert 0.4 <= auc_k <= 0.6
    assert 0.4 <= auc_ag <= 0.6
    assert len({a["account_id"] for a in acc}) == len(acc)
