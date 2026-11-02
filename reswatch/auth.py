"""Built-in single-admin login.

* Passwords are stored only as salted scrypt hashes (hashlib.scrypt), encoded
  as one string:  scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>
* Sessions are HMAC-SHA256 signed cookies: base64(json{u, exp}).signature
* Credentials come from env vars (RESWATCH_USERNAME / RESWATCH_PASSWORD_HASH /
  RESWATCH_SECRET_KEY) or from <config dir>/auth.json (mode 0600).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
COOKIE = "reswatch_session"
MAX_FAILS = 5
LOCK_BASE, LOCK_CAP = 30.0, 900.0


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- hashing
def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, dk = encoded.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=128 * 1024 * 1024, dklen=len(_unb64(dk)))
        return hmac.compare_digest(got, _unb64(dk))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------ credentials
@dataclass
class Credentials:
    username: str
    password_hash: str
    secret_key: str
    source: str = ""


def auth_file(config_dir: Path) -> Path:
    return Path(config_dir) / "auth.json"


def load_credentials(config_dir: Path, env=None) -> Credentials | None:
    env = os.environ if env is None else env
    u, h, k = (env.get(v) for v in ("RESWATCH_USERNAME", "RESWATCH_PASSWORD_HASH", "RESWATCH_SECRET_KEY"))
    if u and h and k:
        return Credentials(u, h, k, "environment")
    if any((u, h, k)):
        raise SystemExit("RESWATCH_USERNAME, RESWATCH_PASSWORD_HASH and RESWATCH_SECRET_KEY must all be set together.")
    path = auth_file(config_dir)
    if not path.is_file():
        return None
    if sys.platform != "win32":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            print(f"warning: {path} is readable by other users (mode {oct(mode)}); run: chmod 600 {path}",
                  file=sys.stderr)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Credentials(data["username"], data["password_hash"], data["secret_key"], str(path))


def save_credentials(config_dir: Path, username: str, password: str) -> Path:
    path = auth_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    try:  # keep the existing secret so other settings survive; sessions are invalidated by the new hash anyway
        secret = json.loads(path.read_text())["secret_key"]
    except Exception:
        pass
    data = {"username": username, "password_hash": hash_password(password), "secret_key": secret,
            "note": "password_hash = scrypt$n$r$p$salt$hash (salted scrypt; the password itself is never stored)"}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------- sessions
class SessionSigner:
    def __init__(self, creds: Credentials, hours: float):
        # binding the key to the password hash means changing the password logs everyone out
        self.key = hashlib.sha256((creds.secret_key + "|" + creds.password_hash).encode()).digest()
        self.ttl = hours * 3600
        self.username = creds.username

    def issue(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        body = _b64(json.dumps({"u": self.username, "exp": int(now + self.ttl)}).encode())
        sig = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify(self, token: str | None, now: float | None = None) -> str | None:
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        want = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        try:
            data = json.loads(_unb64(body))
        except ValueError:
            return None
        now = time.time() if now is None else now
        if data.get("exp", 0) < now or data.get("u") != self.username:
            return None
        return data["u"]


# ------------------------------------------------------------ rate limit
class LoginLimiter:
    """After MAX_FAILS failures from one IP, lock it out: 30 s, 60 s, 120 s ... capped at 15 min."""

    def __init__(self):
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, ip: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            n, until = self._fails.get(ip, (0, 0.0))
        return max(0.0, until - now)

    def failure(self, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            n, _ = self._fails.get(ip, (0, 0.0))
            n += 1
            until = 0.0
            if n >= MAX_FAILS:
                until = now + min(LOCK_CAP, LOCK_BASE * 2 ** (n - MAX_FAILS))
            self._fails[ip] = (n, until)
            if len(self._fails) > 10000:  # bound memory
                self._fails.pop(next(iter(self._fails)))

    def success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
