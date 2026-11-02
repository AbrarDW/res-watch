"""Per-process CPU usage split by logical core (Linux only).

psutil has no per-process/per-core figure, so we read every thread's
/proc/<pid>/task/<tid>/stat: utime+stime (fields 14-15) and `processor`
(field 39, the CPU the thread last ran on). The CPU-time delta of each thread
between two samples is credited to that core. A thread that migrated mid
interval is credited to its last core, so this is an approximation, but the
per-core values always sum to the process's total CPU %.
"""
from __future__ import annotations

import os
import sys
import time

SUPPORTED = sys.platform.startswith("linux")
CLK_TCK = os.sysconf("SC_CLK_TCK") if SUPPORTED else 100


def parse_stat(text: str) -> tuple[int, int]:
    """Return (utime+stime ticks, last cpu) from a /proc/.../stat line."""
    # comm (field 2) may contain spaces or ')' so split after the LAST ')'
    rest = text[text.rindex(")") + 2:].split()
    # rest[0] is field 3 (state) -> field N is rest[N - 3]
    ticks = int(rest[11]) + int(rest[12])
    cpu = int(rest[36])
    return ticks, cpu


class PerCoreTracker:
    def __init__(self, ncpu: int | None = None, proc_root: str = "/proc"):
        self.ncpu = ncpu or os.cpu_count() or 1
        self.root = proc_root
        self._prev: dict[tuple[int, int], tuple[int, float]] = {}

    @property
    def supported(self) -> bool:
        return SUPPORTED

    def _threads(self, pid: int):
        base = f"{self.root}/{pid}/task"
        try:
            tids = os.listdir(base)
        except OSError:
            return
        for tid in tids:
            try:
                with open(f"{base}/{tid}/stat", "rb") as fh:
                    yield int(tid), parse_stat(fh.read().decode("utf-8", "replace"))
            except (OSError, ValueError, IndexError):
                continue

    def sample(self, pids, now: float | None = None) -> dict[int, list[float]]:
        """Per-core % for each pid since the previous call that included it."""
        if not SUPPORTED:
            return {}
        now = time.monotonic() if now is None else now
        out: dict[int, list[float]] = {}
        seen: dict[tuple[int, int], tuple[int, float]] = {}
        for pid in pids:
            cores = [0.0] * self.ncpu
            for tid, (ticks, cpu) in self._threads(pid):
                key = (pid, tid)
                seen[key] = (ticks, now)
                prev = self._prev.get(key)
                if prev is None:
                    continue
                dt = now - prev[1]
                d = ticks - prev[0]
                if dt <= 0 or d <= 0:
                    continue
                if 0 <= cpu < self.ncpu:
                    cores[cpu] += d / CLK_TCK / dt * 100.0
            out[pid] = [round(min(c, 100.0), 2) for c in cores]
        self._prev = seen  # forget threads of pids no longer watched
        return out
