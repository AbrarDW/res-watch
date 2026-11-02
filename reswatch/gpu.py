"""GPU metrics: NVIDIA (NVML) -> AMD (amdsmi, best effort) -> none.

Every backend exposes:
    info()         -> {"available": bool, "backend": str, "reason": str}
    devices()      -> [{index, name, util, mem_util, mem_used, mem_total,
                       temp, power, power_limit, clock, fan, enc, dec}]
    per_process()  -> {pid: {"util": float|None, "mem": int}}
Values a driver does not report are None. Nothing here raises.
"""
from __future__ import annotations

import logging

log = logging.getLogger("reswatch.gpu")


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception:  # driver/API differences: treat as "not reported"
        return default


class NullGpu:
    backend = "none"

    def __init__(self, reason: str = "No supported GPU driver found (NVIDIA NVML or AMD SMI)."):
        self.reason = reason

    def info(self):
        return {"available": False, "backend": self.backend, "reason": self.reason}

    def devices(self):
        return []

    def per_process(self):
        return {}


class NvmlGpu:
    backend = "nvml"

    def __init__(self):
        import pynvml  # provided by the nvidia-ml-py package

        self.n = pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            raise RuntimeError("NVML found no devices")
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
        self._last_ts = [0] * count
        drv = _safe(pynvml.nvmlSystemGetDriverVersion, default="")
        self.driver = drv.decode() if isinstance(drv, bytes) else (drv or "")

    def info(self):
        return {"available": True, "backend": self.backend, "reason": "", "driver": self.driver}

    def devices(self):
        n, out = self.n, []
        for i, h in enumerate(self.handles):
            name = _safe(n.nvmlDeviceGetName, h, default="GPU")
            name = name.decode() if isinstance(name, bytes) else name
            util = _safe(n.nvmlDeviceGetUtilizationRates, h)
            mem = _safe(n.nvmlDeviceGetMemoryInfo, h)
            enc = _safe(n.nvmlDeviceGetEncoderUtilization, h)
            dec = _safe(n.nvmlDeviceGetDecoderUtilization, h)
            power = _safe(n.nvmlDeviceGetPowerUsage, h)
            limit = _safe(n.nvmlDeviceGetEnforcedPowerLimit, h)
            out.append({
                "index": i,
                "name": name,
                "util": util.gpu if util else None,
                "mem_util": util.memory if util else None,
                "mem_used": mem.used if mem else None,
                "mem_total": mem.total if mem else None,
                "temp": _safe(n.nvmlDeviceGetTemperature, h, n.NVML_TEMPERATURE_GPU),
                "power": power / 1000 if power is not None else None,
                "power_limit": limit / 1000 if limit is not None else None,
                "clock": _safe(n.nvmlDeviceGetClockInfo, h, n.NVML_CLOCK_GRAPHICS),
                "fan": _safe(n.nvmlDeviceGetFanSpeed, h),
                "enc": enc[0] if enc else None,
                "dec": dec[0] if dec else None,
            })
        return out

    def per_process(self):
        n, out = self.n, {}
        for i, h in enumerate(self.handles):
            for getter in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
                for p in _safe(getattr(n, getter, lambda _h: []), h, default=[]) or []:
                    d = out.setdefault(int(p.pid), {"util": None, "mem": 0})
                    used = getattr(p, "usedGpuMemory", None)
                    if isinstance(used, int) and used > 0:
                        d["mem"] += used
            samples = _safe(n.nvmlDeviceGetProcessUtilization, h, self._last_ts[i], default=None)
            if samples:
                self._last_ts[i] = max(s.timeStamp for s in samples)
                for s in samples:
                    d = out.setdefault(int(s.pid), {"util": None, "mem": 0})
                    d["util"] = (d["util"] or 0) + float(s.smUtil)
        return out


class AmdGpu:
    """Best-effort AMD support via the `amdsmi` Python package (ROCm 6+)."""

    backend = "amdsmi"

    def __init__(self):
        import amdsmi

        self.a = amdsmi
        amdsmi.amdsmi_init()
        self.handles = amdsmi.amdsmi_get_processor_handles()
        if not self.handles:
            raise RuntimeError("amdsmi found no devices")

    def info(self):
        return {"available": True, "backend": self.backend, "reason": ""}

    def devices(self):
        a, out = self.a, []
        for i, h in enumerate(self.handles):
            act = _safe(a.amdsmi_get_gpu_activity, h, default={}) or {}
            vram = _safe(a.amdsmi_get_gpu_vram_usage, h, default={}) or {}
            asic = _safe(a.amdsmi_get_gpu_asic_info, h, default={}) or {}
            mb = 1024 * 1024
            out.append({
                "index": i, "name": asic.get("market_name") or "AMD GPU",
                "util": act.get("gfx_activity"), "mem_util": act.get("umc_activity"),
                "mem_used": vram["vram_used"] * mb if "vram_used" in vram else None,
                "mem_total": vram["vram_total"] * mb if "vram_total" in vram else None,
                "temp": None, "power": None, "power_limit": None, "clock": None, "fan": None,
                "enc": None, "dec": act.get("mm_activity"),
            })
        return out

    def per_process(self):
        a, out = self.a, {}
        for h in self.handles:
            for proc in _safe(a.amdsmi_get_gpu_process_list, h, default=[]) or []:
                info = proc if isinstance(proc, dict) else _safe(a.amdsmi_get_gpu_process_info, h, proc, default={})
                if not info or "pid" not in info:
                    continue
                mem = info.get("mem") or (info.get("memory_usage") or {}).get("vram_mem") or 0
                d = out.setdefault(int(info["pid"]), {"util": None, "mem": 0})
                d["mem"] += int(mem or 0)
        return out


def detect():
    reasons = []
    for cls in (NvmlGpu, AmdGpu):
        try:
            return cls()
        except ModuleNotFoundError as e:
            reasons.append(f"{cls.backend}: python package not installed ({e.name})")
        except Exception as e:  # driver missing, no devices, ...
            reasons.append(f"{cls.backend}: {e}")
    log.info("GPU metrics unavailable: %s", "; ".join(reasons))
    return NullGpu("No NVIDIA or AMD GPU driver found. GPU metrics are unavailable on this machine.")
