"""A rolling hour of local-LLM traffic.

The Local LLM view shows a LAST HOUR histogram. llama.cpp keeps no such
history, so the proxy records one here: every request that passes through
`/v1/messages` lands in a per-minute bucket. Sixty buckets is ~5 KB of state,
so it lives in memory and resets with the process — an hour of history does not
justify a file.
"""

from __future__ import annotations

import threading
import time

WINDOW_MINUTES = 60
BARS = 8  # what the histogram renders

_lock = threading.Lock()
# minute-since-epoch -> counters
_buckets: dict[int, dict[str, int]] = {}


def _minute(ts: float | None = None) -> int:
    return int((ts if ts is not None else time.time()) // 60)


def _prune(now_min: int) -> None:
    cutoff = now_min - WINDOW_MINUTES
    for m in [m for m in _buckets if m <= cutoff]:
        del _buckets[m]


def record(input_tokens: int = 0, output_tokens: int = 0, error: bool = False) -> None:
    """One completed request through the proxy."""
    now = _minute()
    with _lock:
        _prune(now)
        b = _buckets.setdefault(now, {"requests": 0, "tokens": 0, "errors": 0})
        b["requests"] += 1
        b["tokens"] += max(0, int(input_tokens)) + max(0, int(output_tokens))
        if error:
            b["errors"] += 1


def summary() -> dict:
    """`BARS` equal slices of the last hour, newest slice last."""
    now = _minute()
    with _lock:
        _prune(now)
        snapshot = dict(_buckets)

    per_bar = WINDOW_MINUTES // BARS  # 7 minutes with a 60/8 split
    bars = []
    for i in range(BARS):
        # Slice i covers the minutes [now - (BARS-i)*per_bar, ... ) — oldest first.
        start = now - (BARS - i) * per_bar + 1
        agg = {"requests": 0, "tokens": 0, "errors": 0}
        for m in range(start, start + per_bar):
            b = snapshot.get(m)
            if b:
                for k in agg:
                    agg[k] += b[k]
        bars.append(agg)

    totals = {
        "requests": sum(b["requests"] for b in snapshot.values()),
        "tokens": sum(b["tokens"] for b in snapshot.values()),
        "errors": sum(b["errors"] for b in snapshot.values()),
    }
    return {"bars": bars, "totals": totals, "window_minutes": WINDOW_MINUTES}
