import os
import subprocess
import sys
import time

import pytest

from reswatch.apps import AppResolver, cgroup_app, normalize_name, resolve_app
from reswatch.gpu import NullGpu
from reswatch.monitor import Collector


@pytest.mark.parametrize("raw,want", [
    ("Google Chrome Helper (Renderer)", "google chrome"),
    ("Slack Helper", "slack"),
    ("python3.12", "python"),
    ("python3", "python"),
    ("kworker/3:1", "kworker"),
    ("Code.exe", "code"),
    ("gcc-12.2", "gcc"),
    ("nodejs", "node"),
    ("x264", "x264"),
    ("agent (deleted)", "agent"),
])
def test_normalize_name(raw, want):
    assert normalize_name(raw) == want


@pytest.mark.parametrize("text,want", [
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-firefox-4242.scope", "firefox"),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/snap.slack.slack-1b2c3d.scope", "slack"),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-org.mozilla.Thunderbird-99.scope", "thunderbird"),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome\\x2dterminal\\x2dserver.slice/vte-spawn-0a1b.scope", None),
    ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-code-777.scope", None),
    ("0::/system.slice/docker.service", None),
    ("", None),
])
def test_cgroup_app(text, want):
    assert cgroup_app(text) == want


def test_tree_fallback_merges_helpers_in_same_install_dir():
    procs = {
        100: {"pid": 100, "ppid": 1, "name": "chrome", "exe": "/opt/google/chrome/chrome"},
        101: {"pid": 101, "ppid": 100, "name": "chrome", "exe": "/opt/google/chrome/chrome"},
        102: {"pid": 102, "ppid": 100, "name": "chrome_crashpad", "exe": "/opt/google/chrome/chrome_crashpad_handler"},
        200: {"pid": 200, "ppid": 1, "name": "bash", "exe": "/usr/bin/bash"},
        201: {"pid": 201, "ppid": 200, "name": "python3", "exe": "/usr/bin/python3.12"},
        300: {"pid": 300, "ppid": 1, "name": "x", "exe": None, "kernel": True},
    }
    no_cg = lambda pid: ""
    assert resolve_app(102, procs, no_cg)[0] == "chrome"
    assert resolve_app(101, procs, no_cg)[0] == "chrome"
    # /usr/bin is generic: python started from bash is not part of "bash"
    assert resolve_app(201, procs, no_cg)[0] == "python"
    assert resolve_app(300, procs, no_cg)[0] == "kernel"
    mac = {1: {"pid": 1, "ppid": 0, "name": "Google Chrome Helper (GPU)",
               "exe": "/Applications/Google Chrome.app/Contents/Frameworks/x/Helpers/Google Chrome Helper (GPU)"}}
    assert resolve_app(1, mac, no_cg) == ("google chrome", "Google Chrome")


def test_resolver_caches_per_pid():
    calls = []
    r = AppResolver(cgroup_reader=lambda pid: calls.append(pid) or "")
    procs = {5: {"pid": 5, "ppid": 1, "name": "foo", "exe": "/opt/foo/foo", "started": 1.0}}
    r.assign(procs)
    r.assign(procs)
    assert calls == [5] and procs[5]["app"] == "foo"


def test_spawned_children_form_one_app():
    code = "import subprocess,sys,time\n" \
           "ps=[subprocess.Popen([sys.executable,'-c','while True: pass']) for _ in range(3)]\n" \
           "time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code])
    try:
        import psutil
        # wait until all three children run: starting Python processes is slow on Windows runners,
        # and measuring while they start makes any CPU threshold timing-dependent
        deadline = time.time() + 20
        while len(psutil.Process(parent.pid).children()) < 3 and time.time() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)
        c = Collector(gpu=NullGpu())
        c.tick()
        time.sleep(1.0)
        snap = c.tick()
        kids = [p for p in snap["procs"].values() if p["ppid"] == parent.pid]
        assert len(kids) == 3
        assert {p["app"] for p in kids} == {"python"}
        ent = c.entity(snap, "pid", parent.pid, children=True)
        assert ent["count"] == 4
        # the family total is exactly the sum of its members ...
        members = [parent.pid] + [k["pid"] for k in kids]
        assert ent["cpu"] == pytest.approx(sum(snap["procs"][m]["cpu"] or 0 for m in members), abs=0.05)
        # ... and the three busy loops are clearly busy (loose floor: CI runners share their cores)
        assert ent["cpu"] > 100
    finally:
        for p in __import__("psutil").Process(parent.pid).children():
            p.kill()
        parent.kill()


def test_windows_display_name_is_used_but_key_unchanged(monkeypatch):
    from reswatch import apps
    monkeypatch.setattr(apps, "IS_WINDOWS", True)
    monkeypatch.setattr(apps, "windows_product_name", lambda exe: "Microsoft Edge" if exe and "msedge" in exe else None)
    procs = {10: {"pid": 10, "ppid": 1, "name": "msedge.exe", "exe": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe"},
             11: {"pid": 11, "ppid": 10, "name": "msedge.exe", "exe": "C:\\Program Files\\Microsoft\\Edge\\msedge.exe"},
             20: {"pid": 20, "ppid": 1, "name": "tool.exe", "exe": "C:\\tools\\tool.exe"}}
    no_cg = lambda pid: ""
    assert apps.resolve_app(11, procs, no_cg) == ("msedge", "Microsoft Edge")
    assert apps.resolve_app(20, procs, no_cg) == ("tool", "tool")     # no version info: exe name


def test_windows_product_name_is_none_off_windows():
    from reswatch import apps
    if not apps.IS_WINDOWS:
        assert apps.windows_product_name(sys.executable) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version-info API")
def test_windows_product_name_reads_real_files():
    from reswatch import apps
    notepad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
    name = apps.windows_product_name(notepad)
    assert name and "notepad" in name.lower(), name
    assert apps.windows_product_name(sys.executable)          # python.exe has version info ("Python")
    assert apps.windows_product_name(r"C:\does\not\exist.exe") is None
