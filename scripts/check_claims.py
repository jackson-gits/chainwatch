"""Claims check (spec 20.3, rule H1). Fails when public docs contain a number without a results source,
a forbidden phrase, a MuleHunter mention without the no-comparison wording, or a results table without a
provenance word.

Scans README.md, reports/REPORT.md and the rendered docs. Ignores fenced code blocks, inline code, link
targets and generated regions (``<!--r:...-->...<!--/r-->``). Allowed hand-typed numbers live in
docs/allowed_numbers.txt (``token | source | note``).

Usage: python scripts/check_claims.py [files...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = [
    "README.md",
    "reports/REPORT.md",
    "docs/model_card.md",
    "docs/data_card.md",
    "docs/architecture.md",
    "docs/demo_script.md",
    "docs/deck_outline.md",
]
TABLE_FILES = {"README.md", "reports/REPORT.md"}
FORBIDDEN = [
    "production-ready",
    "production ready",
    "state of the art",
    "state-of-the-art",
    "outperforms",
    "beats mulehunter",
    "better than mulehunter",
    "better than rbi",
    "real-world accuracy",
    "real world accuracy",
    "detects mule accounts in real",
    "saves rs",
    "saves inr",
    "saves ₹",
]
PROVENANCE = ("ChainWorld", "PaySim", "Organizer")
GENERATED = re.compile(r"<!--r:[^>]*-->.*?<!--/r-->", re.S)
FENCED = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]*`")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
SECTION_REF = re.compile(r"§\s?\d+(\.\d+)*")
NUMBER = re.compile(r"(?:(?:Rs\.?|INR|₹)\s?)?\d[\d,]*(?:\.\d+)?%?")


def find_numbers(line: str) -> list[str]:
    """Standalone numbers in a line (not parts of identifiers such as A00012345, s390x or 1e-7)."""
    out = []
    for m in NUMBER.finditer(line):
        a, b = m.start(), m.end()
        tok = m.group(0)
        while tok.endswith(","):
            tok = tok[:-1]
            b -= 1
        if tok and tok[0].isdigit() and a > 0 and (line[a - 1].isalnum() or line[a - 1] in "._-/"):
            continue
        if b < len(line) and (line[b].isalnum() or line[b] in "_"):
            continue
        if b + 1 < len(line) and line[b] in ".-" and line[b + 1].isalnum():
            continue
        out.append(tok)
    return out


def load_allowed(path: Path) -> set[str]:
    allowed = set()
    if not path.exists():
        return allowed
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split("|", 1)[0].strip()
        if token:
            allowed.add(token)
    return allowed


def strip_ignored(text: str) -> str:
    text = FENCED.sub(" ", text)
    text = GENERATED.sub(" ", text)
    text = HTML_COMMENT.sub(" ", text)
    text = INLINE.sub(" ", text)
    text = LINK_TARGET.sub("]", text)
    text = URL.sub(" ", text)
    text = SECTION_REF.sub(" ", text)
    return text


def is_flagged_number(tok: str) -> bool:
    t = tok.strip()
    if t.endswith("%"):
        return True
    if re.match(r"^(Rs\.?|INR|₹)", t):
        return True
    if "." in t:
        return True
    digits = t.replace(",", "")
    try:
        return int(digits) >= 100
    except ValueError:
        return True


def check_file(rel: str, allowed: set[str]) -> list[str]:
    path = ROOT / rel
    if not path.exists():
        return [f"{rel}: missing (run `chainwatch report` first)"]
    raw = path.read_text(encoding="utf-8")
    problems = []
    clean = strip_ignored(raw)
    for lineno, line in enumerate(clean.splitlines(), 1):
        for tok in find_numbers(line):
            if is_flagged_number(tok) and tok not in allowed:
                problems.append(
                    f"{rel}:{lineno}: number '{tok}' has no results source "
                    f"(use a placeholder or add it to docs/allowed_numbers.txt)"
                )
    low = HTML_COMMENT.sub(" ", raw).lower()
    for phrase in FORBIDDEN:
        if phrase in low:
            problems.append(f"{rel}: forbidden phrase '{phrase}'")
    for para in re.split(r"\n\s*\n", HTML_COMMENT.sub(" ", raw)):
        pl = para.lower()
        if "mulehunter" in pl and "do not claim" not in pl and "have not compared" not in pl:
            problems.append(
                f"{rel}: a paragraph mentions MuleHunter without 'do not claim' or 'have not compared'"
            )
    if rel in TABLE_FILES:
        lines = HTML_COMMENT.sub("", FENCED.sub("", raw)).splitlines()
        i = 0
        while i < len(lines):
            if lines[i].lstrip().startswith("|"):
                j = i - 1
                context = []
                while j >= 0 and len(context) < 2:
                    if lines[j].strip():
                        context.append(lines[j])
                    j -= 1
                if not any(p in c for c in context for p in PROVENANCE):
                    problems.append(
                        f"{rel}: table near line {i + 1} lacks a provenance word "
                        f"({', '.join(PROVENANCE)}) in its caption or the line above"
                    )
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
            i += 1
    return problems


def main(argv: list[str]) -> int:
    files = argv or DEFAULT_FILES
    allowed = load_allowed(ROOT / "docs" / "allowed_numbers.txt")
    problems = []
    for rel in files:
        problems.extend(check_file(rel, allowed))
    if problems:
        print("check-claims FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print(f"check-claims OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
