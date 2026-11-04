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
        time.sleep(1.0)
        c = Collector(gpu=NullGpu())
        c.tick()
        time.sleep(1.0)
        snap = c.tick()
        kids = [p for p in snap["procs"].values() if p["ppid"] == parent.pid]
        assert len(kids) == 3
        assert {p["app"] for p in kids} == {"python"}
        ent = c.entity(snap, "pid", parent.pid, children=True)
        assert ent["count"] == 4
        assert ent["cpu"] > 50 * min(3, os.cpu_count() or 1)  # three burn loops (CI runners can be busy)
    finally:
        for p in __import__("psutil").Process(parent.pid).children():
            p.kill()
        parent.kill()
