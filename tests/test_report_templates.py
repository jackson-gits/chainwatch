"""Report rendering (spec 20.3): placeholder resolution, filters, generated-value wrapping,
Markdown tables from a synthetic summary, and failure on missing keys/unknown filters.

These tests use a small in-memory resolver so they do not depend on results/ being rendered.
"""
from __future__ import annotations

import pytest

from chainwatch.eval import report


class FakeResolver:
    """Minimal stand-in for report.Resolver backed by a dict of namespaces."""

    def __init__(self, data: dict):
        self.data = data

    def ns(self, name: str):
        if name not in self.data:
            raise report.MissingKey(f"namespace '{name}' is missing")
        return self.data[name]

    def get(self, dotted: str):
        parts = dotted.split(".")
        node = self.ns(parts[0])
        if isinstance(node, report.TableNS):
            return node.get(".".join(parts[1:]))
        for p in parts[1:]:
            if isinstance(node, dict) and p in node:
                node = node[p]
            elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
                node = node[int(p)]
            else:
                raise report.MissingKey(f"{dotted}: key '{p}' not found")
        return node


def _summary() -> dict:
    variants = {}
    for v in ("RULE", "B0", "B0+R", "B1", "B1+R", "G2", "G2+R"):
        variants[v] = {
            "pooled": {
                "ring_recall": 0.5,
                "ring_recall_ci": [0.4, 0.6],
                "n_rings": 20,
                "before_exit": 0.75,
                "ttc_median_min": 12.5,
                "deployed_recall": 0.6,
            },
            "test_fpr": {"mean": 0.01, "min": 0.007, "max": 0.013},
            "fpr_off_target_any": False,
            "pr_auc": {"mean": 0.42},
        }
    return {"n_worlds": 3, "fpr_target": 0.01, "variants": variants, "paired": {}}


def test_fmt_applies_every_filter():
    assert report.fmt(1234, "int", "p") == "1,234"
    assert report.fmt(1234.567, "num2", "p") == "1,234.57"
    assert report.fmt(0.5, "pct0", "p") == "50%"
    assert report.fmt(0.5, "pct1", "p") == "50.0%"
    assert report.fmt(90.0, "min1", "p") == "90.0 min"
    assert report.fmt(1500.0, "us0", "p") == "1,500 µs"
    assert report.fmt(1500.0, "ms1", "p") == "1.5 ms"
    assert report.fmt([0.4, 0.6], "ci", "p") == "(95% interval 40% to 60%)"
    assert report.fmt([1.0, 2.0], "ci_min", "p") == "(95% interval 1.0 to 2.0 min)"
    assert report.fmt(0.05, "pp1", "p") == "+5.0 points"
    assert report.fmt("x", "str", "p") == "x"
    assert report.fmt(None, "pct0", "p") == "n/a"
    with pytest.raises(report.MissingKey):
        report.fmt(0.5, "bogus", "p")


def test_fmt_null_without_filter_raises():
    with pytest.raises(report.MissingKey):
        report.fmt(None, None, "summary.x")
    with pytest.raises(report.MissingKey):
        report.fmt(None, "raw", "summary.x")


def test_render_text_wraps_values_and_resolves():
    r = FakeResolver({"summary": {"n_worlds": 3, "fpr_target": 0.01}})
    text, probs = report.render_text("worlds: {{ summary.n_worlds }} fpr {{ summary.fpr_target | pct1 }}", r)
    assert probs == []
    assert text == "worlds: <!--r:summary.n_worlds-->3<!--/r--> fpr <!--r:summary.fpr_target-->1.0%<!--/r-->"


def test_render_text_reports_missing_key_and_unknown_filter():
    r = FakeResolver({"summary": {"n_worlds": 3}})
    _, probs = report.render_text("{{ summary.missing }}", r)
    assert probs and "missing" in probs[0]
    _, probs2 = report.render_text("{{ summary.n_worlds | nope }}", r)
    assert probs2 and "unknown filter" in probs2[0]


def test_render_text_reports_unresolved_leftover():
    text, probs = report.render_text("{{ a b }}", FakeResolver({}))
    assert text == "{{ a b }}"
    assert probs and "unresolved placeholder" in probs[0]


def test_table_main_renders_all_variants():
    r = FakeResolver({"summary": _summary()})
    block = report.TableNS(r).get("main")
    assert isinstance(block, report.GeneratedBlock)
    assert "G2+R ring-aware (served)" in block.text
    assert "ChainWorld" in block.text
    for v in ("RULE", "B0", "B0+R", "B1", "B1+R", "G2", "G2+R"):
        assert report.VARIANT_LABEL[v] in block.text


def test_table_unknown_name_raises():
    r = FakeResolver({"summary": _summary()})
    with pytest.raises(report.MissingKey, match="unknown table"):
        report.TableNS(r).get("does_not_exist")


def test_table_leakage_reports_pass_and_fail():
    lk = {
        "world_x": {
            "checks": {
                "L1": {"pass": True},
                "L2": {"pass": False, "pr_auc": 0.2, "bound": 0.049},
                "L4": {"pass": True, "top": [{"feature": "f", "auc": 0.97}]},
                "L6": {"pass": True, "share_reused": 0.0},
            }
        }
    }
    r = FakeResolver({"leak": lk})
    text = report.TableNS(r).get("leakage").text
    assert "FAIL" in text
    assert "PR-AUC 0.2000, bound 0.0490" in text
    assert "world_x" in text


def test_render_all_reports_missing_template(monkeypatch):
    # point TEMPLATES at a nonexistent template: render_all must return a problem, not write
    from tests.tiny import tiny_cfg

    fake = report.ROOT / "docs" / "templates" / "__does_not_exist__.md"
    never = report.ROOT / "reports" / "__never__.md"
    monkeypatch.setattr(report, "TEMPLATES", [(fake, never)])
    problems = report.render_all(tiny_cfg(), figures=False)
    assert problems and "missing template" in problems[0]
    assert not never.exists()
