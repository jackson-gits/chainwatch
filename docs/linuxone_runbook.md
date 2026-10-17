# LinuxONE runbook

Spec §16.3. Commands in the order they run on the instance; real outputs are pasted in as they are
executed. **Status: the instance is not available yet (UNVERIFIED access); everything below the
"Laptop rehearsals" section is pending real access. Do not treat the QEMU numbers as performance
(§16.5): emulated timings are meaningless and are only used to prove byte-order safety and that the
dependencies install.**

## Laptop rehearsals (done, 2026-10-18 IST)

```
$ docker run --privileged --rm tonistiigi/binfmt --install s390x
  ... "qemu-s390x", "riscv64", "s390x" registered

$ docker run --rm --platform linux/s390x python:3.12-slim python -c \
    "import platform,sys;print(platform.machine(), sys.byteorder)"
  s390x big
```

So `python:3.12-slim` **does** have an s390x variant (this resolves the `[UNVERIFIED]` in CLAUDE.md
§3.2; `Dockerfile.ubuntu` stays as the fallback but is not needed so far).

Serve-dependency wheels for s390x (2026-10-18, verified on the laptop):

```
$ pip download --only-binary=:all: --no-deps -d wheelhouse-s390x \
    --platform manylinux2014_s390x --platform manylinux_2_17_s390x --platform manylinux_2_28_s390x \
    --python-version 312 --implementation cp --abi cp312 -r requirements-serve.lock
  15 packages downloaded, no source builds: fastapi, starlette, pydantic, pydantic_core, uvicorn,
  websockets, anyio, h11, idna, click, typing_extensions, typing-inspection, annotated-types,
  annotated-doc, opentelemetry-api
```

So the pinned lock file installs on s390x without compiling anything (the `wheelhouse-s390x/`
directory in the repo root is the copied-over artefact; it is git-ignored and regenerable).

The cross-build `docker buildx build --platform linux/s390x -t chainwatch:s390x --load .` was run
from the laptop; its log is `logs/docker_s390x_build.log` and the image built cleanly
(manifest `df38a4ede31`, 51.5 MB, `python:3.12-slim` base). The QEMU parity run and `/v1/info`
check are recorded at the bottom of this file once the image build completes.

## Laptop QEMU parity (emulated s390x vs laptop)

Emulated `s390x` (big-endian) inside Docker Desktop (QEMU):

```json
{
  "out": "/app/results/parity_qemu-s390x.jsonl",
  "lines": 20000,
  "warmup_events": 26658,
  "machine": "s390x",
  "byteorder": "big",
  "seconds": 628.6
}
```

Laptop (x86_64, little-endian):

```json
{
  "out": "results/parity_laptop.jsonl",
  "lines": 20000,
  "warmup_events": 26658,
  "machine": "AMD64",
  "byteorder": "little",
  "seconds": 70.8
}
```

`chainwatch parity --compare`:

```json
{
  "decision_mismatches": 0,
  "ring_id_mismatches": 0,
  "max_abs_score_diff": 6.94e-18,
  "identical_scores": false,
  "first_difference": {
    "line": 525,
    "event_id": 27183,
    "a": { "decision": "allow", "score": "7.014785735734204e-06", "base_score": "7.014785735734204e-06", "ring_id": null },
    "b": { "decision": "allow", "score": "7.014785735734203e-06", "base_score": "7.014785735734203e-06", "ring_id": null }
  },
  "length_a": 20000,
  "length_b": 20000
}
```

Zero decision and ring-ID mismatches. The tiny floating-point difference (~6.9e-18) is at the
machine epsilon level (a harmless representation difference) — no behavioural divergence. This
proves byte-order safety and the pure-Python `PortableGBDT` is portable across little-endian and
big-endian architectures (consistent with §10.3 / the parity rule). Timings are emulated and
cannot be used for performance.

## On the instance (pending access)

1. **Connect and look around**

   ```bash
   bash scripts/check_platform.sh          # probe script (§16.4)
   python3 scripts/check_platform.py       # structured verdict -> results/platform_check_linuxone.json
   ```

   Expect: `uname -m` = `s390x`; record `lscpu`, `/etc/os-release`, `python3 --version`,
   `docker --version`, `docker info`, `free -m`, `df -h`, `ss -ltn`, and whether PyPI and GitHub
   answer (5 s timeout). Paste the full output here.

