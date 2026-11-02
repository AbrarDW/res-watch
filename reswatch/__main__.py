"""Command line:  python -m reswatch [serve|setup|set-password|hash-password] ..."""
from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .config import load_settings

COMMANDS = ("serve", "setup", "set-password", "hash-password")


def _serve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", help="bind address (default 127.0.0.1, env RESWATCH_HOST)")
    p.add_argument("--port", type=int, help="port (default 8000, env RESWATCH_PORT)")
    p.add_argument("--base-path", dest="base_path", help="serve under a sub-path, e.g. /reswatch (env RESWATCH_BASE_PATH)")
    p.add_argument("--interval", type=float, help="sampling interval in seconds (default 1)")
    p.add_argument("--auth", choices=("auto", "on", "off"),
                   help="built-in login: auto = on for non-localhost binds (default)")
    p.add_argument("--no-auth", dest="no_auth", action="store_true",
                   help="disable the built-in login (only when a proxy/SSO in front already authenticates)")
    p.add_argument("--trusted-proxy", dest="trusted_proxy", action="store_true", default=None,
                   help="trust X-Forwarded-For / X-Forwarded-Proto from a reverse proxy")
    p.add_argument("--session-hours", dest="session_hours", type=float, help="login session lifetime (default 12)")
    p.add_argument("--config-dir", dest="config_dir", help="config directory (default ~/.config/reswatch)")
    p.add_argument("--log-level", default="info")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m reswatch", description="res-watch: per-process resource monitor")
    ap.add_argument("--version", action="version", version=f"res-watch {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    _serve_args(sub.add_parser("serve", help="run the web server (default)"))

    sp = sub.add_parser("set-password", help="create/replace the admin login (writes auth.json, mode 0600)")
    sp.add_argument("--username", default=None)
    sp.add_argument("--config-dir", dest="config_dir")
    sp.add_argument("--password-stdin", action="store_true", help="read the password from stdin (automation)")

    hp = sub.add_parser("hash-password", help="print a password hash for RESWATCH_PASSWORD_HASH")
    hp.add_argument("--password-stdin", action="store_true")

    from .setup_wizard import add_arguments
    add_arguments(sub.add_parser("setup", help="interactive setup: local machine or VPS"))
    return ap


def _read_password(from_stdin: bool, confirm: bool = True) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password: ")
        if confirm and getpass.getpass("Repeat password: ") != pw:
            raise SystemExit("error: passwords do not match")
    if len(pw) < 8:
        raise SystemExit("error: use at least 8 characters")
    return pw


def cmd_set_password(args) -> int:
    from .auth import load_credentials, save_credentials
    s = load_settings({"config_dir": args.config_dir})
    current = None
    try:
        current = load_credentials(s.config_dir, env={})
    except Exception:
        pass
    default_user = current.username if current else "admin"
    username = args.username
    if not username:
        username = default_user if args.password_stdin else (input(f"Username [{default_user}]: ").strip() or default_user)
    path = save_credentials(s.config_dir, username, _read_password(args.password_stdin))
    print(f"Saved login for '{username}' to {path} (mode 0600). Existing sessions are logged out.")
    return 0


def cmd_hash_password(args) -> int:
    from .auth import hash_password
    import secrets
    h = hash_password(_read_password(args.password_stdin))
    print(h)
    print("\n# for environment-based config:", file=sys.stderr)
    print(f"# RESWATCH_USERNAME=admin\n# RESWATCH_PASSWORD_HASH='{h}'\n# RESWATCH_SECRET_KEY={secrets.token_urlsafe(32)}",
          file=sys.stderr)
    return 0


def check_startup(settings, env=None):
    """Return (credentials, warnings). Raises SystemExit with a clear message when unsafe."""
    from .auth import load_credentials
    creds = load_credentials(settings.config_dir, env=env)
    warnings = []
    if settings.auth_wanted and creds is None:
        if not settings.loopback:
            raise SystemExit(
                f"error: refusing to listen on {settings.host}:{settings.port} without a login.\n"
                "  Set a password first:   python -m reswatch set-password\n"
                "  (or set RESWATCH_USERNAME / RESWATCH_PASSWORD_HASH / RESWATCH_SECRET_KEY)\n"
                "  If a reverse proxy / SSO already authenticates users, start with --no-auth instead.")
        raise SystemExit("error: --auth on was requested but no login is configured. "
                         "Run: python -m reswatch set-password")
    if not settings.auth_wanted and not settings.loopback:
        warnings.append(f"WARNING: built-in login is OFF while listening on {settings.host}. Anyone who can reach "
                        f"port {settings.port} can see every process on this machine. Only do this behind a proxy/SSO "
                        "that authenticates users.")
    return creds, warnings


def cmd_serve(args) -> int:
    import logging
    import uvicorn
    from .main import create_app

    cli = {k: getattr(args, k, None) for k in ("host", "port", "base_path", "interval", "auth", "trusted_proxy",
                                               "session_hours", "config_dir")}
    if args.no_auth:
        cli["auth"] = "off"
    settings = load_settings(cli)
    creds, warnings = check_startup(settings)
    for w in warnings:
        print(w, file=sys.stderr)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    app = create_app(settings, creds)
    shown = "localhost" if settings.loopback else settings.host
    print(f"res-watch {__version__} on http://{shown}:{settings.port}{settings.base_path}/  "
          f"(login {'on' if settings.auth_wanted else 'off'})", file=sys.stderr)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=args.log_level, proxy_headers=False,
                ws_ping_interval=20)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)
    if args.cmd == "set-password":
        return cmd_set_password(args)
    if args.cmd == "hash-password":
        return cmd_hash_password(args)
    if args.cmd == "setup":
        from .setup_wizard import run
        try:
            return run(args)
        except EOFError:
            print("\nerror: no answer received (stdin is closed). For unattended installs pass the answers as "
                  "flags with --yes, e.g.  --yes --where local --start manual", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("\nsetup cancelled; nothing else was changed.", file=sys.stderr)
            return 130
    return cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())
