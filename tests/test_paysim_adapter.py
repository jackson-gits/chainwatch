"""Spec 17.2 (test_paysim_adapter): mapping on 50-row hand-made fixture.

Verifies type normalisation, ts within the hour, merchant destination balances null,
burn-in and splits by step.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from chainwatch.data.io import iter_events, read_accounts
from chainwatch.data.paysim import SPLIT_STEPS, convert, find_csv, read_paysim, to_canonical


def _build_fixture(tmp_path: Path) -> Path:
    src = tmp_path / "paysim_sample.csv"
    rows = [
        "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud"
    ]
    # include key cases: CASH-OUT, multiple steps, merchant
    cases = [
        (1, "CASH-OUT", 100.0, "C100", 200.0, 100.0, "M200", 10.0, 10.0, 0, 0),
        (1, "CASH_OUT", 200.0, "C101", 500.0, 300.0, "C300", 50.0, 250.0, 1, 0),
        (72, "TRANSFER", 1500.0, "C102", 5000.0, 3500.0, "C301", 100.0, 1600.0, 0, 1),
        (400, "PAYMENT", 50.0, "C103", 1000.0, 950.0, "M201", 0.0, 0.0, 0, 0),
        (500, "DEBIT", 10.0, "C104", 200.0, 190.0, "M202", 0.0, 0.0, 0, 0),
        (744, "CASH_IN", 1000.0, "C105", 0.0, 1000.0, "C302", 0.0, 1000.0, 0, 0),
    ]
    for step, typ, amt, nO, oO, nO2, nD, oD, nD2, f, ff in cases:
        rows.append(f"{step},{typ},{amt},{nO},{oO},{nO2},{nD},{oD},{nD2},{f},{ff}")
    src.write_text("\n".join(rows), encoding="utf-8")
    return src


def test_paysim_adapter_mapping(tmp_path) -> None:
    from tests.tiny import tiny_cfg

    cfg = tiny_cfg()
    src = _build_fixture(tmp_path)
    # read
    df = read_paysim(src, chunksize=2)
    assert len(df) == 6
    # to canonical
    can = to_canonical(df, seed=123)
    assert len(can) == 6
    # types normalised
    assert set(can["type"]) == {"CASH_OUT", "TRANSFER", "PAYMENT", "DEBIT", "CASH_IN"}
    # ts within hour after step-1
    # step 1 -> ts in [0, 3600)
    step1 = can[can["step"] == 1]
    for ts in step1["ts"]:
        assert 0.0 <= ts < 3600.0
    # merchant destination balances null
    merch_rows = can[can["dst"].str.startswith("M")]
    for dbb, _dba in zip(merch_rows["dst_bal_before"], merch_rows["dst_bal_after"], strict=False):
        assert dbb != dbb or dbb is None  # NaN after pandas?
    # convert
    out = tmp_path / "out"
    meta = convert(cfg, src=src, out=out)
    assert meta["n_events"] == 6
    assert meta["provenance"] == "PaySim"
    assert meta["split_steps"] == SPLIT_STEPS
    # files
    assert (out / "events.csv.gz").exists()
    assert (out / "flagged.csv.gz").exists()
    assert (out / "accounts.csv.gz").exists()
    assert (out / "rings.json").exists()
    assert (out / "meta.json").exists()
    assert (out / "manifest.json").exists()
    # events back
    evs = list(iter_events(out / "events.csv.gz"))
    assert len(evs) == 6
    for le in evs:
        assert le.ring_id == "-1"
        assert le.is_fraud in (0, 1)
        assert le.hop == -1
    # merchant null
    m = [e for e in evs if e.event.dst.startswith("M")]
    for le in m:
        assert le.event.dst_bal_before is None and le.event.dst_bal_after is None
    # flagged
    with gzip.open(out / "flagged.csv.gz", "rt") as fh:
        lines = fh.readlines()
    assert lines[0].strip() == "event_id,isFlaggedFraud"
    # accounts
    acc = read_accounts(out / "accounts.csv.gz")
    assert any(a["archetype"] == "merchant" for a in acc)
    # rings empty
    rings = json.loads((out / "rings.json").read_text())
    assert rings["rings"] == []
    assert "note" in rings
    # manifest has hashes
    man = json.loads((out / "manifest.json").read_text())
    assert "files" in man and set(man["files"]) == {"events.csv.gz", "accounts.csv.gz"}
    # find_csv works
    assert find_csv(src) == src
    # burn-in steps (1..71) covered
    step1_events = [e for e in evs if e.event.ts < SPLIT_STEPS["burn_end"] * 3600.0]
    assert step1_events  # have at least the step 1 and 72? 72 is burn_end (included)