2. **Get the code onto the machine.** If GitHub answers: `git clone
   https://github.com/jackson-gits/upi-mule-ring-detection.git`. Otherwise `make bundle` on the
   laptop and `scp dist/chainwatch-bundle.tar.gz` over. Never copy raw datasets.

3. **Run path.**

   - Docker present or installable: `docker build -t chainwatch:latest .`; if there is no outbound
     access, build the s390x image on the laptop, `docker save chainwatch:s390x | gzip >
     chainwatch-s390x.tar.gz`, `scp`, `docker load`, then `docker run -d --name chainwatch -p
     8000:8000 --restart unless-stopped chainwatch:s390x`.
   - No Docker: `bash scripts/run_native.sh` (needs `python3-venv`; if pip is offline use the
     wheelhouse from `make wheelhouse-s390x` on the laptop with `pip install --no-index
     --find-links wheelhouse-s390x -r requirements-serve.txt`).

4. **Alive check**

   ```bash
   curl -s localhost:8000/healthz
   curl -s localhost:8000/v1/info      # must show "machine": "s390x", "byteorder": "big"
   ```

5. **Dashboard.** Preferred: SSH tunnel from the laptop
   (`ssh -L 8000:localhost:8000 <user>@<host>`, open http://localhost:8000). Alternative: open port
   8000 in the security group and use the floating IP, but set `CHAINWATCH_API_KEY` first.

6. **Parity, then benchmarks, then copy results back**

   ```bash
   python3 -m chainwatch parity --out results/parity_linuxone.jsonl
   python3 bench/platform_info.py --host-tag linuxone
   python3 bench/engine_bench.py --host-tag linuxone
   python3 bench/load_test.py --host-tag linuxone      # needs httpx (pure Python)
   ```

   `scp` the `results/*linuxone*` files to the laptop, compare with
   `python -m chainwatch parity --compare results/parity_laptop.jsonl
   results/parity_linuxone.jsonl`, commit both.

7. **Record** exact commands, versions, outputs and any deviation in this file and in
   `docs/STATUS.md`; write the image digest and git commit there at submission time.

## QEMU parity run (laptop, emulated s390x — proof of byte order, NOT performance)

The local image is `chainwatch:s390x` (built for `linux/s390x`). It starts under Docker Desktop/QEMU
and Python reports `s390x`, big-endian. Structured host metadata is in
`results/platform_qemu-s390x.json` (captured 2026-10-18 IST). This is an emulation record; it does
not establish the CPU, byte order reported by host utilities, or performance of a physical LinuxONE.
In this Docker Desktop environment `lscpu` exposes host CPU details, so use Python's `platform.machine()`
and `sys.byteorder` only as the guest-runtime check.

## Laptop benchmark baseline (Windows 11 x86_64, 2026-10-18 IST)

Recorded using the medium ChainWorld seed-0 slice, with 98,466 warm events. Full JSON artifacts are
`results/platform_laptop.json`, `results/bench_engine_laptop.json`, and `results/bench_load_laptop.json`.

- Engine (`G2+R`): 10,000 measured events, one repetition, 1,000 in-sample warmup requests;
  total scorer latency p50 336 µs, p95 1,004 µs, p99 2,016 µs; throughput about 1,399 events/s.
- HTTP (`uvicorn`, one worker, localhost, same machine for client and server): 1,000 requests at
  each concurrency level. At concurrency 1, p50/p95 were 7.3/15.4 ms and throughput was 119 req/s.
  At concurrency 8, p50/p95 were 57.9/148.0 ms and throughput was 99 req/s. Both levels had zero
  errors. These are small-sample laptop baselines, not LinuxONE or production capacity claims.

The HTTP benchmark used `--spawn`, which starts the service with `max_lateness_s=3600` to tolerate
concurrent out-of-order arrivals. Its 6,000 requests include warmup and batch requests; see the JSON
for the exact load shape. The engine run used one repetition because a three-repetition 80,000-event
run was stopped before completion on this host.

Physical LinuxONE deployment, its platform record, and engine/HTTP measurements remain pending
instance access. Use the commands in "On the instance" above once access is available.
