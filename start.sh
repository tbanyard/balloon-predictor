#!/usr/bin/env bash
set -euo pipefail
predictor_project="$(cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd -- "$predictor_project"

# Reuse an explicitly activated environment without changing it silently.
if [[ -n "${CONDA_PREFIX:-}" || -n "${VIRTUAL_ENV:-}" ]]; then
  predictor_python="$(command -v python)"
  if ! "$predictor_python" -c 'import sys; assert sys.version_info >= (3,12); import numpy, scipy, xarray, netCDF4, fastapi, uvicorn, pydantic, yaml, dotenv, filelock' >/dev/null 2>&1; then
    echo 'The active environment needs Python 3.12+ and the predictor dependencies.' >&2
    printf 'Run: python -m pip install -r %q\n' "$predictor_project/requirements.txt" >&2
    exit 1
  fi
else
  predictor_venv="${BALLOONPREDICTOR_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/balloon-predictor/venv}"
  # Retain compatibility with existing project-local installations.
  if [[ -x "$predictor_project/.venv/bin/python" && -z "${BALLOONPREDICTOR_VENV:-}" ]]; then
    predictor_venv="$predictor_project/.venv"
  fi
  if [[ ! -x "$predictor_venv/bin/python" ]]; then
    predictor_python="${BP_PYTHON:-python3}"
    "$predictor_python" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required"'
    mkdir -p -- "$(dirname -- "$predictor_venv")"
    "$predictor_python" -m venv --copies "$predictor_venv"
  fi
  predictor_python="$predictor_venv/bin/python"
  if [[ ! -f "$predictor_venv/.balloon-predictor-1.1-ready" ]]; then
    "$predictor_python" -m pip install -r "$predictor_project/requirements.lock.txt"
    touch "$predictor_venv/.balloon-predictor-1.1-ready"
  fi
fi
# Keep generated data per-user for a shared or read-only installation.
predictor_personal_config=false
[[ -n "${BP_CONFIG:-}" ]] && predictor_personal_config=true
for predictor_argument in "$@"; do
  [[ "$predictor_argument" == --config || "$predictor_argument" == --config=* ]] && predictor_personal_config=true
done
if [[ "$predictor_personal_config" == false ]]; then
  export BP_DATA_DIR="${BP_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/balloon-predictor}"
fi
echo 'BalloonPredictor v1.1 · default http://127.0.0.1:8000 · Ctrl+C to stop.'
exec "$predictor_python" "$predictor_project/server.py" "$@"
