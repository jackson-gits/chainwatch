"""Claims checker (spec 20.3): find_numbers, is_flagged_number, forbidden phrases, table
provenance checks. Uses scripts/check_claims.py logic."""
from __future__ import annotations

from scripts import check_claims


def test_find_numbers_basic():
    assert check_claims.find_numbers("recall 95%") == ["95%"]
    assert check_claims.find_numbers("Rs. 10,000") == ["Rs. 10,000"]
    assert check_claims.find_numbers("₹500") == ["₹500"]
    assert check_claims.find_numbers("A00012345") == []
    assert check_claims.find_numbers("1e-7") == []


def test_is_flagged_number():
    assert check_claims.is_flagged_number("95%") is True
    assert check_claims.is_flagged_number("Rs. 100") is True
    assert check_claims.is_flagged_number("₹10") is True
    assert check_claims.is_flagged_number("0.5") is True
    assert check_claims.is_flagged_number("5") is False
    assert check_claims.is_flagged_number("1000") is True


def test_strip_ignored_removes_code_and_links():
    text = "see `code` [link](http://x) https://x.y §10 <!--r:foo-->123<!--/r-->"
    clean = check_claims.strip_ignored(text)
    assert "`code`" not in clean
    assert "http" not in clean
    assert "123" not in clean  # inside a generated region, gets stripped
    assert "§10" not in clean


def test_load_allowed_reads_file(tmp_path):
    p = tmp_path / "allowed.txt"
    p.write_text("#comment\n90% | source\n", encoding="utf-8")
    allowed = check_claims.load_allowed(p)
    assert "90%" in allowed


# ------------------------------------------------------------------ check_file
def _write(root, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_check_file_flags_number_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "README.md", "Ring recall was 42% on the test split.\n")
    problems = check_claims.check_file("README.md", set())
    assert any("42%" in p and "no results source" in p for p in problems)


def test_check_file_allows_number_listed_in_allowed_set(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "README.md", "Ring recall was 42% on the test split.\n")
    problems = check_claims.check_file("README.md", {"42%"})
    assert not any("no results source" in p for p in problems)


def test_check_file_flags_forbidden_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "reports/REPORT.md", "This is a production-ready detector.\n")
    problems = check_claims.check_file("reports/REPORT.md", set())
    assert any("forbidden phrase 'production-ready'" in p for p in problems)


def test_check_file_ignores_numbers_in_code_and_generated_regions(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(
        tmp_path,
        "reports/REPORT.md",
        "```\nvalue = 12345\n```\n<!--r:summary.x-->99%<!--/r-->\n",
    )
    problems = check_claims.check_file("reports/REPORT.md", set())
    assert not any("no results source" in p for p in problems)


def test_check_file_mulehunter_needs_no_comparison_wording(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "reports/REPORT.md", "MuleHunter reports strong numbers on PaySim.\n")
    problems = check_claims.check_file("reports/REPORT.md", set())
    assert any("MuleHunter" in p for p in problems)

    _write(
        tmp_path,
        "reports/REPORT.md",
        "We have not compared with MuleHunter on any shared dataset.\n",
    )
    problems2 = check_claims.check_file("reports/REPORT.md", set())
    assert not any("MuleHunter" in p for p in problems2)


def test_check_file_table_needs_provenance_word(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "README.md", "Some caption\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    problems = check_claims.check_file("README.md", set())
    assert any("lacks a provenance word" in p for p in problems)

    _write(tmp_path, "README.md", "Results on ChainWorld (synthetic)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    problems2 = check_claims.check_file("README.md", set())
    assert not any("lacks a provenance word" in p for p in problems2)


def test_check_file_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    problems = check_claims.check_file("README.md", set())
    assert problems and "missing" in problems[0]


def test_main_returns_zero_on_clean_files(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "reports/REPORT.md", "All good here.\n")
    assert check_claims.main(["reports/REPORT.md"]) == 0


def test_main_returns_one_on_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    _write(tmp_path, "reports/REPORT.md", "production-ready and 99% accurate\n")
    assert check_claims.main(["reports/REPORT.md"]) == 1
