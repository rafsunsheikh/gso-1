"""System resource monitor (CPU / GPU / RAM) for macOS, no extra deps.

Uses native tools that work without sudo:
  * `top`        -> system CPU%, PhysMem, and per-process CPU%/MEM
  * `ioreg`      -> Apple Silicon GPU "Device Utilization %" + GPU memory in use
  * `sysctl`     -> logical CPU count, total physical memory

Per-process GPU usage is NOT exposed by macOS without elevated privileges, so
GPU is reported system-wide only (with the model offloaded via -ngl, that
activity is essentially the LLM).

Measured 2026-09-08, because a monitor that costs more than the thing it
watches is not a monitor, it is a load:

    top -l 1        673 ms    almost all of it start-up
    top -l 2       1774 ms    673 ms of start-up, 1 s of waiting, ~90 ms of work
    ioreg            22 ms
    vm_stat           2 ms
    sysctl            2 ms

This module used to run `top -l 2` every 2.5 seconds, paying that 673 ms of
start-up over and over purely to obtain one delta: 42% of wall-clock spent
sampling, for a panel updating a number a human reads once. It now keeps a
single `top -l 0 -s N` alive and reads frames off it, so the start-up is paid
once per viewing session and each further frame costs ~70 ms. Same numbers,
1.4% duty cycle, roughly thirty times less CPU.

The other half is that it stops. The old idle branch slept two seconds and
looped, forever, whether or not a soul was watching, which on a laptop is
43,200 pointless wakeups a day holding the CPU out of its deep idle states.
Nothing here runs unless `get_snapshot` has been called recently; when it has
not, the stream is torn down and the thread exits.
"""
from __future__ import annotations

import os
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

# How often `top` emits a frame while somebody is watching. The panel is read
# by a human, not a profiler; five seconds is the difference between a live
# number and a stale one, and each step down multiplies the cost.
_SAMPLE_SECONDS = max(2, int(os.environ.get("MANAGER_SYSMON_INTERVAL", "5") or 5))

# Stop sampling this long after the last read. The UI polls every few seconds,
# so anything above that is comfortably "still watching"; close the tab and the
# stream is gone within one window.
_IDLE_AFTER = 25.0

# Sorted by memory so the real RAM consumers (including a GPU-offloaded
# llama-server, whose weights never show up in rss) are the ones captured.
_TOP_ARGS = ["-o", "mem", "-n", "12", "-stats", "pid,cpu,mem,command"]

# A llama-server holding gigabytes is always in a list sorted by memory, so the
# direct per-pid lookup below is a rare fallback, but "rare" was once per frame
# whenever it did fire, at 673 ms a go. [when, last_row]
_LLM_PROBE_EVERY = 30.0
_llm_probe: list = [0.0, None]


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


def _memory() -> dict:
    """Memory the way Activity Monitor accounts for it.

    `top`'s PhysMem "used" counts the file cache as used, so GSO-1 reported 90%
    used and 2 GB free on a machine Activity Monitor showed as healthy with
    6.8 GB of reclaimable cache. That is not a rounding difference, it is a
    different definition, and the alarming one is the wrong one: cached file
    pages are evicted the moment anything needs the space.

    vm_stat exposes the categories Apple actually breaks out, and the arithmetic
    below reproduces the Memory tab to two decimal places on this machine:

        App Memory   = anonymous - purgeable
        Memory Used  = App Memory + wired + compressed
        Cached Files = file-backed + purgeable
        Available    = free + speculative + cached
    """
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return {}
    m = re.search(r"page size of (\d+)", out)
    if not m:
        return {}
    page = int(m.group(1))

    def pages(name: str) -> int:
        hit = re.search(rf"{re.escape(name)}:\s+(\d+)", out)
        return int(hit.group(1)) * page if hit else 0

    anon = pages("Anonymous pages")
    purgeable = pages("Pages purgeable")
    wired = pages("Pages wired down")
    compressed = pages("Pages occupied by compressor")
    file_backed = pages("File-backed pages")
    free = pages("Pages free")
    speculative = pages("Pages speculative")
    if not (anon or wired):
        return {}

    app = max(0, anon - purgeable)
    cached = file_backed + purgeable
    return {
        "app_bytes": app,
        "wired_bytes": wired,
        "compressed_bytes": compressed,
        "cached_bytes": cached,
        "free_bytes": free + speculative,
        "used_bytes": app + wired + compressed,
        # Free plus everything reclaimable: the number that decides whether a
        # 9 GB model loads without swapping, and the one the summariser's
        # recommendation should be reading rather than bare free memory.
        "available_bytes": free + speculative + cached,
    }


def _swap() -> dict:
    """Swap in use. The honest signal that a machine is over-committed."""
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    used = re.search(r"used\s*=\s*([\d.]+[KMGT]?)", out)
    total = re.search(r"total\s*=\s*([\d.]+[KMGT]?)", out)
    return {
        "swap_used_bytes": _to_bytes(used.group(1)) if used else 0,
        "swap_total_bytes": _to_bytes(total.group(1)) if total else 0,
    }


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


