"""World difficulty calibration grid (spec 8.2.5): B0 and B1 ONLY, on a calibration seed that is never
used for evaluation. Only noise_level, hard_negative_boost and the mule-profile mix are varied.

Usage: python scripts/calibrate_grid.py [--preset medium] [--seed 1000] [--jobs 3]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GRID = [
    {"name": "default", "world.noise_level": 1.0, "world.hard_negative_boost": 1.0},
    {"name": "n1.5_h2", "world.noise_level": 1.5, "world.hard_negative_boost": 2.0},
    {
        "name": "n1.5_h2_active",
        "world.noise_level": 1.5,
        "world.hard_negative_boost": 2.0,
        "world.mule_mix": {"new": 0.25, "dormant": 0.30, "active": 0.45},
    },
    {
        "name": "n2_h3_active",
        "world.noise_level": 2.0,
        "world.hard_negative_boost": 3.0,
        "world.mule_mix": {"new": 0.20, "dormant": 0.30, "active": 0.50},
    },
]


def run_one(setting: dict, preset: str, seed: int) -> dict:
    from chainwatch import config
    from chainwatch.eval.protocol import calibrate_world

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{setting['name']}] %(message)s")
    overrides = {k: v for k, v in setting.items() if k != "name"}
    cfg = config.load(preset=preset, overrides=overrides)
    res = calibrate_world(cfg, preset, seed, tag=f"{preset}_{setting['name']}")
    res["setting"] = setting
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument(
        "--grid",
        help="JSON file with a list of settings (same shape as GRID); defaults to the built-in grid",
    )
    ap.add_argument("--tag", help="results/calibration_grid_<preset>_<tag>.json")
    args = ap.parse_args()
    grid = GRID if not args.grid else json.loads(Path(args.grid).read_text(encoding="utf-8"))
    grid = [g for g in grid if not args.only or g["name"] in args.only]
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(run_one, grid, [args.preset] * len(grid), [args.seed] * len(grid)))
    out = ROOT / "results" / f"calibration_grid_{args.preset}{'_' + args.tag if args.tag else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"seed": args.seed, "note": "B0 and B1 only; G2 never trained here", "runs": results}, indent=1
        ),
        encoding="utf-8",
    )
    for r in results:
        print(
            r["setting"]["name"],
            {
                d: {k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}
                for d, m in r["detectors"].items()
            },
            "share",
            round(r["fraud_share"], 5),
            "n",
            r["n_events"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
