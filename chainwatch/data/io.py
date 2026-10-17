"""Read and write canonical ``csv.gz`` event streams with the stdlib (works in the serve env).

Files are written with a fixed gzip header (no file name, ``mtime=0``) so the same rows give byte-identical
files (determinism, rule E3).
"""

from __future__ import annotations

import csv
import gzip
import io
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from chainwatch.data.canonical import ACCOUNT_COLUMNS, COLUMNS
from chainwatch.schema import Event, LabeledEvent


def _fmt(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        return repr(x)
    return str(x)


def _opt_float(s: str) -> float | None:
    return None if s == "" else float(s)


class _GzWriter:
    """Context manager yielding a csv.writer over a deterministic gzip file."""

    def __init__(self, path: str | Path, level: int = 6):
        self.path = Path(path)
        self.level = level

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._raw = open(self.path, "wb")
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0, compresslevel=self.level)
        self._txt = io.TextIOWrapper(self._gz, encoding="utf-8", newline="")
        return csv.writer(self._txt, lineterminator="\n")

    def __exit__(self, *exc):
        self._txt.flush()
        self._txt.detach()
        self._gz.close()
        self._raw.close()
        return False


def write_rows(path: str | Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> int:
    """Write rows (already ordered) to a csv.gz file. Floats use ``repr`` (exact round trip)."""
    n = 0
    with _GzWriter(path) as w:
        w.writerow(list(header))
        for row in rows:
            w.writerow([_fmt(x) for x in row])
            n += 1
    return n


def labeled_row(le: LabeledEvent) -> tuple:
    ev = le.event
    return (
        ev.event_id,
        ev.ts,
        ev.src,
        ev.dst,
        ev.amount,
        ev.type,
        ev.src_bal_before,
        ev.src_bal_after,
        ev.dst_bal_before,
        ev.dst_bal_after,
        le.is_fraud,
        le.ring_id,
        le.hop,
        le.src_role,
        le.dst_role,
    )


def write_events(path: str | Path, events: Iterable[LabeledEvent | Event]) -> int:
    """Write labeled (or plain) events. Plain events get default labels."""

    def rows():
        for item in events:
            le = item if isinstance(item, LabeledEvent) else LabeledEvent(item)
            yield labeled_row(le)

    return write_rows(path, COLUMNS, rows())


def iter_events(path: str | Path) -> Iterator[LabeledEvent]:
    """Stream labeled events from a canonical csv.gz file (label columns optional)."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = {name: i for i, name in enumerate(header)}
        missing = [c for c in COLUMNS[:10] if c not in idx]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}")
        has_labels = all(c in idx for c in COLUMNS[10:])
        i_id, i_ts, i_src, i_dst, i_amt, i_type = (idx[c] for c in COLUMNS[:6])
        i_sb, i_sa, i_db, i_da = (idx[c] for c in COLUMNS[6:10])
        if has_labels:
            i_f, i_r, i_h, i_sr, i_dr = (idx[c] for c in COLUMNS[10:])
        for row in reader:
            ev = Event(
                int(row[i_id]),
                float(row[i_ts]),
                row[i_src],
                row[i_dst],
                float(row[i_amt]),
                row[i_type],
                _opt_float(row[i_sb]),
                _opt_float(row[i_sa]),
                _opt_float(row[i_db]),
                _opt_float(row[i_da]),
            )
            if has_labels:
                yield LabeledEvent(ev, int(row[i_f]), row[i_r], int(row[i_h]), row[i_sr], row[i_dr])
            else:
                yield LabeledEvent(ev)


def read_events(path: str | Path) -> list[LabeledEvent]:
    return list(iter_events(path))


def write_accounts(path: str | Path, rows: Iterable[Iterable[Any]]) -> int:
    return write_rows(path, ACCOUNT_COLUMNS, rows)


def read_accounts(path: str | Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        out = []
        for row in reader:
            row["is_mule"] = int(row["is_mule"])
            row["is_unwitting"] = int(row["is_unwitting"])
            out.append(row)
        return out
