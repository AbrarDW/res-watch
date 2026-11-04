#!/usr/bin/env bash
# Generate every reverse-proxy config with the setup wizard and check that the
# real server accepts it (syntax + modules), inside the official containers.
#   scripts/ci/check-proxy-configs.sh            needs: docker, openssl, python with reswatch deps
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python3}
WORK=$(mktemp -d)
trap 'docker rm -f rw-traefik >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
DOMAIN=mon.example.com
fails=0
ok()   { echo "ok    $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }

gen() {  # gen <name> <proxy> <run> <base-path>
  local dir="$WORK/$1"; shift
  local args=(--yes --where vps --proxy "$1" --run "$2" --domain "$DOMAIN" --https have --auth builtin
              --output-dir "$dir" --config-dir "$dir/cfg")
  [ -n "$3" ] && args+=(--base-path "$3")
  "$PY" -m reswatch setup "${args[@]}" >/dev/null
  echo "$dir"
}

# self-signed certificate at the Let's Encrypt paths the nginx/Apache configs expect
mkdir -p "$WORK/le/live/$DOMAIN"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=$DOMAIN" \
  -keyout "$WORK/le/live/$DOMAIN/privkey.pem" -out "$WORK/le/live/$DOMAIN/fullchain.pem" 2>/dev/null
chmod -R a+rX "$WORK"

for base in "" "/reswatch"; do
  label="base path '${base:-/}'"

  # ---- nginx
  d=$(gen "nginx$base" nginx systemd "$base"); chmod -R a+rX "$d"
  # --network host: nginx sees the same IPv6 support the wizard detected on this machine
  if docker run --rm --network host -v "$d/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "$WORK/le:/etc/letsencrypt:ro" \
       nginx:1.27-alpine nginx -t >"$WORK/out" 2>&1; then ok "nginx -t ($label)"; else fail "nginx -t ($label)"; cat "$WORK/out"; fi

  # ---- Caddy
  d=$(gen "caddy$base" caddy systemd "$base"); chmod -R a+rX "$d"
  if docker run --rm -v "$d/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8-alpine \
       caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >"$WORK/out" 2>&1; then ok "caddy validate ($label)"; else fail "caddy validate ($label)"; cat "$WORK/out"; fi

  # ---- Apache: minimal httpd.conf that loads the modules the README lists, then our site
  d=$(gen "apache$base" apache systemd "$base"); chmod -R a+rX "$d"
  cat > "$d/httpd.conf" <<'CONF'
ServerRoot /usr/local/apache2
Listen 80
Listen 443
LoadModule mpm_event_module modules/mod_mpm_event.so
LoadModule authz_core_module modules/mod_authz_core.so
LoadModule unixd_module modules/mod_unixd.so
LoadModule alias_module modules/mod_alias.so
LoadModule headers_module modules/mod_headers.so
LoadModule socache_shmcb_module modules/mod_socache_shmcb.so
LoadModule ssl_module modules/mod_ssl.so
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule proxy_wstunnel_module modules/mod_proxy_wstunnel.so
User daemon
Group daemon
ServerName localhost
ErrorLog /proc/self/fd/2
Include /test/deploy/apache.conf
CONF
  chmod a+r "$d/httpd.conf"
  if docker run --rm -v "$d:/test:ro" -v "$WORK/le:/etc/letsencrypt:ro" httpd:2.4 \
       httpd -t -f /test/httpd.conf >"$WORK/out" 2>&1; then ok "apache httpd -t ($label)"; else fail "apache httpd -t ($label)"; cat "$WORK/out"; fi

  # ---- Traefik (file provider): load it and ask the API whether the router is enabled
  d=$(gen "traefik$base" traefik systemd "$base"); chmod -R a+rX "$d"
  docker rm -f rw-traefik >/dev/null 2>&1 || true
  docker run -d --name rw-traefik -p 127.0.0.1:18080:8080 -v "$d/deploy/traefik-dynamic.yml:/dyn.yml:ro" traefik:v3.1 \
    --api.insecure=true --entrypoints.web.address=:80 --entrypoints.websecure.address=:443 \
    --providers.file.filename=/dyn.yml \
    --certificatesresolvers.letsencrypt.acme.email=ci@example.com \
    --certificatesresolvers.letsencrypt.acme.storage=/tmp/acme.json \
    --certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web >/dev/null
  status=""
  for _ in $(seq 1 20); do
    status=$(curl -fsS http://127.0.0.1:18080/api/http/routers/reswatch@file 2>/dev/null \
      | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(r.get("status"), r.get("rule"))' 2>/dev/null || true)
    [ -n "$status" ] && break; sleep 0.5
  done
  if [[ "$status" == enabled* ]]; then ok "traefik router enabled ($label): ${status#enabled }"; else fail "traefik router ($label): '${status}'"; docker logs rw-traefik 2>&1 | tail -20; fi
  docker rm -f rw-traefik >/dev/null

  # ---- Traefik labels in docker-compose.yml: compose must accept the file
  d=$(gen "traefik-compose$base" traefik compose "$base")
  if (cd "$d" && docker compose config -q) >"$WORK/out" 2>&1; then ok "docker compose config with Traefik labels ($label)"; else fail "compose config ($label)"; cat "$WORK/out"; fi

  # ---- Cloudflare Tunnel ingress rules
  d=$(gen "cloudflare$base" cloudflare systemd "$base"); chmod -R a+rX "$d"
  if docker run --rm -v "$d/deploy/cloudflared.yml:/c.yml:ro" cloudflare/cloudflared:2024.12.2 \
       tunnel --config /c.yml ingress validate >"$WORK/out" 2>&1; then ok "cloudflared ingress validate ($label)"; else fail "cloudflared ingress validate ($label)"; cat "$WORK/out"; fi
done

echo
[ "$fails" -eq 0 ] && echo "all proxy configs valid" || { echo "$fails check(s) failed"; exit 1; }
