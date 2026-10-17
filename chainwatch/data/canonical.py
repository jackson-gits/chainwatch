"""Canonical event columns, type normalisation, sort order and validation (stdlib only)."""

from __future__ import annotations

from collections.abc import Iterable

from chainwatch.schema import EVENT_TYPES, Event, LabeledEvent

EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "ts",
    "src",
    "dst",
    "amount",
    "type",
    "src_bal_before",
    "src_bal_after",
    "dst_bal_before",
    "dst_bal_after",
)
LABEL_COLUMNS: tuple[str, ...] = ("is_fraud", "ring_id", "hop", "src_role", "dst_role")
COLUMNS: tuple[str, ...] = EVENT_COLUMNS + LABEL_COLUMNS
ACCOUNT_COLUMNS: tuple[str, ...] = ("account_id", "archetype", "is_mule", "is_unwitting", "first_ring_id")

_TYPE_ALIASES = {
    "CASH-IN": "CASH_IN",
    "CASH-OUT": "CASH_OUT",
    "CASHIN": "CASH_IN",
    "CASHOUT": "CASH_OUT",
}
BALANCE_TOL = 0.01


def normalize_type(value: str) -> str:
    """Map PaySim-style spellings (``CASH-OUT``) to the canonical enum."""
    v = value.strip().upper()
    v = _TYPE_ALIASES.get(v, v)
    if v not in EVENT_TYPES:
        raise ValueError(f"unknown transaction type {value!r}")
    return v


def sort_key(ev: Event) -> tuple[float, int]:
    return (ev.ts, ev.event_id)


def validate(events: Iterable[Event | LabeledEvent], max_problems: int = 20) -> list[str]:
    """Return a list of problems (empty when the stream is valid).

    Checks: sorted by (ts, event_id); amount > 0; type in the enum; no duplicate ids; when both balances of a
    side are known, ``src_after = src_before - amount`` and ``dst_after = dst_before + amount`` within 0.01.
    """
    problems: list[str] = []
    seen: set[int] = set()
    prev: tuple[float, int] | None = None
    for item in events:
        ev = item.event if isinstance(item, LabeledEvent) else item
        key = (ev.ts, ev.event_id)
        if prev is not None and key < prev:
            problems.append(f"event {ev.event_id}: not sorted by (ts, event_id)")
        prev = key
        if ev.event_id in seen:
            problems.append(f"event {ev.event_id}: duplicate event_id")
        seen.add(ev.event_id)
        if not ev.amount > 0:
            problems.append(f"event {ev.event_id}: amount must be > 0")
        if ev.type not in EVENT_TYPES:
            problems.append(f"event {ev.event_id}: unknown type {ev.type!r}")
        if ev.src_bal_before is not None and ev.src_bal_after is not None:
            if abs(ev.src_bal_before - ev.amount - ev.src_bal_after) > BALANCE_TOL:
                problems.append(f"event {ev.event_id}: sender balance arithmetic inconsistent")
        if ev.dst_bal_before is not None and ev.dst_bal_after is not None:
            if abs(ev.dst_bal_before + ev.amount - ev.dst_bal_after) > BALANCE_TOL:
                problems.append(f"event {ev.event_id}: receiver balance arithmetic inconsistent")
        if len(problems) >= max_problems:
            problems.append("... (stopped after max_problems)")
            break
    return problems
