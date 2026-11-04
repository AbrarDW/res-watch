import json
import os
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from reswatch import auth
from reswatch.__main__ import check_startup
from reswatch.config import load_settings
from reswatch.gpu import NullGpu
from reswatch.main import create_app
from reswatch.monitor import Collector

PW = "correct horse battery"


def test_hash_roundtrip_and_wrong_password():
    h = auth.hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert auth.verify_password(PW, h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password(PW, "garbage")
    assert h != auth.hash_password(PW)  # salted


def test_save_credentials_is_private(tmp_path):
    path = auth.save_credentials(tmp_path, "admin", PW)
    data = json.loads(path.read_text())
    assert PW not in path.read_text()
    assert set(data) >= {"username", "password_hash", "secret_key"}
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    creds = auth.load_credentials(tmp_path, env={})
    assert creds.username == "admin" and auth.verify_password(PW, creds.password_hash)


def test_env_credentials_win(tmp_path):
    env = {"RESWATCH_USERNAME": "u", "RESWATCH_PASSWORD_HASH": auth.hash_password(PW), "RESWATCH_SECRET_KEY": "k"}
    assert auth.load_credentials(tmp_path, env=env).source == "environment"
    with pytest.raises(SystemExit):
        auth.load_credentials(tmp_path, env={"RESWATCH_USERNAME": "u"})


def test_session_signing():
    creds = auth.Credentials("admin", auth.hash_password(PW), "secret")
    s = auth.SessionSigner(creds, hours=1)
    tok = s.issue(now=1000)
    assert s.verify(tok, now=1000 + 10) == "admin"
    assert s.verify(tok, now=1000 + 3601) is None          # expired
    assert s.verify(tok[:-2] + "xx", now=1010) is None      # tampered
    other = auth.SessionSigner(auth.Credentials("admin", auth.hash_password(PW), "secret"), 1)
    assert other.verify(tok, now=1010) is None              # password changed -> logged out


def test_rate_limiter_backoff():
    lim = auth.LoginLimiter()
    for _ in range(4):
        lim.failure("1.2.3.4", now=0)
    assert lim.retry_after("1.2.3.4", now=0) == 0
    lim.failure("1.2.3.4", now=0)
    assert lim.retry_after("1.2.3.4", now=0) == pytest.approx(30)
    lim.failure("1.2.3.4", now=0)
    assert lim.retry_after("1.2.3.4", now=0) == pytest.approx(60)
    for _ in range(20):
        lim.failure("1.2.3.4", now=0)
    assert lim.retry_after("1.2.3.4", now=0) == auth.LOCK_CAP
    lim.success("1.2.3.4")
    assert lim.retry_after("1.2.3.4", now=0) == 0


# ------------------------------------------------------------------ app
def make_client(tmp_path, **cli):
    auth.save_credentials(tmp_path, "admin", PW)
    settings = load_settings({"config_dir": str(tmp_path), "auth": "on", **cli}, env={})
    creds = auth.load_credentials(tmp_path, env={})
    app = create_app(settings, creds, collector=Collector(gpu=NullGpu()))
    return TestClient(app)


def login(c, pw=PW, headers=None):
    return c.post("/api/login", json={"username": "admin", "password": pw}, headers=headers or {})


def test_api_and_ws_require_session(tmp_path):
    with make_client(tmp_path) as c:
        for path in ("/api/processes", "/api/system", "/api/apps", "/api/meta", "/api/process/1"):
            assert c.get(path).status_code == 401, path
        assert c.get("/", follow_redirects=False).status_code == 303
        with c.websocket_connect("/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4401
        assert c.get("/login").status_code == 200
        assert c.get("/api/me").json() == {"auth": True, "user": None}


def test_login_logout_flow(tmp_path):
    with make_client(tmp_path) as c:
        assert login(c, "nope").status_code == 401
        r = login(c)
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        assert c.get("/api/processes").status_code == 200
        with c.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello"
            ws.send_text(json.dumps({"type": "subscribe", "entities": [{"kind": "pid", "id": os.getpid()}]}))
            tick = json.loads(ws.receive_text())
            assert tick["type"] == "tick" and tick["entities"][0]["pid"] == os.getpid()
        c.post("/api/logout")
        c.cookies.clear()
        assert c.get("/api/processes").status_code == 401


def test_login_rate_limited_after_5_failures(tmp_path):
    with make_client(tmp_path) as c:
        for _ in range(5):
            assert login(c, "bad").status_code == 401
        r = login(c)  # even the right password is refused while locked out
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_forwarded_headers_ignored_without_trusted_proxy(tmp_path):
    with make_client(tmp_path) as c:
        for i in range(5):  # spoofing X-Forwarded-For must not dodge the limiter
            login(c, "bad", headers={"X-Forwarded-For": f"10.0.0.{i}"})
        assert login(c, headers={"X-Forwarded-For": "10.9.9.9"}).status_code == 429
        r = login(TestClient(c.app), headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 429


def test_forwarded_headers_used_with_trusted_proxy(tmp_path):
    with make_client(tmp_path, trusted_proxy=True) as c:
        for _ in range(5):
            login(c, "bad", headers={"X-Forwarded-For": "10.0.0.1"})
        assert login(c, headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
        r = login(c, headers={"X-Forwarded-For": "10.0.0.2", "X-Forwarded-Proto": "https"})
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()


def test_base_path(tmp_path):
    with make_client(tmp_path, base_path="/reswatch") as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/reswatch/"
        assert c.get("/reswatch/api/processes").status_code == 401
        login_r = c.post("/reswatch/api/login", json={"username": "admin", "password": PW})
        assert "path=/reswatch" in login_r.headers["set-cookie"].lower()
        assert c.get("/reswatch/api/processes").status_code == 200
        assert c.get("/reswatch/style.css").status_code == 200


# ------------------------------------------------------------ startup
def test_refuses_public_bind_without_password(tmp_path):
    s = load_settings({"config_dir": str(tmp_path), "host": "0.0.0.0"}, env={})
    with pytest.raises(SystemExit) as e:
        check_startup(s, env={})
    assert "set-password" in str(e.value)


def test_no_auth_allows_public_bind_with_warning(tmp_path):
    s = load_settings({"config_dir": str(tmp_path), "host": "0.0.0.0", "auth": "off"}, env={})
    creds, warnings = check_startup(s, env={})
    assert creds is None and warnings and "OFF" in warnings[0]


def test_localhost_needs_no_login(tmp_path):
    s = load_settings({"config_dir": str(tmp_path)}, env={})
    assert s.host == "127.0.0.1" and not s.auth_wanted
    assert check_startup(s, env={}) == (None, [])


def test_public_bind_with_password_is_ok(tmp_path):
    auth.save_credentials(tmp_path, "admin", PW)
    s = load_settings({"config_dir": str(tmp_path), "host": "0.0.0.0"}, env={})
    creds, warnings = check_startup(s, env={})
    assert creds and not warnings and s.auth_wanted


def test_settings_layering(tmp_path):
    (tmp_path / "config.toml").write_text('port = 9000\nhost = "127.0.0.1"\nbase_path = "sub/"\n')
    s = load_settings({"config_dir": str(tmp_path)}, env={"RESWATCH_PORT": "9100"})
    assert (s.port, s.base_path) == (9100, "/sub")
    s = load_settings({"config_dir": str(tmp_path), "port": 9200}, env={"RESWATCH_PORT": "9100"})
    assert s.port == 9200


def test_ws_accepts_group_kinds(tmp_path):
    with make_client(tmp_path) as c:
        login(c)
        user = c.get("/api/processes").json()[0]["user"] or "?"
        with c.websocket_connect("/ws") as ws:
            json.loads(ws.receive_text())  # hello
            ws.send_text(json.dumps({"type": "subscribe", "entities": [
                {"kind": "exe", "id": "python"}, {"kind": "user", "id": user}, {"kind": "bogus", "id": "x"}]}))
            tick = json.loads(ws.receive_text())
            assert [e["id"] for e in tick["entities"]] == ["exe:python", f"user:{user}"]
