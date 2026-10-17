"""Report rendering (spec 20.3): templates with ``{{ namespace.path | filter }}`` placeholders, generated
tables and figures. Every rendered value is wrapped in ``<!--r:path-->value<!--/r-->`` so the claims
checker can tell generated numbers from hand-typed ones. A missing key, an unknown filter or an unresolved
placeholder fails the build and lists every problem; there are no silent blanks.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from chainwatch.config import ROOT, Config

log = logging.getLogger(__name__)
RESULTS = ROOT / "results"
FIG_DIR = ROOT / "reports" / "figures"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.+\-]+)\s*(?:\|\s*([a-z0-9_]+)\s*)?\}\}")
FILTERS = (
    "int",
    "num0",
    "num1",
    "num2",
    "pct0",
    "pct1",
    "min1",
    "us0",
    "ms1",
    "ci",
    "ci_min",
    "pp1",
    "str",
    "raw",
)
TEMPLATES = [
    (ROOT / "README.template.md", ROOT / "README.md"),
    (ROOT / "docs" / "report_template.md", ROOT / "reports" / "REPORT.md"),
    (ROOT / "docs" / "templates" / "demo_script.template.md", ROOT / "docs" / "demo_script.md"),
    (ROOT / "docs" / "templates" / "deck_outline.template.md", ROOT / "docs" / "deck_outline.md"),
    (ROOT / "docs" / "templates" / "model_card.template.md", ROOT / "docs" / "model_card.md"),
    (ROOT / "docs" / "templates" / "data_card.template.md", ROOT / "docs" / "data_card.md"),
]
VARIANT_LABEL = {
    "RULE": "RULE (relay rule only)",
    "B0": "B0 one payment at a time",
    "B0+R": "B0+R",
    "B1": "B1 account history",
    "B1+R": "B1+R account history + rings",
    "G2": "G2 graph features",
    "G2+R": "G2+R ring-aware (served)",
}


class MissingKey(KeyError):
    pass


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class Resolver:
    """Loads results files lazily and resolves dotted paths."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg
        self.cache: dict = {}

    def ns(self, name: str):
        if name in self.cache:
            return self.cache[name]
        data = None
        if name == "summary":
            data = _load(RESULTS / "summary.json")
        elif name == "world":
            data = _load(RESULTS / "world_manifest.json")
        elif name == "paysim":
            data = _load(RESULTS / "paysim_profile.json")
            ev = _load(RESULTS / "eval_paysim.json")
            if data is not None and ev is not None:
                data = dict(data, eval=ev)
        elif name == "ablation":
            data = _load(RESULTS / "ablation_summary.json")
        elif name == "robust":
            data = _load(RESULTS / "robustness.json")
        elif name == "leak":
            data = {p.stem[len("leakage_") :]: _load(p) for p in sorted(RESULTS.glob("leakage_*.json"))}
            data["all_blocking_ok"] = (
                all(v["blocking_ok"] for v in data.values() if isinstance(v, dict)) if data else None
            )
        elif name in ("bench", "platform"):
            data = {}
            pattern = "bench_*.json" if name == "bench" else "platform_*.json"
            for p in sorted(RESULTS.glob(pattern)):
                d = _load(p)
                host = d.get("host_tag") if isinstance(d, dict) else None
                if not host:
                    continue
                if name == "bench":
                    kind = (
                        "engine_compare"
                        if p.stem.startswith("bench_engine_compare")
                        else ("engine" if p.stem.startswith("bench_engine") else "load")
                    )
                    data.setdefault(host, {})[kind] = d
                else:
                    data[host] = d
        elif name == "parity":
            data = {
                p.stem[len("parity_compare_") :]: _load(p)
                for p in sorted(RESULTS.glob("parity_compare_*.json"))
            }
        elif name == "eval":
            data = {p.stem[len("eval_") :]: _load(p) for p in sorted(RESULTS.glob("eval_*.json"))}
        elif name == "decisions":
            data = _load(RESULTS / "expectations.json")
        elif name == "cfg":
            data = self.cfg.to_dict() if self.cfg is not None else None
        elif name == "table":
            data = TableNS(self)
        self.cache[name] = data
        return data

    def get(self, dotted: str):
        parts = dotted.split(".")
        node = self.ns(parts[0])
        if node is None:
            raise MissingKey(f"{dotted}: results for namespace '{parts[0]}' are missing")
        if isinstance(node, TableNS):
            return node.get(".".join(parts[1:]))
        for p in parts[1:]:
            if isinstance(node, dict) and p in node:
                node = node[p]
            elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
                node = node[int(p)]
            else:
                raise MissingKey(f"{dotted}: key '{p}' not found")
        return node


