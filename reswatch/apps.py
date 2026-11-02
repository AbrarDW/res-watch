"""Group processes into "apps".

Resolution order for a process (first hit wins):
  1. kernel threads                     -> "kernel"
  2. macOS app bundle in the exe path   -> bundle name ("Google Chrome")
  3. Linux cgroup app scope (systemd / snap / flatpak), except terminal-like
     launchers whose scope also contains everything typed into them
  4. process tree: walk up while the parent has the same executable name or
     lives in the same (non-generic) install directory, then use that
     ancestor's normalised executable name
"""
from __future__ import annotations

import os
import re
import sys

GENERIC_DIRS = {
    "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin",
    "/usr/libexec", "/usr/lib", "/opt/homebrew/bin", "/snap/bin",
    "c:\\windows\\system32", "c:\\windows", "c:\\windows\\syswow64",
}
# Scopes that host arbitrary user commands; grouping by them would put every
# script run from a terminal under "terminal".
LAUNCHER_SCOPES = {
    "terminal", "gnome-terminal", "gnome-terminal-server", "konsole", "kitty", "alacritty",
    "xterm", "tilix", "terminator", "wezterm", "foot", "ptyxis", "console", "code",
    "vscodium", "tmux", "screen", "vte-spawn", "guake", "yakuake", "xfce4-terminal",
}
_INTERP = re.compile(r"^(python|pypy|perl|ruby|php|node|nodejs|java|lua|luajit|bash|zsh)[\d.]*m?$")
_HELPER = re.compile(r"[\s_-]+helper(\s*\(.*\))?$", re.I)
_VERSION = re.compile(r"[-_]v?\d+(\.\d+)+$")
_HEXID = re.compile(r"^[0-9a-f-]{16,}$")


def normalize_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return "unknown"
    n = re.sub(r"\s*\(deleted\)$", "", n)
    n = re.sub(r"^memfd:", "", n)
    n = re.sub(r"\.exe$", "", n, flags=re.I)
    n = n.split("/", 1)[0] or n            # kworker/3:1 -> kworker
    n = _HELPER.sub("", n)                 # "Google Chrome Helper (Renderer)" -> "Google Chrome"
    n = n.strip().lower()
    m = _INTERP.match(n)
    if m:
        return {"nodejs": "node", "pypy": "python"}.get(m.group(1), m.group(1))
    n = _VERSION.sub("", n)                # gcc-12.2 -> gcc
    return n or "unknown"


def _unescape_systemd(s: str) -> str:
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)


def cgroup_app(cgroup_text: str) -> str | None:
    """App name from /proc/<pid>/cgroup content, or None."""
    for line in (cgroup_text or "").splitlines():
        path = line.split(":", 2)[-1]
        for comp in reversed([c for c in path.split("/") if c]):
            comp = _unescape_systemd(comp)
            name = None
            if comp.startswith("snap."):
                parts = comp.split(".")
                name = parts[1] if len(parts) > 2 else None
            elif comp.startswith("app-") and comp.endswith((".scope", ".service")):
                rest = comp[4:].rsplit(".", 1)[0]
                rest = rest.split("@", 1)[0]
                for pre in ("gnome-", "kde-", "flatpak-", "xfce-", "mate-", "cinnamon-", "lxqt-"):
                    if rest.startswith(pre):
                        rest = rest[len(pre):]
                        break
                rest = re.sub(r"-(\d+|[0-9a-f]{32}|[0-9a-f-]{36})$", "", rest)
                if "." in rest and rest.count(".") >= 2:   # reverse-DNS id
                    rest = rest.rsplit(".", 1)[-1]
                name = rest
            if name:
                name = normalize_name(name)
                if name in LAUNCHER_SCOPES or name.startswith("vte-spawn") or _HEXID.match(name):
                    return None
                return name
            if comp.endswith(".scope"):
                return None  # innermost scope is not an app scope -> no answer
    return None


def bundle_app(exe: str | None) -> str | None:
    if not exe:
        return None
    m = re.search(r"/([^/]+)\.app/", exe)
    return m.group(1) if m else None


def read_cgroup(pid: int) -> str:
    if not sys.platform.startswith("linux"):
        return ""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _exe_dir(exe: str | None) -> str | None:
    if not exe:
        return None
    d = os.path.dirname(exe)
    return None if d.lower().rstrip("/\\") in GENERIC_DIRS else d


def base_key(p: dict) -> str:
    exe = p.get("exe")
    return normalize_name(os.path.basename(exe) if exe else p.get("name", ""))


def resolve_app(pid: int, procs: dict[int, dict], cgroup_reader=read_cgroup) -> tuple[str, str]:
    """Return (app_key, display_name) for pid, using the snapshot `procs` for parent lookups."""
    p = procs[pid]
    if p.get("kernel"):
        return "kernel", "kernel threads"
    b = bundle_app(p.get("exe"))
    if b:
        return normalize_name(b), b
    cg = cgroup_app(cgroup_reader(pid))
    if cg:
        return cg, cg
    cur, seen = p, {pid}
    while True:
        parent = procs.get(cur.get("ppid") or 0)
        if not parent or parent["pid"] in seen or parent["pid"] <= 1 or parent.get("kernel"):
            break
        same_name = base_key(parent) == base_key(cur)
        d = _exe_dir(cur.get("exe"))
        same_dir = d is not None and d == _exe_dir(parent.get("exe"))
        if not (same_name or same_dir):
            break
        seen.add(parent["pid"])
        cur = parent
    k = base_key(cur)
    return k, k


class AppResolver:
    """Caches the app of each process, keyed by (pid, create_time)."""

    def __init__(self, cgroup_reader=read_cgroup):
        self._cache: dict[tuple[int, float], tuple[str, str]] = {}
        self._cg = cgroup_reader

    def assign(self, procs: dict[int, dict]) -> None:
        live = set()
        for pid, p in procs.items():
            key = (pid, p.get("started") or 0.0)
            live.add(key)
            if key not in self._cache:
                self._cache[key] = resolve_app(pid, procs, self._cg)
            p["app"], p["app_name"] = self._cache[key]
        for k in list(self._cache):
            if k not in live:
                del self._cache[k]
