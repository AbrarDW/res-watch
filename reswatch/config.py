"""Layered settings: defaults < config.toml < environment variables < CLI flags."""
from __future__ import annotations

import ipaddress
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    _toml = None

AUTH_MODES = ("auto", "on", "off")


def default_config_dir() -> Path:
    env = os.environ.get("RESWATCH_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "reswatch"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "reswatch"


def normalize_base_path(value: str | None) -> str:
    """'' for root, otherwise '/sub/path' with no trailing slash."""
    if not value:
        return ""
    value = "/" + value.strip().strip("/")
    return "" if value == "/" else value


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    base_path: str = ""
    interval: float = 1.0
    auth: str = "auto"            # auto: on when bound to a non-loopback host
    trusted_proxy: bool = False
    session_hours: float = 12.0
    config_dir: Path = field(default_factory=default_config_dir)

    # --- derived -------------------------------------------------------
    @property
    def loopback(self) -> bool:
        return is_loopback(self.host)

    @property
    def auth_wanted(self) -> bool:
        if self.auth == "on":
            return True
        if self.auth == "off":
            return False
        return not self.loopback


ENV_MAP = {
    "host": "RESWATCH_HOST",
    "port": "RESWATCH_PORT",
    "base_path": "RESWATCH_BASE_PATH",
    "interval": "RESWATCH_INTERVAL",
    "auth": "RESWATCH_AUTH",
    "trusted_proxy": "RESWATCH_TRUSTED_PROXY",
    "session_hours": "RESWATCH_SESSION_HOURS",
}


def _coerce(name: str, value):
    if name == "port":
        return int(value)
    if name in ("interval", "session_hours"):
        return float(value)
    if name == "trusted_proxy":
        return _as_bool(value)
    if name == "base_path":
        return normalize_base_path(str(value))
    if name == "auth":
        v = str(value).lower()
        if v in ("true", "builtin", "yes"):
            v = "on"
        if v in ("false", "no", "none", "proxy"):
            v = "off"
        if v not in AUTH_MODES:
            raise ValueError(f"auth must be one of {AUTH_MODES}, got {value!r}")
        return v
    if name == "config_dir":
        return Path(value).expanduser()
    return str(value)


def read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if _toml is not None:
        return _toml.loads(text)
    out: dict = {}  # minimal flat key = value parser (3.10)
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line or line.startswith("["):
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        out[k] = v.strip('"').strip("'") if v[:1] in "\"'" else v
    return out


def toml_text(data: dict, header: str = "") -> str:
    lines = [header.rstrip()] if header else []
    for k, v in data.items():
        if isinstance(v, bool):
            s = "true" if v else "false"
        elif isinstance(v, (int, float)):
            s = repr(v)
        else:
            s = '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"{k} = {s}")
    return "\n".join(lines) + "\n"


def write_toml(path: Path, data: dict, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_text(data, header), encoding="utf-8")


def load_settings(cli: dict | None = None, env: dict | None = None) -> Settings:
    """Merge config sources. `cli` holds only flags the user actually passed."""
    cli = {k: v for k, v in (cli or {}).items() if v is not None}
    env = os.environ if env is None else env
    s = Settings()
    if "config_dir" in cli:
        s.config_dir = _coerce("config_dir", cli["config_dir"])
    names = {f.name for f in fields(Settings)} - {"config_dir"}
    for k, v in read_toml(s.config_dir / "config.toml").items():
        if k in names:
            setattr(s, k, _coerce(k, v))
    for k, var in ENV_MAP.items():
        if env.get(var) not in (None, ""):
            setattr(s, k, _coerce(k, env[var]))
    for k, v in cli.items():
        if k in names:
            setattr(s, k, _coerce(k, v))
    if s.interval < 0.2:
        s.interval = 0.2
    return s
