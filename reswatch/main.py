"""FastAPI application: REST API, WebSocket stream, login and static frontend."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE, LoginLimiter, SessionSigner, verify_password
from .config import Settings
from .monitor import Collector

log = logging.getLogger("reswatch")
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_SUBS = 16


class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.specs: list[tuple[str, object, bool]] = []
        self.every = 1
        self.counter = 0


def client_ip(request, settings: Settings) -> str:
    if settings.trusted_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def is_https(request, settings: Settings) -> bool:
    if settings.trusted_proxy:
        proto = request.headers.get("x-forwarded-proto")
        if proto:
            return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme in ("https", "wss")


def create_app(settings: Settings, credentials=None, collector: Collector | None = None) -> FastAPI:
    """`credentials` is required when settings.auth_wanted is true (checked in the CLI)."""
    auth_on = settings.auth_wanted and credentials is not None
    signer = SessionSigner(credentials, settings.session_hours) if auth_on else None
    limiter = LoginLimiter()
    base = settings.base_path
    clients: set[Client] = set()
    state = {"collector": collector}

    def get_collector() -> Collector:
        if state["collector"] is None:
            state["collector"] = Collector()
        return state["collector"]

    async def loop():
        col = get_collector()
        while True:
            specs = list({s for c in list(clients) for s in c.specs})
            try:
                snap = await asyncio.to_thread(col.tick, specs)
            except Exception:  # never let the sampler die
                log.exception("sampling failed")
                await asyncio.sleep(settings.interval)
                continue
            for c in list(clients):
                c.counter += 1
                if c.counter % c.every:
                    continue
                msg = {"type": "tick", "ts": snap["ts"], "system": snap["system"],
                       "entities": [col.entity(snap, k, i, ch) for k, i, ch in c.specs]}
                try:
                    await c.ws.send_text(json.dumps(msg, separators=(",", ":")))
                except Exception:
                    clients.discard(c)
            await asyncio.sleep(settings.interval)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="res-watch", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def session_user(conn) -> str | None:
        if not auth_on:
            return "local"
        return signer.verify(conn.cookies.get(COOKIE))

    public = {f"{base}/api/login", f"{base}/api/me", f"{base}/login", f"{base}/login.html"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if auth_on and path not in public:
            if path.startswith(f"{base}/api/") and session_user(request) is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            if path in (f"{base}/", f"{base}/index.html") and session_user(request) is None:
                return RedirectResponse(f"{base}/login", status_code=303)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    # ------------------------------------------------------------ auth
    @app.get(f"{base}/api/me")
    async def me(request: Request):
        return {"auth": auth_on, "user": session_user(request)}

    @app.post(f"{base}/api/login")
    async def login(request: Request):
        if not auth_on:
            return {"ok": True}
        ip = client_ip(request, settings)
        wait = limiter.retry_after(ip)
        if wait > 0:
            return JSONResponse({"detail": f"Too many attempts. Try again in {int(wait) + 1} s."},
                                status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        try:
            if "json" in request.headers.get("content-type", ""):
                body = await request.json()
            else:
                body = dict(await request.form())
        except Exception:
            body = {}
        user, pw = str(body.get("username", "")), str(body.get("password", ""))
        ok = verify_password(pw, credentials.password_hash)
        if not (ok and user == credentials.username):  # always run scrypt so timing doesn't leak the username
            limiter.failure(ip)
            return JSONResponse({"detail": "Wrong username or password."}, status_code=401)
        limiter.success(ip)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, signer.issue(), max_age=int(settings.session_hours * 3600), httponly=True,
                        samesite="strict", secure=is_https(request, settings), path=base or "/")
        return resp

    @app.post(f"{base}/api/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path=base or "/")
        return resp

    @app.get(f"{base}/login")
    async def login_page():
        if not auth_on:
            return RedirectResponse(f"{base}/", status_code=303)
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- data
    @app.get(f"{base}/api/meta")
    async def meta():
        return {**get_collector().meta(), "interval": settings.interval, "auth": auth_on}

    @app.get(f"{base}/api/system")
    async def system():
        col = get_collector()
        if not col.snapshot["system"]:
            await asyncio.to_thread(col.tick)
        return col.snapshot["system"]

    @app.get(f"{base}/api/processes")
    async def processes():
        col = get_collector()
        if not col.snapshot["procs"]:
            await asyncio.to_thread(col.tick)
        return col.process_list()

    @app.get(f"{base}/api/apps")
    async def apps():
        col = get_collector()
        if not col.snapshot["procs"]:
            await asyncio.to_thread(col.tick)
        return col.app_list()

    @app.get(f"{base}/api/process/{{pid}}")
    async def process(pid: int):
        d = await asyncio.to_thread(get_collector().process_detail, pid)
        if d is None:
            raise HTTPException(404, "No such process")
        return d

    # ------------------------------------------------------------ stream
    @app.websocket(f"{base}/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        if session_user(websocket) is None:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        c = Client(websocket)
        clients.add(c)
        try:
            await websocket.send_text(json.dumps({"type": "hello", **get_collector().meta(),
                                                  "interval": settings.interval}))
            while True:
                msg = json.loads(await websocket.receive_text())
                if msg.get("type") == "subscribe":
                    children = bool(msg.get("children"))
                    specs = []
                    for e in (msg.get("entities") or [])[:MAX_SUBS]:
                        kind, ident = e.get("kind"), e.get("id")
                        if kind == "pid" and str(ident).isdigit():
                            specs.append(("pid", int(ident), children))
                        elif kind == "app" and isinstance(ident, str) and len(ident) < 200:
                            specs.append(("app", ident, False))
                    c.specs = specs
                    c.every = max(1, min(60, int(msg.get("every") or 1)))
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ValueError, TypeError):
            with contextlib.suppress(Exception):
                await websocket.close(code=1003)
        finally:
            clients.discard(c)

    # ----------------------------------------------------------- static
    if base:
        @app.get("/")
        async def root_redirect():
            return RedirectResponse(f"{base}/", status_code=307)

        @app.get(base)
        async def base_redirect():
            return RedirectResponse(f"{base}/", status_code=307)

    @app.get(f"{base}/healthz")
    async def healthz():
        return Response("ok", media_type="text/plain")

    app.mount(base or "/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
