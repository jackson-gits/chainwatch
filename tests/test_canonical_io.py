"""Spec 17.2 (test_canonical_io): csv.gz round trip with nulls, sort check, validation catches.

Everything here uses the stdlib-only reader/writer of ``chainwatch.data.io`` (rule: the same code
must run in the serve environment).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chainwatch.data.canonical import (
    ACCOUNT_COLUMNS,
    COLUMNS,
    EVENT_COLUMNS,
    normalize_type,
    sort_key,
    validate,
)
from chainwatch.data.io import (
    read_accounts,
    read_events,
    write_accounts,
    write_events,
    write_rows,
)
from chainwatch.schema import Event, LabeledEvent


def _ev(event_id: int, ts: float, amount: float = 100.0, **kw) -> Event:
    defaults = dict(
        src=f"A0000{event_id:04d}",
        dst=f"A0001{event_id:04d}",
        amount=amount,
        type="TRANSFER",
        src_bal_before=1000.0,
        src_bal_after=1000.0 - amount,
        dst_bal_before=50.0,
        dst_bal_after=50.0 + amount,
    )
    defaults.update(kw)
    return Event(event_id, ts, **defaults)


# --- round trips -----------------------------------------------------------------------------


def test_round_trip_with_nulls_keeps_fields_exactly(tmp_path: Path) -> None:
    exact = 0.1 + 0.2  # 0.30000000000000004: repr() must round trip bit-exactly
    events = [
        _ev(0, 1.5, amount=exact, src_bal_after=1000.0 - exact, dst_bal_after=50.0 + exact),
        _ev(1, 2.25, src_bal_before=None, src_bal_after=None, dst_bal_before=None, dst_bal_after=None),
        _ev(2, 2.25, dst_bal_before=None, dst_bal_after=None),
    ]
    path = tmp_path / "events.csv.gz"
    assert write_events(path, events) == 3
    back = read_events(path)
    assert len(back) == 3
    for orig, got in zip(events, back, strict=False):
        assert got.event.to_dict() == orig.to_dict()
        assert got.event.ts == orig.ts


def test_round_trip_labeled_events(tmp_path: Path) -> None:
    le = LabeledEvent(_ev(0, 1.0), is_fraud=1, ring_id="4", hop=2, src_role="mule", dst_role="sink")
    path = tmp_path / "events.csv.gz"
    write_events(path, [le])
    (back,) = read_events(path)
    assert (back.is_fraud, back.ring_id, back.hop, back.src_role, back.dst_role) == (
        1,
        "4",
        2,
        "mule",
        "sink",
    )


def test_plain_events_written_with_default_labels(tmp_path: Path) -> None:
    path = tmp_path / "events.csv.gz"
    write_events(path, [_ev(0, 1.0)])
    (back,) = read_events(path)
    assert (back.is_fraud, back.ring_id, back.hop) == (0, "-1", -1)
    assert (back.src_role, back.dst_role) == ("legit", "legit")


def test_header_without_label_columns_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "bare.csv.gz"
    ev = _ev(0, 1.0)
    row = [ev.event_id, ev.ts, ev.src, ev.dst, ev.amount, ev.type, None, None, None, None]
    write_rows(path, EVENT_COLUMNS, [row])
    (back,) = read_events(path)
    assert back.event.to_dict() == Event(0, 1.0, ev.src, ev.dst, ev.amount, ev.type).to_dict()


def test_missing_event_column_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv.gz"
    write_rows(path, ["event_id", "ts", "src", "dst", "type"], [])
    with pytest.raises(ValueError, match="missing columns"):
        read_events(path)


def test_write_is_byte_deterministic(tmp_path: Path) -> None:
    events = [_ev(0, 1.0), _ev(1, 2.0)]
    a, b = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    write_events(a, events)
    write_events(b, events)
    assert a.read_bytes() == b.read_bytes()


def test_accounts_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "accounts.csv.gz"
    write_accounts(path, [("A00000001", "customer", 0, 1, "-1"), ("A00000002", "mule_account", 1, 0, "4")])
    rows = read_accounts(path)
    assert [r["account_id"] for r in rows] == ["A00000001", "A00000002"]
    assert rows[0]["is_mule"] == 0 and rows[0]["is_unwitting"] == 1
    assert rows[1]["is_mule"] == 1 and rows[1]["first_ring_id"] == "4"
    assert ACCOUNT_COLUMNS == ("account_id", "archetype", "is_mule", "is_unwitting", "first_ring_id")
    assert COLUMNS == EVENT_COLUMNS + ("is_fraud", "ring_id", "hop", "src_role", "dst_role")


# --- validate() ------------------------------------------------------------------------------


def test_validate_accepts_a_clean_stream() -> None:
    assert validate([_ev(0, 1.0), _ev(1, 1.0), _ev(2, 2.5)]) == []
    assert validate([LabeledEvent(_ev(0, 1.0), is_fraud=1)]) == []


def test_validate_catches_unsorted_timestamps() -> None:
    problems = validate([_ev(0, 5.0), _ev(1, 4.0)])
    assert any("not sorted" in p for p in problems)


def test_validate_catches_tie_break_violations() -> None:
    # equal ts: the lower event_id must come first
    problems = validate([_ev(5, 2.0), _ev(3, 2.0)])
    assert any("not sorted" in p for p in problems)


def test_validate_catches_duplicate_ids() -> None:
    problems = validate([_ev(7, 1.0), _ev(7, 2.0)])
    assert any("duplicate event_id" in p for p in problems)


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_validate_catches_non_positive_amounts(amount: float) -> None:
    problems = validate([_ev(0, 1.0, amount=amount)])
    assert any("amount must be > 0" in p for p in problems)


def test_validate_catches_unknown_type() -> None:
    problems = validate([_ev(0, 1.0, type="WIRE")])
    assert any("unknown type" in p for p in problems)


def test_validate_catches_inconsistent_sender_balance() -> None:
    bad = _ev(0, 1.0, amount=10.0, src_bal_before=1000.0, src_bal_after=89.9)
    assert any("sender balance arithmetic" in p for p in validate([bad]))


def test_validate_catches_inconsistent_receiver_balance() -> None:
    bad = _ev(0, 1.0, amount=10.0, dst_bal_before=50.0, dst_bal_after=60.02)
    assert any("receiver balance arithmetic" in p for p in validate([bad]))


def test_balance_tolerance_of_one_paisa_is_accepted() -> None:
    ok = _ev(0, 1.0, amount=10.0, src_bal_after=990.005, dst_bal_after=60.0)
    assert validate([ok]) == []


def test_validate_stops_after_max_problems() -> None:
    events = [_ev(i, float(i), amount=0.0) for i in range(30)]
    problems = validate(events, max_problems=5)
    assert problems[-1] == "... (stopped after max_problems)"
    assert len(problems) == 6


def test_validate_handles_mixed_event_and_labeled_event() -> None:
    stream = [_ev(0, 1.0), LabeledEvent(_ev(1, 2.0), is_fraud=1, ring_id="0"), _ev(2, 3.0)]
    assert validate(stream) == []


def test_sort_key_is_ts_then_event_id() -> None:
    assert sort_key(_ev(3, 1.5)) == (1.5, 3)


# --- type normalisation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CASH-OUT", "CASH_OUT"),
        ("CASH-IN", "CASH_IN"),
        ("cash-out", "CASH_OUT"),
        (" cash_out ", "CASH_OUT"),
        ("TRANSFER", "TRANSFER"),
        ("PAYMENT", "PAYMENT"),
        ("DEBIT", "DEBIT"),
    ],
)
def test_normalize_type(raw: str, expected: str) -> None:
    assert normalize_type(raw) == expected


def test_normalize_type_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown transaction type"):
        normalize_type("WIRE")