def _collect(out: Optional[str] = None) -> dict:
    """Turn one `top` frame into a snapshot.

    `out` is a frame taken off the long-lived stream. Passing None runs a
    one-off `top -l 2` instead, for the callers that want a single reading and
    are not worth keeping a stream alive for; two samples because the first
    reports averages since boot and only the second is live.
    """
    pid = _llm_pid
    cpu_user = cpu_sys = cpu_idle = 0.0
    mem_used = mem_unused = mem_wired = 0
    procs: list[dict] = []

    if out is None:
        try:
            out = subprocess.run(
                ["top", "-l", "2", *_TOP_ARGS],
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
    if pid and not llm and time.time() - _llm_probe[0] > _LLM_PROBE_EVERY:
        _llm_probe[0] = time.time()
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

    if llm is not None:
        _llm_probe[1] = llm
    elif pid and _llm_probe[1] and _llm_probe[1].get("pid") == pid:
        # Between probes, reuse the last direct reading rather than dropping
        # the LLM row out of the panel every other frame.
        llm = _llm_probe[1]
        procs.append(llm)

    llm_cpu = llm["cpu_share"] if llm else 0.0
    llm_mem = llm["mem_bytes"] if llm else 0

    gpu = _gpu()

    # Top list: highest memory consumers, but always include the LLM row.
    top = procs[:8]
    llm_row = next((p for p in procs if p["is_llm"]), None)
    if llm_row and llm_row not in top:
        top = top[:7] + [llm_row]

    vm = _memory()
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
            # Apple's accounting where vm_stat gave it; top's cruder figures
            # only as a fallback.
            "used_bytes": vm.get("used_bytes", mem_used),
            "wired_bytes": vm.get("wired_bytes", mem_wired),
            "free_bytes": vm.get("free_bytes",
                                 mem_unused or max(0, _TOTAL_MEM - mem_used)),
            "cached_bytes": vm.get("cached_bytes", 0),
            "compressed_bytes": vm.get("compressed_bytes", 0),
            "app_bytes": vm.get("app_bytes", 0),
            "available_bytes": vm.get("available_bytes",
                                      mem_unused or max(0, _TOTAL_MEM - mem_used)),
            "llm_bytes": llm_mem,  # process RSS only (GPU-offloaded weights excluded)
            "other_bytes": max(0, vm.get("used_bytes", mem_used) - llm_mem),
            "percent": round(vm.get("used_bytes", mem_used) / _TOTAL_MEM * 100, 1)
            if _TOTAL_MEM else 0,
            **_swap(),
        },
        "gpu": gpu,
        "top": top,
        "llm_pid": pid,
    }


def _stream() -> None:
    """Read frames off one long-lived `top` for as long as somebody is watching.

    Ends by itself once nothing has asked for a snapshot in `_IDLE_AFTER`
    seconds, killing the child and clearing `_thread` so the next reader starts
    a fresh one. An idle GSO-1 runs no sampler at all, which is the point.
    """
    global _snapshot, _thread

    # One immediate reading, so the panel has numbers now rather than after two
    # frames of the stream. This is the expensive call; it happens once when
    # somebody starts watching, not every two and a half seconds.
    try:
        snap = _collect()
        with _lock:
            _snapshot = snap
    except Exception:
        pass

    proc = None
    try:
        proc = subprocess.Popen(
            ["top", "-l", "0", "-s", str(_SAMPLE_SECONDS), *_TOP_ARGS],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        frame: list[str] = []
        seen = 0
        for line in proc.stdout:  # type: ignore[union-attr]
            if not line.startswith("Processes:"):
                frame.append(line)
                continue
            # A frame is only known to be complete when the next one starts.
            seen += 1
            if seen > 2 and frame:
                # `top`'s first frame reports CPU averaged since boot; from the
                # second on it is a delta against the frame before, which is
                # what the panel means by "CPU now".
                try:
                    snap = _collect("".join(frame))
                    with _lock:
                        _snapshot = snap
                except Exception:
                    pass
            frame = [line]
            if time.time() - _last_access > _IDLE_AFTER:
                break
    except Exception:
        pass
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with _lock:
            _thread = None


def sample(llm_pid: Optional[int]) -> dict:
    """Synchronous one-off sample (blocks ~2 s). Used by non-polling callers.

    Does not start or touch the stream: a caller that wants one reading every
    few hours should not leave a sampler running behind it.
    """
    global _llm_pid
    _llm_pid = llm_pid
    return _collect()


def total_memory() -> int:
    """Physical RAM in bytes, read once at import. 0 if sysctl was unavailable.

    Separate from the sampler because callers that only need the machine's size
    should not have to wait for the first three-second sample to land.
    """
    return _TOTAL_MEM


def get_snapshot(llm_pid: Optional[int]) -> Optional[dict]:
    """Return the latest cached snapshot (None while the first sample warms up).

    Only the background thread collects, so two samples never race.
    """
    global _last_access, _llm_pid, _thread
    _last_access = time.time()
    _llm_pid = llm_pid
    with _lock:
        # The stream shuts itself down when unwatched, so this both starts the
        # first one and revives it after an idle spell.
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_stream, name="sysmon", daemon=True)
            _thread.start()
        return _snapshot
