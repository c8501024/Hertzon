"""What a benchmark result needs to say about the computer it was measured on, on Windows, macOS and Linux:
device model, CPU, cores, clock, memory (size, type, speed, modules), GPU, operating system, Python and
numerical libraries, power source and whether it is a virtual machine; plus a short measurement of the
memory bandwidth, which the operating systems do not report and which limits a sparse direct solver.

Nothing that identifies the person or the computer is read: no serial numbers, computer name, user name,
network addresses or paths. What cannot be read stays empty instead of guessed. Every query has a short
time limit, so a slow or missing tool only leaves its field empty.

python -m hertzon.solver.sysinfo prints it as JSON.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time

TIMEOUT = 8.0       # seconds per query


def _run(cmd: list[str], timeout: float = TIMEOUT) -> str:
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW: no console flashes up
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags,
                             encoding="utf-8", errors="replace")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _clean(text: str) -> str:
    """One line, no trademark noise, no repeated blanks."""
    text = re.sub(r"\((R|TM|tm|r)\)|®|™", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return "" if text.lower() in ("", "to be filled by o.e.m.", "default string", "system product name",
                                  "not specified", "none", "unknown", "o.e.m.") else text


def _ps_json(script: str):
    """Result of a PowerShell CIM query as JSON (Windows): a list of dicts, or []."""
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                script + " | ConvertTo-Json -Compress"], timeout=15)
    try:
        data = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


# ---- per operating system ---------------------------------------------------------------------
def _windows() -> dict:
    info: dict = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            info["cpu"] = _clean(winreg.QueryValueEx(k, "ProcessorNameString")[0])
            info["cpu_mhz"] = int(winreg.QueryValueEx(k, "~MHz")[0])
    except Exception:  # noqa: BLE001
        pass
    for cs in _ps_json("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,"
                       "HypervisorPresent,PCSystemType"):
        info["device"] = _clean(f"{_clean(cs.get('Manufacturer'))} {_clean(cs.get('Model'))}")
        info["virtual_machine"] = bool(cs.get("HypervisorPresent")) and \
            bool(re.search(r"virtual|vmware|kvm|qemu|xen|hyper-v", str(cs.get("Model")), re.I))
        info["device_type"] = {1: "desktop", 2: "laptop", 3: "workstation", 4: "server"}.get(cs.get("PCSystemType"), "")
    for p in _ps_json("Get-CimInstance Win32_Processor | Select-Object MaxClockSpeed")[:1]:
        if p.get("MaxClockSpeed"):
            info["cpu_max_mhz"] = int(p["MaxClockSpeed"])
    mods = _ps_json("Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,ConfiguredClockSpeed,"
                    "SMBIOSMemoryType")
    if mods:
        info["memory_modules"] = len(mods)
        speeds = [m.get("ConfiguredClockSpeed") or m.get("Speed") for m in mods if (m.get("ConfiguredClockSpeed") or m.get("Speed"))]
        if speeds:
            info["memory_speed_mts"] = int(max(speeds))
        types = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5", 35: "LPDDR5", 30: "LPDDR4"}
        t = {types.get(m.get("SMBIOSMemoryType")) for m in mods} - {None}
        if len(t) == 1:
            info["memory_type"] = t.pop()
    gpus = _ps_json("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM")
    info["gpus"] = [{"name": _clean(g.get("Name")),
                     **({"memory_gb": round(g["AdapterRAM"] / 2 ** 30, 1)} if g.get("AdapterRAM", 0) > 0 else {})}
                    for g in gpus if _clean(g.get("Name")) and "basic display" not in str(g.get("Name")).lower()]
    ver = sys.getwindowsversion()
    name = "Windows 11" if ver.build >= 22000 else f"Windows {platform.release()}"
    disp = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
    except Exception:  # noqa: BLE001
        pass
    info["os"] = f"{name} {disp}".strip() + f" (build {ver.build})"
    plan = _run(["powercfg", "/getactivescheme"])
    m = re.search(r"\((.+)\)\s*$", plan.strip())
    if m:
        info["power_plan"] = m.group(1)
    return info


def _sysctl(name: str) -> str:
    return _run(["sysctl", "-n", name]).strip()


def _macos() -> dict:
    info: dict = {"cpu": _clean(_sysctl("machdep.cpu.brand_string")), "os": f"macOS {platform.mac_ver()[0]}"}
    model = _sysctl("hw.model")
    hw = _run(["system_profiler", "-json", "SPHardwareDataType", "SPDisplaysDataType", "SPMemoryDataType"],
              timeout=20)
    try:
        data = json.loads(hw) if hw else {}
    except ValueError:
        data = {}
    h = (data.get("SPHardwareDataType") or [{}])[0]
    info["device"] = _clean(f"{h.get('machine_name', '')} ({model})" if h.get("machine_name") else model)
    info["device_type"] = "laptop" if "book" in (h.get("machine_name", "") + model).lower() else "desktop"
    if not info["cpu"]:
        info["cpu"] = _clean(h.get("chip_type", ""))
    for lvl, key in ((0, "performance_cores"), (1, "efficiency_cores")):
        v = _sysctl(f"hw.perflevel{lvl}.physicalcpu")
        if v.isdigit():
            info[key] = int(v)
    mem = (data.get("SPMemoryDataType") or [{}])[0]
    if mem.get("dimm_type"):
        info["memory_type"] = _clean(mem["dimm_type"])
    gpus = []
    for g in data.get("SPDisplaysDataType") or []:
        name = _clean(g.get("sppci_model") or g.get("_name"))
        if name:
            gpus.append({"name": name, **({"cores": int(g["sppci_cores"])} if str(g.get("sppci_cores", "")).isdigit() else {})})
    info["gpus"] = gpus
    info["virtual_machine"] = _sysctl("kern.hv_vmm_present") == "1"
    batt = _run(["pmset", "-g", "batt"])
    if batt:
        info["power_source"] = "battery" if "Battery Power" in batt else "AC"
    return info


def _linux() -> dict:
    info: dict = {}
    cpuinfo = _read("/proc/cpuinfo")
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    if m:
        info["cpu"] = _clean(m.group(1))
    lscpu = _run(["lscpu"])
    if not info.get("cpu"):
        m = re.search(r"^Model name:\s*(.+)$", lscpu, re.M)
        if m:
            info["cpu"] = _clean(m.group(1))
    m = re.search(r"^CPU max MHz:\s*([0-9.]+)", lscpu, re.M)
    if m:
        info["cpu_max_mhz"] = int(float(m.group(1)))
    vendor, product = _clean(_read("/sys/class/dmi/id/sys_vendor")), _clean(_read("/sys/class/dmi/id/product_name"))
    info["device"] = _clean(f"{vendor} {product}")
    chassis = _read("/sys/class/dmi/id/chassis_type")
    if chassis.isdigit():
        c = int(chassis)
        info["device_type"] = "laptop" if c in (8, 9, 10, 14, 31, 32) else "server" if c in (17, 23, 28) \
            else "desktop" if c in (3, 4, 5, 6, 7, 13, 15, 16, 35, 36) else ""
    osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', _read("/etc/os-release"), re.M))
    info["os"] = f"{osr.get('PRETTY_NAME') or 'Linux'}, kernel {platform.release().split('-')[0]}"
    virt = _run(["systemd-detect-virt"]).strip()
    info["virtual_machine"] = bool(virt) and virt != "none"
    if info["virtual_machine"]:
        info["virtualization"] = virt
    gpus = []
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    for line in smi.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0]:
            g = {"name": _clean(parts[0])}
            if len(parts) > 1 and parts[1].replace(".", "").isdigit():
                g["memory_gb"] = round(float(parts[1]) / 1024, 1)
            gpus.append(g)
    if not gpus:
        for line in _run(["lspci"]).splitlines():
            if re.search(r"VGA compatible|3D controller|Display controller", line):
                gpus.append({"name": _clean(line.split(": ", 1)[-1])})
    info["gpus"] = gpus
    for ps in ("/sys/class/power_supply/AC/online", "/sys/class/power_supply/ACAD/online",
               "/sys/class/power_supply/ADP1/online"):
        v = _read(ps)
        if v in ("0", "1"):
            info["power_source"] = "AC" if v == "1" else "battery"
            break
    return info


def cpu_name() -> str:
    """The CPU's marketing name, quickly (no PowerShell or system_profiler): for the window's computer line."""
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return _clean(winreg.QueryValueEx(k, "ProcessorNameString")[0])
        if sys.platform == "darwin":
            return _clean(_sysctl("machdep.cpu.brand_string"))
        m = re.search(r"^model name\s*:\s*(.+)$", _read("/proc/cpuinfo"), re.M)
        if m:
            return _clean(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return ""


# ---- libraries and the measurement -------------------------------------------------------------
def libraries() -> dict:
    from importlib import metadata

    from .. import __version__
    out = {"hertzon": __version__, "python": platform.python_version()}
    for name in ("emerge", "numpy", "scipy"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    out["blas"] = blas()
    return out


def blas() -> str:
    """The BLAS that NumPy uses (MKL, OpenBLAS, Accelerate)."""
    try:
        import numpy as np
        cfg = np.show_config(mode="dicts")
        b = cfg.get("Build Dependencies", {}).get("blas", {})
        return _clean(f"{b.get('name', '')} {b.get('version', '')}")
    except Exception:  # noqa: BLE001
        return ""


def memory_bandwidth(seconds: float = 1.5) -> float:
    """Measured memory bandwidth in GB/s on one core: the best of repeated copies of a 256 MB array (read and
    write, as STREAM's copy counts it). One core shows how fast data reaches a core; the solver's
    factorization reads its matrices the same way."""
    try:
        import numpy as np
        n = 32 * 2 ** 20                     # 32 Mi doubles = 256 MB, far beyond every cache
        a = np.ones(n)
        b = np.empty_like(a)
        np.copyto(b, a)
        best, t_end = 0.0, time.perf_counter() + seconds
        while time.perf_counter() < t_end:
            t = time.perf_counter()
            np.copyto(b, a)
            dt = time.perf_counter() - t
            if dt > 0:
                best = max(best, 2 * a.nbytes / dt / 1e9)
        return round(best, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def collect(measure: bool = True) -> dict:
    """The whole system description for a benchmark result."""
    info: dict = {"platform": {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux"),
                  "arch": platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")}
    try:
        info.update({"win32": _windows, "darwin": _macos}.get(sys.platform, _linux)())
    except Exception:  # noqa: BLE001
        pass
    info.setdefault("cpu", _clean(platform.processor()) or platform.machine())
    info["logical_cores"] = os.cpu_count() or 1
    try:
        import psutil
        info["physical_cores"] = psutil.cpu_count(logical=False) or info["logical_cores"]
        info["memory_gb"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
        if "power_source" not in info:
            batt = psutil.sensors_battery()
            if batt is not None:
                info["power_source"] = "AC" if batt.power_plugged else "battery"
    except Exception:  # noqa: BLE001
        pass
    info["software"] = libraries()
    if measure:
        info["memory_bandwidth_gbs"] = memory_bandwidth()
    return {k: v for k, v in info.items() if v not in ("", None, [])}


def summary(info: dict) -> str:
    """One line for the dialog: CPU, cores, memory, GPU, system."""
    parts = [info.get("cpu", "")]
    if info.get("physical_cores"):
        parts.append(f"{info['physical_cores']} cores / {info['logical_cores']} threads")
    if info.get("memory_gb"):
        parts.append(f"{info['memory_gb']:g} GB" + (f" {info['memory_type']}" if info.get("memory_type") else ""))
    if info.get("gpus"):
        parts.append(", ".join(g["name"] for g in info["gpus"]))
    parts.append(info.get("os", info.get("platform", "")))
    return ", ".join(p for p in parts if p)


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
