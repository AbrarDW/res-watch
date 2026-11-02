#!/usr/bin/env sh
# res-watch installer for Linux and macOS.
#   ./setup.sh                 interactive: asks where/how you'll run it
#   ./setup.sh --where local --os linux --start autostart --yes    (non-interactive)
# Creates .venv, installs dependencies, then runs `python -m reswatch setup "$@"`.
set -eu
cd "$(dirname "$0")"

pick_python() {
  for c in ${PYTHON:-} python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY=$(pick_python) || { echo "error: Python 3.10 or newer is required (set PYTHON=/path/to/python3)" >&2; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment in .venv (using $($PY --version 2>&1))"
  "$PY" -m venv .venv || { echo "error: could not create a venv (Debian/Ubuntu: sudo apt install python3-venv)" >&2; exit 1; }
fi
VPY=.venv/bin/python
"$VPY" -m pip install --quiet --upgrade pip
echo "Installing dependencies"
"$VPY" -m pip install --quiet -r requirements.txt
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU detected: installing GPU support"
  "$VPY" -m pip install --quiet -r requirements-gpu.txt
fi
"$VPY" -m pip install --quiet --no-deps -e .

exec "$VPY" -m reswatch setup "$@"
