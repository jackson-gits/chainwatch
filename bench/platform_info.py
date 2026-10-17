"""Write results/platform_<host_tag>.json (spec 15.1). Stdlib only; never stores host names, user names
or IPs.

Usage: python bench/platform_info.py --host-tag laptop
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip() or None
    except Exception:
        return None


def collect(host_tag: str) -> dict:
    meminfo = None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            meminfo = fh.readline().strip()
    except OSError:
        pass
    pkgs = None
    out = _run([sys.executable, "-m", "pip", "list", "--format=json"])
    if out:
        try:
            wanted = {
                "fastapi",
                "pydantic",
                "pydantic-core",
                "pydantic_core",
                "uvicorn",
                "websockets",
                "h11",
                "anyio",
                "starlette",
                "httpx",
                "tomli",
            }
            pkgs = {p["name"]: p["version"] for p in json.loads(out) if p["name"].lower() in wanted}
        except ValueError:
            pkgs = None
    try:
        from chainwatch.config import load

        cfg_hash = load().config_hash
    except Exception:
        cfg_hash = None
    return {
        "host_tag": host_tag,
        "machine": platform.machine(),
        "system": platform.system(),
        "release": platform.release(),
        "byteorder": sys.byteorder,
        "python": sys.version,
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "cpu_count": os.cpu_count(),
        "lscpu": _run(["lscpu"]) if shutil.which("lscpu") else None,
        "meminfo_total": meminfo,
        "in_container": os.path.exists("/.dockerenv"),
        "docker": _run(["docker", "--version"]) if shutil.which("docker") else None,
        "serving_packages": pkgs,
        "git_commit": _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "config_hash": cfg_hash,
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "utc": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-tag", required=True)
    args = ap.parse_args()
    info = collect(args.host_tag)
    out = ROOT / "results" / f"platform_{args.host_tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                k: info[k]
                for k in ("host_tag", "machine", "byteorder", "python_version", "cpu_count", "in_container")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
