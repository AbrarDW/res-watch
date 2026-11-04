#!/usr/bin/env bash
# Build the image the setup wizard generates and check, against a real Docker daemon:
#   1. `docker run --pid=host` (local setup): the container sees a process started on the host
#   2. Docker Compose (VPS setup, built-in login): API needs a session, login works, host processes visible
#   scripts/ci/docker-smoke.sh          needs: docker (+ compose plugin), curl, python with reswatch deps
# Sandboxes whose HTTPS goes through an intercepting proxy can set RESWATCH_CI_CA=/path/ca.pem; the CA is
# then added to a throwaway copy of the build so pip trusts it (never used in CI).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python3}
WORK=$(mktemp -d)
created=()
for f in Dockerfile .dockerignore docker-compose.yml; do [ -e "$f" ] || created+=("$f"); done
cleanup() {
  docker rm -f rw-ci >/dev/null 2>&1 || true
  [ -f docker-compose.yml ] && docker compose down -v >/dev/null 2>&1 || true
  kill "${HOSTPID:-0}" 2>/dev/null || true
  for f in "${created[@]}" ci-ca.crt; do rm -f "$f"; done
  rm -rf "$WORK"
}
trap cleanup EXIT
fails=0
ok()   { echo "ok    $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
wait_http() { for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 0.5; done; return 1; }
sees_pid() {  # sees_pid <url> <pid> [curl args...]
  local url=$1 pid=$2; shift 2
  curl -fsS "$@" "$url" | "$PY" -c "import json,sys; sys.exit(0 if any(p['pid']==$pid for p in json.load(sys.stdin)) else 1)"
}
build() {
  local extra=()
  if [ -n "${RESWATCH_CI_CA:-}" ]; then
    cp "$RESWATCH_CI_CA" ci-ca.crt
    sed -i 's#^RUN pip install#COPY ci-ca.crt /tmp/ci-ca.crt\nENV PIP_CERT=/tmp/ci-ca.crt\nRUN pip install#' Dockerfile
    extra=(--network host --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" --build-arg "HTTP_PROXY=${HTTP_PROXY:-}")
  fi
  docker build -q "${extra[@]}" -t reswatch:latest . >/dev/null
}

sleep 600 & HOSTPID=$!   # a process on the host the container must be able to see

# ---- 1. local: docker run --pid=host -----------------------------------------
"$PY" -m reswatch setup --yes --where local --os linux --start docker --output-dir . --config-dir "$WORK/cfg1" >"$WORK/setup1.txt"
grep -q -- "--pid=host" "$WORK/setup1.txt" && ok "wizard prints a docker run command with --pid=host" || fail "no --pid=host in wizard output"
if build; then ok "docker build"; else fail "docker build"; exit 1; fi
docker run -d --name rw-ci --pid=host --read-only --cap-drop ALL --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:18000:8000 -e RESWATCH_AUTH=off reswatch:latest >/dev/null
if wait_http http://127.0.0.1:18000/healthz; then ok "container healthy (read-only, all capabilities dropped)"; else fail "container did not start"; docker logs rw-ci; fi
sees_pid http://127.0.0.1:18000/api/processes "$HOSTPID" && ok "container sees host process $HOSTPID via --pid=host" || fail "host process $HOSTPID not visible"
docker rm -f rw-ci >/dev/null

# ---- 2. VPS: Docker Compose with the built-in login --------------------------
"$PY" -m reswatch setup --yes --where vps --run compose --proxy none --auth builtin --port 18001 \
  --output-dir . --config-dir "$WORK/cfg2" >/dev/null
echo 'ci-compose-password' | "$PY" -m reswatch set-password --username admin --password-stdin --config-dir "$WORK/cfg2" >/dev/null
chmod 755 "$WORK" "$WORK/cfg2"
build
docker compose up -d >/dev/null 2>&1
if wait_http http://127.0.0.1:18001/healthz; then ok "compose service healthy"; else fail "compose service did not start"; docker compose logs; fi
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18001/api/processes)
[ "$code" = 401 ] && ok "API requires login (401)" || fail "API without login returned $code"
curl -fsS -c "$WORK/jar" -H 'Content-Type: application/json' -d '{"username":"admin","password":"ci-compose-password"}' \
  http://127.0.0.1:18001/api/login >/dev/null && ok "login with the password from set-password (auth.json mounted read-only)" || fail "login failed"
sees_pid http://127.0.0.1:18001/api/processes "$HOSTPID" -b "$WORK/jar" && ok "compose container sees host process $HOSTPID" || fail "compose: host process not visible"

echo
[ "$fails" -eq 0 ] && echo "docker checks passed" || { echo "$fails check(s) failed"; exit 1; }
