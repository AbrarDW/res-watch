import os
import subprocess
import sys
import time

from reswatch.gpu import NullGpu
from reswatch.monitor import Collector


def make():
    return Collector(gpu=NullGpu())


def test_snapshot_contains_own_process():
    c = make()
    c.tick()
    snap = c.tick()
    me = snap["procs"][os.getpid()]
    assert me["rss"] > 0
    assert me["cpu"] is not None
    assert me["app"] == "python"
    sysinfo = snap["system"]
    assert len(sysinfo["per_core"]) == c.ncpu
    assert sysinfo["ram_total"] > 0
    assert sysinfo["gpus"] == []


def test_entity_fields_and_gone_pid():
    c = make()
    snap = c.tick()
    e = c.entity(snap, "pid", os.getpid())
    for k in ("cpu", "rss", "threads", "per_core", "name", "members", "gone"):
        assert k in e
    assert e["gone"] is False
    gone = c.entity(snap, "pid", 2 ** 22 + 12345)
    assert gone["gone"] is True


def test_children_and_app_aggregation():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        c = make()
        time.sleep(0.2)
        snap = c.tick()
        solo = c.entity(snap, "pid", os.getpid(), children=False)
        fam = c.entity(snap, "pid", os.getpid(), children=True)
        assert fam["count"] == solo["count"] + 1
        assert fam["rss"] > solo["rss"]
        apps = {a["key"]: a for a in c.app_list(snap)}
        assert child.pid in apps["python"]["pids"]
    finally:
        child.kill()


def test_process_detail():
    c = make()
    c.tick()
    d = c.process_detail(os.getpid())
    assert d["pid"] == os.getpid() and d["cmdline"]
    assert c.process_detail(2 ** 22 + 12345) is None


def test_group_kinds_exe_and_user():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        c = make()
        time.sleep(0.2)
        snap = c.tick()
        me = snap["procs"][os.getpid()]
        assert me["exe_key"] == "python"
        by_exe = c.entity(snap, "exe", "python")
        assert by_exe["count"] >= 2 and by_exe["name"] == "python"
        assert child.pid in [m["pid"] for m in by_exe["members"]]
        by_user = c.entity(snap, "user", me["user"])
        assert by_user["count"] >= by_exe["count"] - 1 and by_user["name"] == me["user"]
        assert c.entity(snap, "user", "no-such-user-xyz")["gone"] is True
        listed = {p["pid"]: p for p in c.process_list(snap)}
        assert listed[os.getpid()]["exe_key"] == "python"
    finally:
        child.kill()