def fmt(value, filt: str | None, path: str) -> str:
    if filt in (None, "str", "raw"):
        if value is None:
            raise MissingKey(f"{path}: value is null")
        return str(value)
    if value is None:
        return "n/a"
    if filt == "int":
        return f"{int(round(float(value))):,}"
    if filt == "num0":
        return f"{float(value):,.0f}"
    if filt == "num1":
        return f"{float(value):,.1f}"
    if filt == "num2":
        return f"{float(value):,.2f}"
    if filt == "pct0":
        return f"{float(value) * 100:.0f}%"
    if filt == "pct1":
        return f"{float(value) * 100:.1f}%"
    if filt == "min1":
        return f"{float(value):.1f} min"
    if filt == "us0":
        return f"{float(value):,.0f} µs"
    if filt == "ms1":
        return f"{float(value) / 1000.0:.1f} ms"
    if filt == "ci":
        lo, hi = value
        if lo is None:
            return "(interval not available)"
        return f"(95% interval {lo * 100:.0f}% to {hi * 100:.0f}%)"
    if filt == "ci_min":
        lo, hi = value
        if lo is None:
            return "(interval not available)"
        return f"(95% interval {lo:.1f} to {hi:.1f} min)"
    if filt == "pp1":
        return f"{float(value) * 100:+.1f} points"
    raise MissingKey(f"{path}: unknown filter {filt!r}")


def render_text(text: str, resolver: Resolver) -> tuple[str, list[str]]:
    problems: list[str] = []

    def sub(m: re.Match) -> str:
        path, filt = m.group(1), m.group(2)
        if filt is not None and filt not in FILTERS:
            problems.append(f"{path}: unknown filter {filt!r}")
            return m.group(0)
        try:
            val = resolver.get(path)
            out = val if isinstance(val, GeneratedBlock) else fmt(val, filt, path)
        except MissingKey as e:
            problems.append(str(e).strip("'\""))
            return m.group(0)
        if isinstance(out, GeneratedBlock):
            return f"<!--r:{path}-->\n{out.text}\n<!--/r-->"
        return f"<!--r:{path}-->{out}<!--/r-->"

    rendered = PLACEHOLDER.sub(sub, text)
    leftover = re.findall(r"\{\{[^}]*\}\}", rendered)
    if leftover and not problems:
        problems.extend(f"unresolved placeholder {x}" for x in leftover)
    return rendered, problems


class GeneratedBlock:
    def __init__(self, text: str):
        self.text = text


# ---------------------------------------------------------------------------------------------- tables
def _p(x, digits=0):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def _m(x):
    return "n/a" if x is None else f"{x:.1f}"


