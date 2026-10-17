#!/usr/bin/env bash
# Run the API natively (no Docker): create .venv-serve with the pure-Python serving requirements and start uvicorn.
# Offline machines: build a wheelhouse on the laptop (make wheelhouse-s390x), copy it, then
#   WHEELHOUSE=wheelhouse-s390x bash scripts/run_native.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
VENV="${VENV:-.venv-serve}"
PORT="${PORT:-8000}"
if [ ! -d "$VENV" ]; then
  echo "creating $VENV with $($PY --version)"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
if [ -n "${WHEELHOUSE:-}" ]; then
  pip install --no-index --find-links "$WHEELHOUSE" -r requirements-serve.txt
else
  pip install --only-binary=:all: -r requirements-serve.txt
fi
export PYTHONHASHSEED=0
echo "starting ChainWatch on 0.0.0.0:$PORT (dashboard at http://localhost:$PORT)"
exec python -m chainwatch serve --host 0.0.0.0 --port "$PORT" "$@"
