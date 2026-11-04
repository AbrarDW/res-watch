"""Process / system sampling.

A single Collector takes a snapshot of every process each tick. CPU % and I/O
rates are computed from deltas against the previous tick, so the collector is
the only thing that needs to keep state. Per-core attribution (expensive: it
reads every thread) is only done for the PIDs someone is watching.
"""
from __future__ import annotations

import getpass
import os
import platform
import socket
import sys
import threading
import time

import psutil

from . import gpu as gpu_mod
from .apps import AppResolver, base_key
from .percore import PerCoreTracker

IS_LINUX = sys.platform.startswith("linux")
SYSTEM_USERS = {"root", "system", "nt authority\\system", "local service", "network service",
                "nt authority\\local service", "nt authority\\network service"}
MAX_MEMBERS = 200


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return str(os.getuid()) if hasattr(os, "getuid") else "?"


class Collector:
    def __init__(self, gpu=None, percore: PerCoreTracker | None = None, resolver: AppResolver | None = None):
        self.lock = threading.Lock()
        self.gpu = gpu if gpu is not None else gpu_mod.detect()
        self.ncpu = psutil.cpu_count(logical=True) or 1
        self.ncpu_phys = psutil.cpu_count(logical=False) or self.ncpu
        self.percore = percore or PerCoreTracker(self.ncpu)
        self.resolver = resolver or AppResolver()
        self.user = _current_user()
        self._static: dict[tuple[int, float], dict] = {}
        self._prev: dict[int, tuple[float, float, int | None, int | None]] = {}
        self.snapshot: dict = {"ts": 0, "system": {}, "procs": {}, "percore": {}}
        self.total_mem = psutil.virtual_memory().total
        psutil.cpu_percent(percpu=True)  # prime system counters

    # ------------------------------------------------------------------ #
    def _static_info(self, p: psutil.Process, started: float) -> dict:
        key = (p.pid, started)
        info = self._static.get(key)
        if info is not None:
            return info
        info = {"name": "", "exe": None, "user": None, "cmd": "", "uid": None, "denied": False}
        try:
            info["name"] = p.name()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            info["denied"] = True
        for attr, fn in (("exe", p.exe), ("user", p.username)):
            try:
                info[attr] = fn() or None
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                pass
            except KeyError:  # uid without passwd entry (e.g. inside a container)
                pass
        try:
            info["cmd"] = " ".join(p.cmdline())[:400]
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
        if hasattr(p, "uids"):
            try:
                info["uid"] = p.uids().real
            except (psutil.AccessDenied, psutil.ZombieProcess):
                pass
        if info["user"] is None and info["uid"] is not None:
            info["user"] = str(info["uid"])
        self._static[key] = info
        return info

    def _is_system(self, rec: dict) -> bool:
        if rec["kernel"]:
            return True
        user = (rec.get("user") or "").lower()
        if user == self.user.lower():
            return False
        if user in SYSTEM_USERS:
            return True
        uid = rec.get("uid")
        return uid is not None and uid < 1000

    # ------------------------------------------------------------------ #
    def tick(self, specs=()) -> dict:
        """Take a snapshot. `specs` = [(kind, id, children)] being watched."""
        with self.lock:
            return self._tick(specs)

    def _tick(self, specs) -> dict:
        now = time.monotonic()
        procs: dict[int, dict] = {}
        prev = self._prev
        cur_prev = {}
        live_static = set()
        for p in psutil.process_iter():
            try:
                with p.oneshot():
                    started = p.create_time()
                    ppid = p.ppid()
                    st = self._static_info(p, started)
                    live_static.add((p.pid, started))
                    rec = {
                        "pid": p.pid, "ppid": ppid, "name": st["name"], "exe": st["exe"],
                        "user": st["user"], "uid": st["uid"], "cmd": st["cmd"], "started": started,
                        "cpu": None, "rss": None, "mem": None, "threads": None, "status": None,
                        "ior": None, "iow": None, "gpu": None, "gmem": None, "denied": st["denied"],
                    }
                    rec["kernel"] = bool(IS_LINUX and (p.pid == 2 or ppid == 2))
                    try:
                        rec["status"] = p.status()
                    except psutil.Error:
                        pass
                    cpu_total = None
                    try:
                        t = p.cpu_times()
                        cpu_total = t.user + t.system
                    except psutil.AccessDenied:
                        rec["denied"] = True
                    try:
                        mi = p.memory_info()
                        rec["rss"] = mi.rss
                        rec["mem"] = round(mi.rss / self.total_mem * 100, 2) if self.total_mem else None
                    except psutil.AccessDenied:
                        rec["denied"] = True
                    try:
                        rec["threads"] = p.num_threads()
                    except psutil.AccessDenied:
                        pass
                    rb = wb = None
                    if hasattr(p, "io_counters"):
                        try:
                            io = p.io_counters()
                            rb, wb = io.read_bytes, io.write_bytes
                        except (psutil.AccessDenied, NotImplementedError, OSError):
                            pass
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                continue
            pv = prev.get(p.pid)
            if pv and pv[0] == started:
                dt = now - pv[1]
                if dt > 0:
                    if cpu_total is not None and pv[2] is not None:
                        rec["cpu"] = round(max(0.0, (cpu_total - pv[2]) / dt * 100), 2)
                    if rb is not None and pv[3] is not None:
                        rec["ior"] = max(0, int((rb - pv[3][0]) / dt))
                        rec["iow"] = max(0, int((wb - pv[3][1]) / dt))
            elif cpu_total is not None:
                rec["cpu"] = 0.0
            cur_prev[p.pid] = (started, now, cpu_total, (rb, wb) if rb is not None else None)
            procs[p.pid] = rec
        # tracking state
        self._prev = {pid: (v[0], v[1], v[2], v[3]) for pid, v in cur_prev.items()}
        for k in list(self._static):
            if k not in live_static:
                del self._static[k]

        for rec in procs.values():
            rec["system"] = self._is_system(rec)
            rec["exe_key"] = "kernel" if rec["kernel"] else base_key(rec)
        self.resolver.assign(procs)

        gpu_procs = self.gpu.per_process()
        for pid, g in gpu_procs.items():
            rec = procs.get(pid)
            if rec:
                rec["gpu"] = g.get("util")
                rec["gmem"] = g.get("mem")

        # per-core only for watched pids
        watched = set()
        for kind, ident, children in specs:
            watched.update(self.resolve_pids(procs, kind, ident, children))
        percore = self.percore.sample(sorted(watched), now) if watched else {}

        system = self._system(procs)
        self.snapshot = {"ts": time.time(), "system": system, "procs": procs, "percore": percore}
        return self.snapshot

    def _system(self, procs) -> dict:
        per_core = psutil.cpu_percent(percpu=True)
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        freq = None
        try:
            f = psutil.cpu_freq()
            freq = round(f.current) if f else None
        except Exception:
            pass
        return {
            "cpu": round(sum(per_core) / max(1, len(per_core)), 1),
            "per_core": per_core,
            "freq": freq,
            "ram_used": vm.total - vm.available,
            "ram_total": vm.total,
            "ram_pct": vm.percent,
            "swap_used": sw.used,
            "swap_total": sw.total,
            "cores": self.ncpu,
            "cores_physical": self.ncpu_phys,
            "proc_count": len(procs),
            "gpus": self.gpu.devices(),
        }

    def meta(self) -> dict:
        return {
            "hostname": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "os": sys.platform,
            "user": self.user,
            "cores": self.ncpu,
            "cores_physical": self.ncpu_phys,
            "percore_supported": self.percore.supported,
            "gpu": self.gpu.info(),
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def descendants(procs: dict, pid: int) -> list[int]:
        kids: dict[int, list[int]] = {}
        for p in procs.values():
            kids.setdefault(p["ppid"], []).append(p["pid"])
        out, stack = [], [pid]
        while stack:
            for c in kids.get(stack.pop(), []):
                if c not in out and c != pid:
                    out.append(c)
                    stack.append(c)
        return out

    # group kinds a client can subscribe to, and the process field each one matches on
    GROUP_FIELDS = {"app": "app", "exe": "exe_key", "user": "user"}

    def resolve_pids(self, procs: dict, kind: str, ident, children: bool = False) -> list[int]:
        field = self.GROUP_FIELDS.get(kind)
        if field:
            return [pid for pid, p in procs.items() if (p.get(field) or "?") == ident]
        pid = int(ident)
        if pid not in procs:
            return []
        return [pid] + (self.descendants(procs, pid) if children else [])

    def entity(self, snap: dict, kind: str, ident, children: bool = False) -> dict:
        procs, percore = snap["procs"], snap["percore"]
        pids = self.resolve_pids(procs, kind, ident, children)
        ent = {"id": f"{kind}:{ident}", "kind": kind, "key": ident, "count": len(pids)}
        if not pids:
            ent["gone"] = True
            return ent

        def total(field):
            vals = [procs[p][field] for p in pids if procs[p].get(field) is not None]
            return round(sum(vals), 2) if vals else None

        ent.update({
            "gone": False,
            "cpu": total("cpu"), "rss": total("rss"), "mem": total("mem"),
            "threads": total("threads"), "ior": total("ior"), "iow": total("iow"),
            "gpu": total("gpu"), "gmem": total("gmem"),
        })
        cores = None
        if self.percore.supported:
            cores = [0.0] * self.ncpu
            for p in pids:
                for i, v in enumerate(percore.get(p) or ()):
                    cores[i] += v
            cores = [round(c, 2) for c in cores]
        ent["per_core"] = cores
        if kind == "pid":
            p = procs[int(ident)]
            ent.update({k: p[k] for k in ("name", "user", "cmd", "status", "started", "ppid", "app", "exe")})
            ent["pid"] = p["pid"]
            ent["denied"] = p["denied"]
        else:
            first = procs[pids[0]]
            ent["name"] = (first.get("app_name") or ident) if kind == "app" else ident
            ent["users"] = sorted({procs[p]["user"] or "?" for p in pids})
            ent["started"] = min(procs[p]["started"] for p in pids)
        members = sorted(pids, key=lambda p: (procs[p]["cpu"] or 0, procs[p]["rss"] or 0), reverse=True)
        ent["members"] = [
            {"pid": p, "name": procs[p]["name"], "cpu": procs[p]["cpu"], "rss": procs[p]["rss"],
             "gpu": procs[p]["gpu"], "gmem": procs[p]["gmem"], "cmd": procs[p]["cmd"][:120]}
            for p in members[:MAX_MEMBERS]
        ]
        return ent

    # ------------------------------------------------------------------ #
    def process_list(self, snap: dict | None = None) -> list[dict]:
        snap = snap or self.snapshot
        keep = ("pid", "ppid", "name", "user", "cpu", "rss", "mem", "threads", "status", "gpu", "gmem",
                "app", "app_name", "exe_key", "kernel", "system", "denied", "cmd")
        return [{k: p.get(k) for k in keep} for p in snap["procs"].values()]

    def app_list(self, snap: dict | None = None) -> list[dict]:
        snap = snap or self.snapshot
        apps: dict[str, dict] = {}
        for p in snap["procs"].values():
            a = apps.setdefault(p["app"], {"key": p["app"], "name": p.get("app_name") or p["app"], "count": 0,
                                           "cpu": 0.0, "rss": 0, "gpu": None, "gmem": None, "users": set(),
                                           "pids": [], "system": True, "kernel": p["kernel"]})
            a["count"] += 1
            a["cpu"] += p["cpu"] or 0
            a["rss"] += p["rss"] or 0
            if p["gpu"] is not None:
                a["gpu"] = (a["gpu"] or 0) + p["gpu"]
            if p["gmem"] is not None:
                a["gmem"] = (a["gmem"] or 0) + p["gmem"]
            a["users"].add(p["user"] or "?")
            a["pids"].append(p["pid"])
            a["system"] = a["system"] and p["system"]
        for a in apps.values():
            a["users"] = sorted(a["users"])
            a["cpu"] = round(a["cpu"], 2)
        return sorted(apps.values(), key=lambda a: a["cpu"], reverse=True)

    def process_detail(self, pid: int) -> dict | None:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                d = {"pid": pid, "name": p.name(), "ppid": p.ppid(), "started": p.create_time(),
                     "status": p.status()}
                for attr in ("exe", "cwd", "username", "nice", "num_fds", "num_threads"):
                    fn = getattr(p, attr, None)
                    if fn is None:
                        continue
                    try:
                        d[attr] = fn()
                    except (psutil.AccessDenied, psutil.ZombieProcess, OSError, KeyError):
                        d[attr] = None
                try:
                    d["cmdline"] = p.cmdline()
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    d["cmdline"] = None
        except psutil.NoSuchProcess:
            return None
        rec = self.snapshot["procs"].get(pid)
        if rec:
            d["app"] = rec.get("app")
            d["app_name"] = rec.get("app_name")
        return d
