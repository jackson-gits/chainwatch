"""Command line: ``python -m chainwatch <command>`` (spec 18.2).

Only ``argparse`` and stdlib at import time; heavy modules (numpy, LightGBM) are imported inside the commands
that need them, so ``serve`` and ``parity`` run in the pure-Python serving environment.
Exit codes: 0 success, 1 failure, 2 usage error, 3 refused.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
log = logging.getLogger("chainwatch")


class Refused(RuntimeError):
    """An operation was refused on purpose (exit code 3)."""


def _cfg(args, preset: str | None = None):
    from chainwatch import config

    return config.load(args.config, preset=preset, overrides=args.set or None)


def _processed() -> Path:
    from chainwatch.config import ROOT

    return ROOT / "data" / "processed"


# ---------------------------------------------------------------------------------------------- data
def cmd_world(args) -> int:
    from chainwatch.data.world.generate import generate_world, world_tag

    preset = "small" if args.small else args.preset
    cfg = _cfg(args, preset)
    if args.days:
        cfg = cfg.with_overrides({"world.days": args.days})
    seeds = args.seed if args.seed else [0]
    for s in seeds:
        out = Path(args.out) if args.out else _processed() / world_tag(preset, s)
        log.info("generating %s (preset %s, seed %d)", out, preset, s)
        rep = generate_world(cfg, s, out)
        print(
            json.dumps(
                {
                    k: rep[k]
                    for k in (
                        "world",
                        "n_events",
                        "fraud_events",
                        "fraud_share",
                        "fraud_share_in_band",
                        "rings_written",
                    )
                }
            )
        )
    return EXIT_OK


def cmd_world_demo(args) -> int:
    from chainwatch.data.world.generate import generate_demo

    cfg = _cfg(args, "small")
    rep = generate_demo(cfg)
    print(json.dumps({k: rep[k] for k in ("n_events", "fraud_events", "rings_written", "rings_by_typology")}))
    return EXIT_OK


def cmd_world_freeze(args) -> int:
    from chainwatch.eval.protocol import freeze_worlds

    man = freeze_worlds(args.worlds or None)
    print(json.dumps({"worlds": sorted(man["worlds"])}, indent=1))
    return EXIT_OK


def cmd_paysim_convert(args) -> int:
    from chainwatch.data.paysim import convert

    cfg = _cfg(args)
    meta = convert(cfg, args.src, args.out)
    print(json.dumps(meta, indent=1))
    return EXIT_OK


def cmd_features(args) -> int:
    from chainwatch.features.matrix import build_matrices

    overrides = list(args.set or [])
    if args.priority:
        overrides.append(f'features.priority="{args.priority}"')
    from chainwatch import config

    cfg = config.load(args.config, overrides=overrides or None)
    meta = build_matrices(cfg, args.world, args.out, limit=args.limit)
    print(json.dumps({k: meta[k] for k in ("world", "rows", "positives", "replay_seconds", "us_per_event")}))
    return EXIT_OK


def cmd_train(args) -> int:
    from chainwatch.model.train import train_detector, tune_grid

    cfg = _cfg(args)
    dets = [d.strip() for d in args.detectors.split(",") if d.strip()]
    if args.grid:
        res = tune_grid(cfg, args.features, args.out, dets, seed=args.seed)
        print(json.dumps(res, indent=1))
        return EXIT_OK
    for d in dets:
        rep = train_detector(cfg, args.features, d, args.out, seed=args.seed)
        print(
            json.dumps(
                {
                    k: rep[k]
                    for k in (
                        "detector",
                        "best_iteration",
                        "shipped_trees",
                        "pr_auc_val_shipped",
                        "parity_max_abs_diff",
                    )
                }
            )
        )
    return EXIT_OK


def cmd_thresholds(args) -> int:
    from chainwatch.model.thresholds import compute_thresholds

    cfg = _cfg(args)
    out = compute_thresholds(cfg, args.features, args.models)
    print(
        json.dumps(
            {
                k: {kk: v[kk] for kk in ("verify", "hold", "edge", "ring_alert")}
                for k, v in out["variants"].items()
            },
            indent=1,
        )
    )
    return EXIT_OK


def cmd_models(args) -> int:
    """features + train + thresholds + verify for one world (``make models``)."""
    from chainwatch.data.world.generate import world_tag
    from chainwatch.features.matrix import build_matrices
    from chainwatch.model.thresholds import compute_thresholds
    from chainwatch.model.train import train_detector

    preset = "small" if args.small else args.preset
    cfg = _cfg(args, preset)
    world = Path(args.world) if args.world else _processed() / world_tag(preset, args.world_seed)
    feat = world.parent / f"features_{world.name}"
    if not (feat / "meta.json").exists() or args.rebuild_features:
        build_matrices(cfg, world, feat)
    for d in ("B0", "B1", "G2"):
        train_detector(cfg, feat, d, args.out, seed=args.seed)
    compute_thresholds(cfg, feat, args.out)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from verify_models import verify

    problems = verify(args.out, cfg)
    if problems:
        print("\n".join(problems))
        return EXIT_FAIL
    print(f"model package written to {args.out}")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------- evaluation
def cmd_calibrate(args) -> int:
    from chainwatch.eval.protocol import calibrate_world

    preset = "small" if args.small else args.preset
    cfg = _cfg(args, preset)
    res = calibrate_world(cfg, preset, args.seed or 0, args.tag)
    print(json.dumps(res, indent=1))
    return EXIT_OK


def cmd_evaluate(args) -> int:
    from chainwatch.eval.protocol import TestTouchRefused, evaluate

    cfg = _cfg(args)
    try:
        res = evaluate(cfg, args.features, args.models, allow_rerun=args.allow_rerun)
    except TestTouchRefused as e:
        raise Refused(str(e)) from None
    print(json.dumps(res["headline"], indent=1))
    return EXIT_OK


def cmd_eval_all(args) -> int:
    from chainwatch.eval.protocol import eval_all

    preset = "small" if args.small else args.preset
    cfg = _cfg(args, preset)
    seeds = args.seeds or list(cfg.eval.seeds)
    if args.small:
        seeds = seeds[:2]
    summary = eval_all(
        cfg, preset, seeds, skip_existing=args.skip_existing, jobs=args.jobs, allow_rerun=args.allow_rerun
    )
    print(json.dumps(summary.get("claim", {}), indent=1))
    return EXIT_OK


def cmd_ablate(args) -> int:
    from chainwatch.eval.ablation import run_ablations

    preset = "small" if args.small else args.preset
    cfg = _cfg(args, preset)
    ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    res = run_ablations(cfg, preset, args.seeds or list(cfg.eval.seeds), ids, jobs=args.jobs)
    print(json.dumps(res.get("summary", {}), indent=1)[:4000])
    return EXIT_OK


def cmd_robustness(args) -> int:
    from chainwatch.eval.robustness import run_robustness

    cfg = _cfg(args)
    res = run_robustness(cfg, args.models, seeds=args.seeds, jobs=args.jobs, cells=args.cells)
    print(json.dumps(res.get("table", []), indent=1)[:4000])
    return EXIT_OK


def cmd_leakage(args) -> int:
    from chainwatch.eval.leakage import run_leakage

    cfg = _cfg(args)
    res = run_leakage(cfg, args.features, args.models)
    print(json.dumps({k: v.get("pass") for k, v in res["checks"].items()}, indent=1))
    return EXIT_OK if res["blocking_ok"] else EXIT_FAIL


def cmd_parity(args) -> int:
    from chainwatch.eval.parity import compare_parity, write_parity

    if args.compare:
        rep = compare_parity(args.compare[0], args.compare[1])
        print(json.dumps(rep, indent=1))
        return EXIT_OK if rep["decision_mismatches"] == 0 else EXIT_FAIL
    cfg = _cfg(args)
    out = args.out or "results/parity_local.jsonl"
    rep = write_parity(cfg, out, model_dir=args.model_dir, n_events=args.events)
    print(json.dumps(rep, indent=1))
    return EXIT_OK


def cmd_report(args) -> int:
    from chainwatch.eval.report import render_all

    cfg = _cfg(args)
    problems = render_all(cfg, figures=not args.no_figures)
    if problems:
        print("report problems:\n  - " + "\n  - ".join(problems))
        return EXIT_FAIL
    print("rendered README.md, reports/REPORT.md, docs/demo_script.md, docs/deck_outline.md")
    return EXIT_OK


def cmd_check_claims(args) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from check_claims import main as check_main

    return check_main([])


# ---------------------------------------------------------------------------------------------- serving
def cmd_serve(args) -> int:
    import uvicorn

    from chainwatch.serving.app import create_app

    cfg = _cfg(args)
    host = args.host or cfg.serving.host
    port = int(args.port or cfg.serving.port)
    app = create_app(
        cfg=cfg, model_dir=args.model_dir, replay=not args.no_replay, compare=True if args.compare else None
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        loop="asyncio",
        http="h11",
        ws="websockets",
        workers=1,
        log_level=args.log_level.lower(),
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chainwatch", description="ChainWatch: mule-ring detection on synthetic data"
    )
    p.add_argument("--config", default=None, help="config TOML (default configs/default.toml)")
    p.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE", help="override a config value")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--small", action="store_true", help="shorthand for preset small and two seeds")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("world", help="generate ChainWorld worlds")
    s.add_argument("--preset", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--seed", type=int, nargs="*")
    s.add_argument("--days", type=int)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_world)

    s = sub.add_parser("world-demo", help="generate the demo replay world into data/replay")
    s.set_defaults(fn=cmd_world_demo)

    s = sub.add_parser("world-freeze", help="write results/world_manifest.json")
    s.add_argument("--worlds", nargs="*")
    s.set_defaults(fn=cmd_world_freeze)

    s = sub.add_parser("paysim-convert", help="convert the Kaggle PaySim CSV to the canonical stream")
    s.add_argument("--src", default="data/raw/paysim")
    s.add_argument("--out", default="data/processed/paysim")
    s.set_defaults(fn=cmd_paysim_convert)

    s = sub.add_parser("features", help="replay a world through the feature engine and write matrices")
    s.add_argument("--world", required=True)
    s.add_argument("--out")
    s.add_argument("--limit", type=int)
    s.add_argument("--priority", choices=["P0", "P1"])
    s.set_defaults(fn=cmd_features)

    s = sub.add_parser("train", help="train B0, B1, G2 and export portable JSON")
    s.add_argument("--features", required=True)
    s.add_argument("--out", default="models/default")
    s.add_argument("--detectors", default="B0,B1,G2")
    s.add_argument("--seed", type=int)
    s.add_argument("--grid", action="store_true")
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("thresholds", help="validation thresholds, benign profile, manifest")
    s.add_argument("--features", required=True)
    s.add_argument("--models", default="models/default")
    s.set_defaults(fn=cmd_thresholds)

    s = sub.add_parser("models", help="features + train + thresholds + verify for one world")
    s.add_argument("--preset", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--world")
    s.add_argument("--world-seed", type=int, default=0)
    s.add_argument("--out", default="models/default")
    s.add_argument("--seed", type=int)
    s.add_argument("--rebuild-features", action="store_true")
    s.set_defaults(fn=cmd_models)

    s = sub.add_parser("calibrate", help="world difficulty check with B0 and B1 only (never G2)")
    s.add_argument("--preset", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--seed", type=int)
    s.add_argument("--tag", default="calibration")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("evaluate", help="score the test split once and write results/eval_<world>.json")
    s.add_argument("--features", required=True)
    s.add_argument("--models", required=True)
    s.add_argument("--allow-rerun", action="store_true")
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser(
        "eval-all", help="per seed: world, features, train, thresholds, evaluate; then summary"
    )
    s.add_argument("--preset", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--seeds", type=int, nargs="*")
    s.add_argument("--skip-existing", action="store_true")
    s.add_argument("--allow-rerun", action="store_true")
    s.add_argument("--jobs", type=int, default=1)
    s.set_defaults(fn=cmd_eval_all)

    s = sub.add_parser("ablate", help="ablations A1 to A10")
    s.add_argument("--preset", default="medium", choices=["small", "medium", "large"])
    s.add_argument("--seeds", type=int, nargs="*")
    s.add_argument("--ids")
    s.add_argument("--jobs", type=int, default=1)
    s.set_defaults(fn=cmd_ablate)

    s = sub.add_parser("robustness", help="robustness grid R1 to R6")
    s.add_argument("--models", default="models/eval/medium_seed0")
    s.add_argument("--seeds", type=int, nargs="*")
    s.add_argument("--cells")
    s.add_argument("--jobs", type=int, default=1)
    s.set_defaults(fn=cmd_robustness)

    s = sub.add_parser("leakage", help="leakage checks L1 to L9")
    s.add_argument("--features", required=True)
    s.add_argument("--models", default=None)
    s.set_defaults(fn=cmd_leakage)

    s = sub.add_parser("parity", help="cross-platform decision parity")
    s.add_argument("--out")
    s.add_argument("--compare", nargs=2, metavar=("A", "B"))
    s.add_argument("--model-dir")
    s.add_argument("--events", type=int, default=20000)
    s.set_defaults(fn=cmd_parity)

    s = sub.add_parser("report", help="render README, report, demo script and deck outline from results")
    s.add_argument("--no-figures", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("check-claims", help="fail on numbers without a results source or forbidden phrases")
    s.set_defaults(fn=cmd_check_claims)

    s = sub.add_parser("serve", help="run the API and dashboard (uvicorn, one worker)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--model-dir")
    s.add_argument("--no-replay", action="store_true")
    s.add_argument("--compare", action="store_true")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code not in (0, None) else EXIT_OK
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.fn(args) or 0)
    except Refused as e:
        log.error("refused: %s", e)
        return EXIT_REFUSED
    except Exception as e:
        from chainwatch.serving.engine import RegistryMismatch

        if isinstance(e, RegistryMismatch):
            log.error("refused: %s", e)
            return EXIT_REFUSED
        log.exception("failed: %s", e)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
