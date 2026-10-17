"""Python 3.10 syntax check (spec 17.2, ``tests/serving/test_py310_syntax.py``).

``ast.parse`` every file under ``chainwatch/serving``, ``chainwatch/features``,
``chainwatch/model/portable_gbdt.py``, ``chainwatch/rings``, ``chainwatch/decision``,
``chainwatch/data/canonical.py``, ``chainwatch/data/io.py``, ``chainwatch/config.py``,
``chainwatch/schema.py`` with ``feature_version=(3, 10)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PATTERNS = [
    "chainwatch/serving/*.py",
    "chainwatch/features/*.py",
    "chainwatch/model/portable_gbdt.py",
    "chainwatch/rings/*.py",
    "chainwatch/decision/*.py",
    "chainwatch/data/canonical.py",
    "chainwatch/data/io.py",
    "chainwatch/config.py",
    "chainwatch/schema.py",
]


def _collect():
    root = Path(__file__).resolve().parents[2]
    files = []
    for pat in _PATTERNS:
        files.extend(root.glob(pat))
    # also include __init__.py in the listed subdirs? the glob already gives *.py
    # ensure unique, sorted
    return sorted(set(str(p.resolve()) for p in files))


def test_py310_syntax_for_all_required_files():
    for path in _collect():
        src = Path(path).read_text(encoding="utf-8", errors="replace")
        ast.parse(src, filename=path, feature_version=(3, 10))
