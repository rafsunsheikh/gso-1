"""System resource monitor (CPU / GPU / RAM) for macOS — no extra deps.

Uses native tools that work without sudo:
  * `top -l 2`   -> system CPU%, PhysMem, and per-process CPU%/MEM (2nd sample)
  * `ioreg`      -> Apple Silicon GPU "Device Utilization %" + GPU memory in use
  * `sysctl`     -> logical CPU count, total physical memory

Sampling `top` blocks ~2-3s, so a background thread keeps a cached snapshot
fresh while the panel is being watched; the API returns the cache instantly.
Per-process GPU usage is NOT exposed by macOS without elevated privileges, so
GPU is reported system-wide only (with the model offloaded via -ngl, that
activity is essentially the LLM).
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Optional

_NCPU = 1
_TOTAL_MEM = 0
try:
    _NCPU = int(subprocess.run(["sysctl", "-n", "hw.logicalcpu"],
                               capture_output=True, text=True).stdout.strip() or "1")
    _TOTAL_MEM = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                    capture_output=True, text=True).stdout.strip() or "0")
except Exception:
    pass

_lock = threading.Lock()
_snapshot: Optional[dict] = None
_last_access = 0.0
_llm_pid: Optional[int] = None
_thread: Optional[threading.Thread] = None


def _to_bytes(s: str) -> int:
    """Parse top memory strings like '5385M', '12G', '49M', '2231M+'."""
    s = s.strip().rstrip("+-")
    m = re.match(r"([\d.]+)\s*([KMGTB]?)", s)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2)
    mult = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "": 1}
    return int(val * mult.get(unit, 1))


def _gpu() -> dict:
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return {"available": False}
    # Read util + mem from the SAME PerformanceStatistics block so they're
    # consistent; if several accelerators report, take the one with the
    # highest utilization.
    best = None
    for block in out.split("PerformanceStatistics"):
        um = re.search(r'"Device Utilization %"=(\d+)', block)
        if not um:
            continue
        mm = re.search(r'"In use system memory"=(\d+)', block)
        entry = {"util": int(um.group(1)), "mem_bytes": int(mm.group(1)) if mm else 0}
        if best is None or entry["util"] > best["util"]:
            best = entry
    if best is None:
        return {"available": False}
    best["available"] = True
    return best


def _collect() -> dict:
    pid = _llm_pid
    cpu_user = cpu_sys = cpu_idle = 0.0
    mem_used = mem_unused = mem_wired = 0
    procs: list[dict] = []

    try:
        # Sort by memory so the real RAM consumers (incl. the GPU-offloaded
        # llama-server) are captured; the 2nd sample still gives live CPU%.
        out = subprocess.run(
            ["top", "-l", "2", "-o", "mem", "-n", "12",
             "-stats", "pid,cpu,mem,command"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        out = ""

    lines = out.splitlines()
    # Use the LAST CPU usage / PhysMem lines (the 2nd, live sample).
    for ln in lines:
        cm = re.match(r"CPU usage:\s*([\d.]+)% user, ([\d.]+)% sys, ([\d.]+)% idle", ln)
        if cm:
            cpu_user, cpu_sys, cpu_idle = map(float, cm.groups())
        pm = re.match(r"PhysMem:\s*([\d.]+\s*[KMGTB]?) used.*?,\s*([\d.]+\s*[KMGTB]?) unused", ln)
        if pm:
            mem_used = _to_bytes(pm.group(1))
            mem_unused = _to_bytes(pm.group(2))
        wm = re.search(r"\(([\d.]+\s*[KMGTB]?) wired", ln)
        if wm and ln.startswith("PhysMem:"):
            mem_wired = _to_bytes(wm.group(1))

    # Process rows after the last PhysMem line = the 2nd sample's list.
    last_mem_idx = max((i for i, ln in enumerate(lines)
                        if ln.startswith("PhysMem:")), default=-1)
    for ln in lines[last_mem_idx + 1:]:
        m = re.match(r"^(\d+)\s+([\d.]+)\s+(\S+)\s+(.*?)\s*$", ln)
        if not m:
            continue
        rpid = int(m.group(1))
        cpu = float(m.group(2))          # per-core %, can exceed 100
        membytes = _to_bytes(m.group(3))
        name = m.group(4).strip()
        procs.append({
            "pid": rpid,
            "cpu": round(cpu, 1),
            "cpu_share": round(cpu / _NCPU, 1),   # % of whole machine
            "mem_bytes": membytes,
            "name": name,
            "is_llm": pid is not None and rpid == pid,
        })

    cpu_busy = round(cpu_user + cpu_sys, 1)
    llm = next((p for p in procs if p["is_llm"]), None)

    # If the LLM isn't in the list, fetch it directly. We use top's MEM (not
    # ps rss): for a GPU-offloaded model the weights are Metal/wired memory
    # that rss omits entirely (~6MB), while top's MEM matches Activity
    # Monitor's physical footprint (e.g. 6.5GB).
    if pid and not llm:
        try:
            out2 = subprocess.run(
                ["top", "-l", "1", "-pid", str(pid),
                 "-stats", "pid,cpu,mem,command"],
                capture_output=True, text=True, timeout=8,
            ).stdout
            for ln in reversed(out2.splitlines()):
                m = re.match(rf"^{pid}\s+([\d.]+)\s+(\S+)\s+(.*?)\s*$", ln)
                if m:
                    llm = {
                        "pid": pid,
                        "cpu": round(float(m.group(1)), 1),
                        "cpu_share": round(float(m.group(1)) / _NCPU, 1),
                        "mem_bytes": _to_bytes(m.group(2)),
                        "name": m.group(3).strip(),
                        "is_llm": True,
                    }
                    procs.append(llm)
                    break
        except Exception:
            pass

    llm_cpu = llm["cpu_share"] if llm else 0.0
    llm_mem = llm["mem_bytes"] if llm else 0

    gpu = _gpu()

    # Top list: highest memory consumers, but always include the LLM row.
    top = procs[:8]
    llm_row = next((p for p in procs if p["is_llm"]), None)
    if llm_row and llm_row not in top:
        top = top[:7] + [llm_row]

    return {
        "ts": time.time(),
        "ncpu": _NCPU,
        "cpu": {
            "busy": cpu_busy,
            "user": cpu_user,
            "sys": cpu_sys,
            "idle": round(cpu_idle, 1),
            "llm": round(min(llm_cpu, cpu_busy), 1),
            "other": round(max(0.0, cpu_busy - llm_cpu), 1),
        },
        "ram": {
            "total_bytes": _TOTAL_MEM,
            "used_bytes": mem_used,
            "wired_bytes": mem_wired,
            "free_bytes": mem_unused or max(0, _TOTAL_MEM - mem_used),
            "llm_bytes": llm_mem,  # process RSS only (GPU-offloaded weights excluded)
            "other_bytes": max(0, mem_used - llm_mem),
            "percent": round(mem_used / _TOTAL_MEM * 100, 1) if _TOTAL_MEM else 0,
        },
        "gpu": gpu,
        "top": top,
        "llm_pid": pid,
    }


def _loop() -> None:
    """Sole collector. Runs one sample, then repeats while being watched."""
    global _snapshot
    while True:
        if time.time() - _last_access < 25:
            try:
                snap = _collect()
                with _lock:
                    _snapshot = snap
            except Exception:
                pass
            time.sleep(2.5)
        else:
            time.sleep(2)  # idle: nobody watching, stay cheap


def get_snapshot(llm_pid: Optional[int]) -> Optional[dict]:
    """Return the latest cached snapshot (None while the first sample warms up).

    Only the background thread collects, so two samples never race.
    """
    global _last_access, _llm_pid, _thread
    _last_access = time.time()
    _llm_pid = llm_pid
    if _thread is None:
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    with _lock:
        return _snapshot
