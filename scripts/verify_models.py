"""Recompute model-package file hashes and registry hashes (``make verify-models``). Stdlib only.

Usage: python scripts/verify_models.py [models/default]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chainwatch.config import load, sha256_file  # noqa: E402
from chainwatch.features.registry import registry_hash_for_cfg  # noqa: E402

REQUIRED = ("B0.json", "B1.json", "G2.json", "thresholds.json", "benign_profile.json", "manifest.json")


def verify(model_dir: str | Path, cfg=None) -> list[str]:
    model_dir = Path(model_dir)
    cfg = cfg or load()
    problems = []
    for name in REQUIRED:
        if not (model_dir / name).exists():
            problems.append(f"missing {name}")
    if problems:
        return problems
    man = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in man.get("files", {}).items():
        p = model_dir / name
        if not p.exists():
            problems.append(f"manifest lists {name} but it is missing")
        elif sha256_file(p) != digest:
            problems.append(f"hash mismatch for {name}")
    for name in ("B0.json", "B1.json", "G2.json", "thresholds.json", "benign_profile.json"):
        if name not in man.get("files", {}):
            problems.append(f"manifest does not list {name}")
    for det in ("B0", "B1", "G2"):
        model = json.loads((model_dir / f"{det}.json").read_text(encoding="utf-8"))
        expected = registry_hash_for_cfg(cfg, det)
        if model.get("registry_hash") != expected:
            problems.append(
                f"{det}: registry hash differs from the current config (retrain or use the training config)"
            )
        if man.get("registry_hash", {}).get(det) != model.get("registry_hash"):
            problems.append(f"{det}: manifest registry hash differs from the model file")
    thr = json.loads((model_dir / "thresholds.json").read_text(encoding="utf-8"))
    if thr.get("created_from", {}).get("split") != "validation":
        problems.append("thresholds.json was not fitted on the validation split")
    for v in ("B0", "B1", "G2", "B0+R", "B1+R", "G2+R"):
        if v not in thr.get("variants", {}):
            problems.append(f"thresholds.json lacks variant {v}")
    return problems


def main(argv: list[str]) -> int:
    model_dir = argv[0] if argv else "models/default"
    problems = verify(model_dir)
    if problems:
        print("verify-models FAILED:\n  - " + "\n  - ".join(problems))
        return 1
    print(f"verify-models OK: {model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
