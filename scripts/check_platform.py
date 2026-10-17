"""Platform check (spec 16.4). Stdlib only. Prints OK/WARN/FAIL per check, writes
results/platform_check_<host_tag>.json and ends with a NEXT STEP line.

Usage: python3 scripts/check_platform.py [--host-tag linuxone]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-tag", default=platform.machine().lower())
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})
        print(f"{status:<4}  {name}: {detail}")

    add(
        "machine",
        "OK",
        f"{platform.machine()}, {sys.byteorder}-endian, {platform.system()} {platform.release()}",
    )
    v = sys.version_info
    add(
        "python",
        "OK" if v >= (3, 10) else "WARN",
        platform.python_version() + ("" if v >= (3, 10) else " (need 3.10 or newer)"),
    )
    add(
        "pip",
        "OK" if run([sys.executable, "-m", "pip", "--version"])[0] == 0 else "FAIL",
        run([sys.executable, "-m", "pip", "--version"])[1][:80],
    )
    missing = []
    versions = {}
    for mod in ("fastapi", "pydantic", "pydantic_core", "uvicorn", "websockets"):
        try:
            m = importlib.import_module(mod)
            versions[mod] = getattr(m, "__version__", "?")
        except Exception:
            missing.append(mod)
    if v < (3, 11):
        try:
            importlib.import_module("tomli")
        except Exception:
            missing.append("tomli")
    add(
        "serve packages",
        "OK" if not missing else "WARN",
        (", ".join(f"{k} {x}" for k, x in versions.items()) or "none")
        + (f"; missing: {missing}" if missing else ""),
    )
    docker = shutil.which("docker")
    docker_ok = False
    if docker:
        rc, out = run(["docker", "info", "--format", "{{.ServerVersion}} {{.Architecture}}"])
        docker_ok = rc == 0
        add(
            "docker",
            "OK" if docker_ok else "WARN",
            out[:120] if out else "daemon not reachable or no permission",
        )
    else:
        add("docker", "WARN", "not installed")
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", args.port))
        add(f"port {args.port}", "OK", "free")
        port_free = True
    except OSError as e:
        add(f"port {args.port}", "WARN", f"in use or not allowed ({e})")
        port_free = False
    finally:
        s.close()
    mem = None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            mem = fh.readline().split()[1] + " kB"
    except OSError:
        pass
    add("cpu/memory", "OK", f"{os.cpu_count()} CPUs, MemTotal {mem or 'n/a'}")
    du = shutil.disk_usage(ROOT)
    add("disk", "OK" if du.free > 2e9 else "WARN", f"{du.free / 1e9:.1f} GB free")
    online = {}
    for url in ("https://pypi.org", "https://github.com"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5) as r:
                online[url] = r.status
        except Exception as e:
            online[url] = f"unreachable ({type(e).__name__})"
    reach = all(isinstance(x, int) for x in online.values())
    add("outbound https", "OK" if reach else "WARN", json.dumps(online))
    if reach:
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--only-binary=:all:",
                    "--no-deps",
                    "-d",
                    tmp,
                    "pydantic-core",
                ],
                timeout=120,
            )
            add(
                "wheel availability (pydantic-core)",
                "OK" if rc == 0 else "FAIL",
                out.splitlines()[-1][:120] if out else "",
            )
    else:
        add("wheel availability", "WARN", "skipped (offline)")
    skew = abs(
        time.time() - time.mktime(time.gmtime()) - (time.timezone if time.daylight == 0 else time.altzone)
    )
    add(
        "clock",
        "OK" if skew < 5 else "WARN",
        _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )
    if docker_ok:
        nxt = "Docker works: run `make docker-build docker-run` (or `docker build -t chainwatch:latest .`)."
    elif not reach:
        nxt = (
            "No outbound access: build the wheelhouse on the laptop (`make wheelhouse-s390x`), copy it, then "
            "`WHEELHOUSE=wheelhouse-s390x bash scripts/run_native.sh`."
        )
    else:
        nxt = "No Docker: run `bash scripts/run_native.sh`."
    if not port_free:
        nxt += f" Port {args.port} is busy: set PORT=8080."
    print("NEXT STEP: " + nxt)
    out = ROOT / "results" / f"platform_check_{args.host_tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "host_tag": args.host_tag,
                "checks": checks,
                "next_step": nxt,
                "utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
