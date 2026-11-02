#!/usr/bin/env sh
# res-watch installer for Linux and macOS.
#   ./setup.sh                 interactive: asks where/how you'll run it
#   ./setup.sh --where local --os linux --start autostart --yes    (non-interactive)
# Creates .venv, installs dependencies, then runs `python -m reswatch setup "$@"`.
#
# Output: on a terminal you get pip's progress bars and a spinner for slow steps.
# With --yes / -y, when stdout is not a terminal, or when CI is set, output is
# plain text (no spinner, no progress bars) so logs stay clean.
set -eu
cd "$(dirname "$0")"

TOTAL=5
FANCY=1
[ -t 1 ] || FANCY=0
[ -n "${CI:-}" ] && FANCY=0
for a in "$@"; do
  case "$a" in --yes|-y) FANCY=0 ;; esac
done
if [ "$FANCY" = 1 ]; then PIP_OUT="--progress-bar on"; else PIP_OUT="--progress-bar off --quiet"; fi

step() { printf '[%s/%s] %s\n' "$1" "$TOTAL" "$2"; }
info() { printf '      %s\n' "$1"; }

# run_long LABEL CMD... : spinner on a terminal, plain otherwise; output is only shown if it fails
run_long() {
  label=$1; shift
  if [ "$FANCY" != 1 ]; then
    info "$label"
    "$@"
    return
  fi
  log=$(mktemp "${TMPDIR:-/tmp}/reswatch-setup.XXXXXX")
  "$@" >"$log" 2>&1 &
  pid=$!
  i=0
  while kill -0 "$pid" 2>/dev/null; do
    case $((i % 4)) in 0) c='|' ;; 1) c='/' ;; 2) c='-' ;; *) c='\' ;; esac
    printf '\r      %s %s' "$c" "$label"
    i=$((i + 1))
    sleep 0.1 2>/dev/null || sleep 1
  done
  if wait "$pid"; then rc=0; else rc=$?; fi
  printf '\r\033[K'
  if [ "$rc" -eq 0 ]; then
    info "$label: done"
  else
    info "$label: FAILED (exit $rc)"
    cat "$log" >&2
  fi
  rm -f "$log"
  return "$rc"
}

# ---------------------------------------------------------------- 1. python
step 1 "Checking Python"
pick_python() {
  for c in ${PYTHON:-} python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY=$(pick_python) || { echo "error: Python 3.10 or newer is required (set PYTHON=/path/to/python3)" >&2; exit 1; }
info "found $($PY --version 2>&1) ($(command -v "$PY"))"

# ------------------------------------------------------------------ 2. venv
step 2 "Creating virtual environment (.venv)"
if [ -x .venv/bin/python ]; then
  info "already exists, reusing it"
else
  run_long "creating .venv" "$PY" -m venv .venv \
    || { echo "error: could not create a venv (Debian/Ubuntu: sudo apt install python3-venv)" >&2; exit 1; }
fi
VPY=.venv/bin/python
run_long "updating pip" "$VPY" -m pip install --quiet --upgrade pip

# ------------------------------------------------------------------ 3. deps
step 3 "Installing dependencies (requirements.txt)"
# shellcheck disable=SC2086  # PIP_OUT is intentionally split into flags
"$VPY" -m pip install $PIP_OUT -r requirements.txt
run_long "installing res-watch (editable)" "$VPY" -m pip install --quiet --no-deps -e .

# ------------------------------------------------------------------- 4. gpu
step 4 "GPU support"
if command -v nvidia-smi >/dev/null 2>&1; then
  info "NVIDIA GPU detected: installing NVML bindings"
  # shellcheck disable=SC2086
  "$VPY" -m pip install $PIP_OUT -r requirements-gpu.txt
else
  info "skipped (no nvidia-smi found; the GPU tab will say metrics are unavailable)"
fi

# ---------------------------------------------------------------- 5. wizard
step 5 "Running the setup wizard"
exec "$VPY" -m reswatch setup "$@"
