#!/usr/bin/env bash
#
# ChainWatch installer & native deployment script
# Real-Time Mule-Ring Detection for UPI-Style Payment Streams
#
set -euo pipefail

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
MODEL_DIR="${MODEL_DIR:-models/default}"
VENV="${VENV:-.venv-serve}"
PYTHON="${PYTHON:-python3}"

say "Checking environment for ChainWatch deployment..."
command -v "$PYTHON" >/dev/null 2>&1 || die "Python 3 is required. Install python3 and retry."

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
say "Detected Python $PY_VER"

if [ ! -d "$MODEL_DIR" ]; then
  if [ -d "models/small_seed0" ]; then
    MODEL_DIR="models/small_seed0"
    say "Using fallback model directory: $MODEL_DIR"
  fi
fi

say "Setting up serving environment in $VENV..."
if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
. "$VENV/bin/activate"

say "Installing serving dependencies (pure-Python, s390x-safe)..."
if [ -n "${WHEELHOUSE:-}" ] && [ -d "${WHEELHOUSE:-}" ]; then
  pip install --no-index --find-links "$WHEELHOUSE" -r requirements-serve.txt
else
  pip install --only-binary=:all: -r requirements-serve.txt
fi

export PYTHONHASHSEED=0
say "ChainWatch is ready to serve."
say "Dashboard: http://localhost:$PORT"
say "Health check: http://localhost:$PORT/health"
say "API documentation: http://localhost:$PORT/v1/info"

exec python -m chainwatch serve --host "$HOST" --port "$PORT" --model-dir "$MODEL_DIR" "$@"