class TableNS:
    """``{{ table.<name> }}`` renders a Markdown table (with a provenance caption) from results."""

    def __init__(self, resolver: Resolver):
        self.r = resolver

    def get(self, name: str) -> GeneratedBlock:
        fn = getattr(self, "t_" + name.replace(".", "_"), None)
        if fn is None:
            raise MissingKey(f"table.{name}: unknown table")
        return GeneratedBlock(fn())

    def _summary(self):
        s = self.r.ns("summary")
        if s is None:
            raise MissingKey("table: results/summary.json is missing")
        return s

    def t_main(self) -> str:
        s = self._summary()
        rows = [
            "Table: main results, ChainWorld (synthetic), "
            f"{s['n_worlds']} worlds, test split, thresholds fixed on validation, target false-alarm rate "
            f"{s['fpr_target'] * 100:.1f}%.",
            "",
            "| Variant | Ring recall (pooled, 95% CI) | Caught before first cash-out | "
            "Median time to catch | Transaction recall (deployed) | Realised test FPR (mean, min to max) | "
            "PR-AUC (mean) |",
            "|---|---|---|---|---|---|---|",
        ]
        for v in ("RULE", "B0", "B0+R", "B1", "B1+R", "G2", "G2+R"):
            if v not in s["variants"]:
                continue
            d = s["variants"][v]
            p = d["pooled"]
            ci = p["ring_recall_ci"]
            fpr = d["test_fpr"]
            flag = " (off target)" if d["fpr_off_target_any"] and v != "RULE" else ""
            prauc = d["pr_auc"]["mean"]
            prauc_s = "n/a" if prauc is None else f"{prauc:.3f}"
            rows.append(
                f"| {VARIANT_LABEL[v]} | {_p(p['ring_recall'])} ({_p(ci[0])} to {_p(ci[1])}), "
                f"n={p['n_rings']} | {_p(p['before_exit'])} | {_m(p['ttc_median_min'])} min | "
                f"{_p(p['deployed_recall'])} | {_p(fpr['mean'], 2)} "
                f"({_p(fpr['min'], 2)} to {_p(fpr['max'], 2)}){flag} | {prauc_s} |"
            )
        return "\n".join(rows)

    def t_paired(self) -> str:
        s = self._summary()
        rows = [
            "Table: paired differences, G2+R minus each variant, same rings and time blocks, ChainWorld "
            "(synthetic). Positive ring-recall difference: G2+R found more; negative time difference: G2+R "
            "was earlier.",
            "",
            "| Compared with | Ring recall difference (95% CI) | Rings only G2+R / only other | "
            "Median time difference, rings caught by both (95% CI) | "
            "Transaction recall difference (95% CI) |",
            "|---|---|---|---|---|",
        ]
        for k, d in s["paired"].items():
            other = k.split("_minus_", 1)[1]
            ci = d["ring_recall_diff_ci"]
            tci = d["ttc_diff_min_ci"]
            dci = d["deployed_recall_diff_ci"]
            rows.append(
                f"| {VARIANT_LABEL.get(other, other)} | {_pp(d['ring_recall_diff'])} "
                f"({_pp(ci[0])} to {_pp(ci[1])}) | "
                f"{d['discordant_g2r_only']} / {d['discordant_other_only']} | "
                f"{_sm(d['ttc_diff_min_median'])} ({_sm(tci[0])} to {_sm(tci[1])}), "
                f"n={d['n_caught_by_both']} | "
                f"{_pp(d['deployed_recall_diff'])} ({_pp(dci[0])} to {_pp(dci[1])}) |"
            )
        return "\n".join(rows)

    def t_typology(self) -> str:
        s = self._summary()
        vs = [v for v in ("B0", "B1+R", "G2+R") if v in s["variants"]]
        typs = sorted({t for v in vs for t in s["variants"][v]["pooled"]["recall_by_typology"]})
        rows = [
            "Table: ring recall by typology (pooled over worlds), ChainWorld (synthetic).",
            "",
            "| Typology | " + " | ".join(VARIANT_LABEL[v] for v in vs) + " |",
            "|---|" + "---|" * len(vs),
        ]
        for t in typs:
            cells = []
            for v in vs:
                d = s["variants"][v]["pooled"]["recall_by_typology"].get(t)
                cells.append("n/a" if d is None else f"{_p(d['recall'])} (n={d['n']})")
            rows.append(f"| {t.replace('_', ' ')} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def t_archetype(self) -> str:
        ev = self.r.ns("eval") or {}
        worlds = [k for k in ev if k.startswith("world_")]
        if not worlds:
            raise MissingKey("table.archetype: no eval_world_*.json")
        agg: dict = {}
        for w in worlds:
            for v in ("B1+R", "G2+R"):
                for arch, d in ev[w]["archetype_fpr"].get(v, {}).items():
                    a = agg.setdefault((v, arch), [0, 0])
                    a[0] += d["accounts_alerted"]
                    a[1] += d["accounts_active"]
        archs = sorted({a for _, a in agg})
        rows = [
            "Table: share of legitimate accounts with at least one verify or hold recommendation in the test "
            f"period, by archetype (pooled over {len(worlds)} worlds), ChainWorld (synthetic).",
            "",
            "| Archetype | B1+R | G2+R |",
            "|---|---|---|",
        ]
        for a in archs:
            cells = []
            for v in ("B1+R", "G2+R"):
                k, n = agg.get((v, a), (0, 0))
                cells.append(f"{_p(k / n if n else None, 1)} ({k} of {n})")
            rows.append(f"| {a.replace('_', ' ')} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def t_ablation(self) -> str:
        a = self.r.ns("ablation")
        if a is None:
            raise MissingKey("table.ablation: results/ablation_summary.json is missing")
        rows = [
            f"Table: ablations (mean over {len(a['worlds'])} worlds), ChainWorld (synthetic). "
            "Rows A3 to A10 are recalibrated on validation (matched FPR).",
            "",
            "| Id | Change | Variant | Ring recall | Before cash-out | Median time to catch | "
            "Transaction recall | Test FPR |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for k in sorted(a["ablations"], key=lambda x: (int(re.sub(r"\D.*", "", x[1:]) or 0), x)):
            d = a["ablations"][k]
            for v, m in d["results"].items():
                rows.append(
                    f"| {k} | {d['change']} | {v} | {_p(m['ring_recall']['mean'])} | "
                    f"{_p(m['caught_before_exit']['mean'])} | {_m(m['ttc_median_min']['mean'])} min | "
                    f"{_p(m['deployed_recall']['mean'])} | {_p(m['test_fpr']['mean'], 2)} |"
                )
        return "\n".join(rows)

    def t_robustness(self) -> str:
        r = self.r.ns("robust")
        if r is None:
            raise MissingKey("table.robustness: results/robustness.json is missing")
        rows = [
            f"Table: robustness grid, small ChainWorld (synthetic) worlds, seeds {r['seeds']}, models not "
            "retrained (except R6), validation thresholds of the model package.",
            "",
            "| Cell | Value | Variant | Ring recall | Transaction recall | Realised FPR | "
            "Median time to catch |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in r["table"]:
            extra = ""
            if t.get("heldout_typology_recall") is not None:
                extra = (
                    f" (held-out typology recall {_p(t['heldout_typology_recall'])} vs "
                    f"{_p(t['reference_typology_recall'])} when trained with it)"
                )
            rows.append(
                f"| {t['cell']} | {t['value']} | {t['variant']} | {_p(t['ring_recall'])}{extra} | "
                f"{_p(t['deployed_recall'])} | {_p(t['test_fpr'], 2)} | {_m(t['ttc_median_min'])} min |"
            )
        return "\n".join(rows)

    def t_leakage(self) -> str:
        lk = self.r.ns("leak")
        worlds = [k for k in lk if isinstance(lk.get(k), dict) and "checks" in lk[k]]
        if not worlds:
            raise MissingKey("table.leakage: no results/leakage_*.json")
        names = {
            "L1": "temporal integrity and causality",
            "L2": "ID-only model",
            "L3": "label-shuffle control",
            "L4": "single-feature screen",
            "L5": "importance concentration",
            "L6": "account reuse",
            "L7": "threshold hygiene",
            "L8": "burn-in excluded",
            "L9": "no label fields when serving",
        }
        rows = [
            "Table: leakage checks, ChainWorld (synthetic) unless the world name says otherwise.",
            "",
            "| Check | " + " | ".join(worlds) + " |",
            "|---|" + "---|" * len(worlds),
        ]
        for cid, label in names.items():
            cells = []
            for w in worlds:
                c = lk[w]["checks"].get(cid, {})
                detail = ""
                if cid in ("L2", "L3") and "pr_auc" in c:
                    detail = f" (PR-AUC {c['pr_auc']:.4f}, bound {c['bound']:.4f})"
                if cid == "L4" and c.get("top"):
                    detail = f" (top: {c['top'][0]['feature']} AUC {c['top'][0]['auc']:.3f})"
                if cid == "L6" and c.get("share_reused") is not None:
                    detail = f" ({c['share_reused'] * 100:.0f}% of test mule accounts seen in train rings)"
                cells.append(("pass" if c.get("pass") else "FAIL") + detail)
            rows.append(f"| {cid} {label} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def t_bench(self) -> str:
        b = self.r.ns("bench") or {}
        if not b:
            raise MissingKey("table.bench: no results/bench_*.json")
        rows = [
            "Table: measured latency and throughput (ChainWorld slice). Engine = inside "
            "ScoringEngine.process; end-to-end = HTTP client on the same machine. Hardware differs between "
            "rows; no platform is called faster.",
            "",
            "| Host | Engine p50 | Engine p95 | Engine p99 | Events/s (engine) | "
            "HTTP p50 / p95 at concurrency 1 | HTTP p50 / p95 at concurrency 32 | Memory (MB) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for host, d in sorted(b.items()):
            e = d.get("engine")
            ld = d.get("load")
            if e is None and ld is None:
                continue

            def lvl(c, ld=ld):
                if not ld:
                    return "not run"
                for x in ld["levels"]:
                    if x["concurrency"] == c:
                        return f"{x['latency_us']['p50'] / 1000:.2f} / {x['latency_us']['p95'] / 1000:.2f} ms"
                return "not run"

            if e:
                t = e["latency_us"]["total"]
                eng = (
                    f"{t['p50']['median']:.0f} µs | {t['p95']['median']:.0f} µs | "
                    f"{t['p99']['median']:.0f} µs | {e['events_per_s']['median']:.0f}"
                )
                mem = e.get("rss_mb_end")
            else:
                eng, mem = "not run | not run | not run | not run", None
            rows.append(f"| {host} | {eng} | {lvl(1)} | {lvl(32)} | {mem if mem is not None else 'n/a'} |")
        return "\n".join(rows)


def _pp(x):
    return "n/a" if x is None else f"{x * 100:+.0f} pts"


def _sm(x):
    return "n/a" if x is None else f"{x:+.1f} min"


# ---------------------------------------------------------------------------------------------- expectations
def resolve_expectations() -> dict:
    """Resolve the pre-registered expectations (spec 14.10) against results; wording follows the numbers."""
    s = _load(RESULTS / "summary.json")
    out: dict = {}
    if s is None:
        return out
    V, P = s["variants"], s["paired"]

    def rr(v):
        return V[v]["pooled"]["ring_recall"] if v in V else None

    def dr(v):
        return V[v]["pooled"]["deployed_recall"] if v in V else None

    b0, b1 = dr("B0"), dr("B1")
    if b0 is not None and b1 is not None:
        status = "confirmed" if b1 - b0 >= 0.2 else ("partly confirmed" if b1 > b0 else "refuted")
        out["E1"] = {"status": status, "detail": f"deployed transaction recall B1 {_p(b1)} vs B0 {_p(b0)}"}
    pr = P.get("G2+R_minus_B1+R")
    if pr:
        more = pr["ring_recall_diff_ci"][0] is not None and pr["ring_recall_diff_ci"][0] > 0
        earlier = pr["ttc_diff_min_ci"][1] is not None and pr["ttc_diff_min_ci"][1] < 0
        status = (
            "confirmed" if (more and earlier) else ("partly confirmed" if (more or earlier) else "refuted")
        )
        out["E2"] = {
            "status": status,
            "detail": f"ring recall G2+R {_p(rr('G2+R'))} vs B1+R {_p(rr('B1+R'))}, paired interval "
            f"{_pp(pr['ring_recall_diff_ci'][0])} to {_pp(pr['ring_recall_diff_ci'][1])}; median "
            f"time difference {_sm(pr['ttc_diff_min_median'])}, interval "
            f"{_sm(pr['ttc_diff_min_ci'][0])} to {_sm(pr['ttc_diff_min_ci'][1])}",
        }
    hardest = {}
    for v in ("B0", "B1+R", "G2+R"):
        t = V.get(v, {}).get("pooled", {}).get("recall_by_typology", {})
        if t:
            hardest[v] = min(t, key=lambda k: t[k]["recall"])
    if hardest:
        n_slow = sum(1 for h in hardest.values() if h == "slow_roll")
        status = "confirmed" if n_slow == len(hardest) else ("partly confirmed" if n_slow else "refuted")
        out["E3"] = {"status": status, "detail": f"hardest typology per variant: {hardest}"}
    ev = [p for p in RESULTS.glob("eval_world_*.json")]
    if ev:
        agg: dict = {}
        for p in ev:
            d = _load(p)
            for arch, x in d["archetype_fpr"].get("G2+R", {}).items():
                a = agg.setdefault(arch, [0, 0])
                a[0] += x["accounts_alerted"]
                a[1] += x["accounts_active"]
        rates = {k: v[0] / v[1] for k, v in agg.items() if v[1] >= 20}
        top2 = sorted(rates, key=lambda k: -rates[k])[:2]
        status = (
            "confirmed"
            if set(top2) == {"agent", "trader"}
            else ("partly confirmed" if {"agent", "trader"} & set(top2) else "refuted")
        )
        out["E4"] = {
            "status": status,
            "detail": "highest account-level false-alert shares (G2+R): "
            + ", ".join(f"{k} {_p(rates[k], 1)}" for k in top2),
        }
    ps = _load(RESULTS / "paysim_profile.json")
    if ps is None or ps.get("status") != "ok":
        out["E5"] = {
            "status": "not tested",
            "detail": "PaySim data not available; see results/paysim_profile.json",
        }
    else:
        bal = ps.get("balance_only_model", {}).get("test_roc_auc")
        out["E5"] = {"status": "see report", "detail": f"balance-only model test ROC-AUC {bal}"}
    if "B0+R" in V and "B0" in V:
        d = rr("B0+R") - rr("B0") if rr("B0+R") is not None and rr("B0") is not None else None
        gap = rr("G2+R") - rr("B0+R") if rr("G2+R") is not None and rr("B0+R") is not None else None
        status = "confirmed" if (d is not None and d < 0.05) else "refuted"
        out["E6"] = {
            "status": status,
            "detail": f"ring recall B0+R {_p(rr('B0+R'))} vs B0 {_p(rr('B0'))} (G2+R minus B0+R: {_pp(gap)})",
        }
    return out


# ---------------------------------------------------------------------------------------------- figures
def make_figures(resolver: Resolver) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    plt.rcParams.update({"figure.dpi": 150, "font.size": 9})
    ev = resolver.ns("eval") or {}
    worlds = sorted(k for k in ev if k.startswith("world_"))
    s = resolver.ns("summary")
    colors = {
        "B0": "#999999",
        "B0+R": "#bbbbbb",
        "B1": "#1f77b4",
        "B1+R": "#6baed6",
        "G2": "#d62728",
        "G2+R": "#ff7f0e",
        "RULE": "#2ca02c",
    }
    if worlds:
        e0 = ev[worlds[0]]
        if "curves" in e0:
            fig, ax = plt.subplots(1, 2, figsize=(9, 3.6))
            for v in ("B0", "B1", "B1+R", "G2", "G2+R"):
                c = e0["curves"].get(v)
                if not c:
                    continue
                ax[0].plot(c["recall"], c["precision"], label=v, color=colors[v], lw=1.2)
                ax[1].plot(np.maximum(c["fpr"], 1e-5), c["recall"], label=v, color=colors[v], lw=1.2)
            ax[0].set_xlabel("recall")
            ax[0].set_ylabel("precision")
            ax[0].set_title(f"Precision-recall, {worlds[0]} test (ChainWorld)")
            ax[1].set_xscale("log")
            ax[1].set_xlabel("false-alarm rate (log)")
            ax[1].set_ylabel("recall")
            ax[1].set_title("Recall vs false-alarm rate (ChainWorld)")
            ax[1].axvline(e0["meta"]["fpr_target"], color="k", ls=":", lw=0.8)
            ax[0].legend(fontsize=7)
            fig.tight_layout()
            fig.savefig(FIG_DIR / "pr_and_recall_vs_fpr.png")
            plt.close(fig)
            made.append("pr_and_recall_vs_fpr.png")
        fig, ax = plt.subplots(figsize=(5.5, 3.6))
        for v in ("B0", "B1+R", "G2+R"):
            ttc = sorted(u["ttc_min"] for w in worlds for u in ev[w]["units"][v]["rings"] if u["caught"])
            n = sum(len(ev[w]["units"][v]["rings"]) for w in worlds)
            if ttc and n:
                ax.step(ttc, np.arange(1, len(ttc) + 1) / n, where="post", label=f"{v}", color=colors[v])
        ax.set_xscale("log")
        ax.set_xlabel("minutes from the ring's first payment (log)")
        ax.set_ylabel("share of rings caught")
        ax.set_title("Time to catch (ECDF over all test rings), ChainWorld", fontsize=9)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "time_to_catch_ecdf.png")
        plt.close(fig)
        made.append("time_to_catch_ecdf.png")
        fig, ax = plt.subplots(figsize=(5.5, 3.4))
        bins = np.arange(-4.5, 8.5, 1.0)
        for v, off in (("B1+R", -0.2), ("G2+R", 0.2)):
            h = [
                u["hops_remaining"]
                for w in worlds
                for u in ev[w]["units"][v]["rings"]
                if u["hops_remaining"] is not None
            ]
            if h:
                cnt, edges = np.histogram(h, bins=bins)
                ax.bar((edges[:-1] + 0.5) + off, cnt, width=0.4, label=v, color=colors[v])
        ax.set_xlabel("hops remaining before the first cash-out at the moment of catch")
        ax.set_ylabel("rings")
        ax.set_title("Hops remaining at catch, ChainWorld", fontsize=9)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "hops_remaining.png")
        plt.close(fig)
        made.append("hops_remaining.png")
        peaks = [p for w in worlds for p in ev[w]["ring_level"].get("G2+R", {}).get("ring_peaks", [])]
        if peaks:
            days = sum(ev[w]["meta"]["test_days"] for w in worlds)
            th = np.linspace(0, 1, 101)
            false_pd = [sum(1 for pk, pu in peaks if pk >= t and pu < 0.5) / days for t in th]
            true_pd = [sum(1 for pk, pu in peaks if pk >= t and pu >= 0.5) / days for t in th]
            fig, ax = plt.subplots(figsize=(5.5, 3.4))
            ax.plot(th, false_pd, label="false rings per day", color="#d62728")
            ax.plot(th, true_pd, label="true rings per day", color="#2ca02c")
            ax.set_xlabel("ring-alert threshold (ring score)")
            ax.set_ylabel("rings per test day")
            ax.set_title("Ring alerts vs threshold, G2+R, ChainWorld test", fontsize=9)
            ax.legend(fontsize=7)
            fig.tight_layout()
            fig.savefig(FIG_DIR / "false_rings_vs_threshold.png")
            plt.close(fig)
            made.append("false_rings_vs_threshold.png")
        agg: dict = {}
        for w in worlds:
            for arch, x in ev[w]["archetype_fpr"].get("G2+R", {}).items():
                a = agg.setdefault(arch, [0, 0])
                a[0] += x["accounts_alerted"]
                a[1] += x["accounts_active"]
        if agg:
            items = sorted(((k, v[0] / v[1]) for k, v in agg.items() if v[1]), key=lambda kv: -kv[1])
            fig, ax = plt.subplots(figsize=(5.5, 3.4))
            ax.barh(
                [k.replace("_", " ") for k, _ in items][::-1],
                [v * 100 for _, v in items][::-1],
                color="#6baed6",
            )
            ax.set_xlabel("% of legitimate accounts with at least one verify/hold (test period)")
            ax.set_title("Who bears the false alarms (G2+R), ChainWorld", fontsize=9)
            fig.tight_layout()
            fig.savefig(FIG_DIR / "archetype_false_alerts.png")
            plt.close(fig)
            made.append("archetype_false_alerts.png")
    if s is not None:
        vs = [v for v in ("B0", "B1+R", "G2+R") if v in s["variants"]]
        typs = sorted({t for v in vs for t in s["variants"][v]["pooled"]["recall_by_typology"]})
        if typs:
            fig, ax = plt.subplots(figsize=(7, 3.6))
            w = 0.8 / len(vs)
            for i, v in enumerate(vs):
                d = s["variants"][v]["pooled"]["recall_by_typology"]
                vals = [d[t]["recall"] if t in d else np.nan for t in typs]
                lo = [d[t]["recall"] - (d[t]["ci"][0] or d[t]["recall"]) if t in d else 0 for t in typs]
                hi = [(d[t]["ci"][1] or d[t]["recall"]) - d[t]["recall"] if t in d else 0 for t in typs]
                ax.bar(
                    np.arange(len(typs)) + i * w,
                    vals,
                    width=w,
                    yerr=[lo, hi],
                    label=v,
                    color=colors[v],
                    capsize=2,
                )
            ax.set_xticks(np.arange(len(typs)) + w * (len(vs) - 1) / 2)
            ax.set_xticklabels([t.replace("_", " ") for t in typs], fontsize=8)
            ax.set_ylabel("ring recall")
            ax.set_ylim(0, 1.05)
            ax.set_title("Ring recall by typology (pooled, 95% bootstrap CI), ChainWorld", fontsize=9)
            ax.legend(fontsize=7)
            fig.tight_layout()
            fig.savefig(FIG_DIR / "ring_recall_by_typology.png")
            plt.close(fig)
            made.append("ring_recall_by_typology.png")
    rb = resolver.ns("robust")
    if rb is not None and rb.get("table"):
        rows = [t for t in rb["table"] if t["variant"] == "G2+R" and t["cell"] != "R6"]
        if rows:
            labels = [f"{t['cell']} {t['value']}" for t in rows]
            vals = np.array([[t["ring_recall"] or 0, t["deployed_recall"] or 0] for t in rows])
            fig, ax = plt.subplots(figsize=(4.5, 0.32 * len(rows) + 1.2))
            im = ax.imshow(vals, aspect="auto", cmap="viridis", vmin=0, vmax=1)
            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels(labels, fontsize=7)
            ax.set_xticks([0, 1])
            ax.set_xticklabels(["ring recall", "txn recall"], fontsize=8)
            for i in range(len(rows)):
                for j in range(2):
                    ax.text(j, i, f"{vals[i, j]:.2f}", ha="center", va="center", fontsize=7, color="w")
            ax.set_title("Robustness grid, G2+R, small ChainWorld worlds", fontsize=9)
            fig.colorbar(im, ax=ax, fraction=0.05)
            fig.tight_layout()
            fig.savefig(FIG_DIR / "robustness_heatmap.png")
            plt.close(fig)
            made.append("robustness_heatmap.png")
    b = resolver.ns("bench") or {}
    eng = [(h, d["engine"]) for h, d in sorted(b.items()) if "engine" in d]
    if eng:
        fig, ax = plt.subplots(figsize=(5.5, 3.4))
        for h, e in eng:
            hist = e["hist_total_us"]
            edges = hist["edges"] + [hist["edges"][-1] * 1.41]
            ax.step(edges, hist["counts"], where="post", label=h)
        ax.set_xscale("log")
        ax.set_xlabel("engine latency per event (µs, log)")
        ax.set_ylabel("events")
        ax.set_title("Engine latency distribution (ChainWorld slice)", fontsize=9)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "engine_latency.png")
        plt.close(fig)
        made.append("engine_latency.png")
    return made


def render_all(cfg: Config, figures: bool = True) -> list[str]:
    """Render every template. Returns a list of problems (empty on success); files are only written
    on success."""
    exp = resolve_expectations()
    if exp:
        (RESULTS / "expectations.json").write_text(json.dumps(exp, indent=1), encoding="utf-8")
    resolver = Resolver(cfg)
    if figures:
        try:
            made = make_figures(resolver)
            log.info("figures: %s", ", ".join(made))
        except Exception as e:  # figures never hide a rendering problem but must not block text
            log.warning("figures failed: %s", e)
    problems: list[str] = []
    for src, dst in TEMPLATES:
        if not src.exists():
            problems.append(f"missing template {src.relative_to(ROOT)}")
            continue
        text, probs = render_text(src.read_text(encoding="utf-8"), resolver)
        if probs:
            problems.extend(f"{src.relative_to(ROOT)}: {p}" for p in probs)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            header = (
                f"<!-- Generated by `chainwatch report` from {dst.name} template. "
                f"Edit the template, not this file. -->\n"
            )
            dst.write_text(header + text, encoding="utf-8")
    return problems
