"""A rolling hour of local-LLM traffic.

The Local LLM view shows a LAST HOUR histogram. llama.cpp keeps no such
history, so the proxy records one here: every request that passes through
`/v1/messages` lands in a per-minute bucket. Sixty buckets is ~5 KB of state,
so it lives in memory and resets with the process: an hour of history does not
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

# The Ops Room timeline wants "llama-server · 42 requests · 318k tokens", not one
# line per request. Roll the traffic up once per slot and log that instead.
ROLLUP_MINUTES = 15
_last_rollup: int = 0


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
        due = _rollup_due(now)
    if due:
        _log_rollup(due)


def _rollup_due(now: int) -> Optional[tuple[int, int]]:
    """The slot that just closed, if this is the first request of a new one."""
    global _last_rollup
    slot = now // ROLLUP_MINUTES
    if _last_rollup == 0:
        _last_rollup = slot
        return None
    if slot == _last_rollup:
        return None
    prev, _last_rollup = _last_rollup, slot
    return (prev * ROLLUP_MINUTES, ROLLUP_MINUTES)


def _log_rollup(window: tuple[int, int]) -> None:
    """Summarise a closed slot onto the timeline. Import is local: llmusage is
    imported by the proxy, and events imports config, keep the cycle out."""
    start, span = window
    with _lock:
        agg = {"requests": 0, "tokens": 0, "errors": 0}
        for m in range(start, start + span):
            b = _buckets.get(m)
            if b:
                for k in agg:
                    agg[k] += b[k]
    if not agg["requests"]:
        return
    from . import events

    toks = (f"{agg['tokens'] / 1e6:.2f}M" if agg["tokens"] >= 1e6
            else f"{agg['tokens'] / 1e3:.0f}k" if agg["tokens"] >= 1000
            else str(agg["tokens"]))
    events.record(
        "fail" if agg["errors"] else "llm", "llama-server",
        f"{agg['requests']} requests · {toks} tokens · {agg['errors']} errors",
    )


def summary() -> dict:
    """`BARS` equal slices of the last hour, newest slice last."""
    now = _minute()
    with _lock:
        _prune(now)
        snapshot = dict(_buckets)

    per_bar = WINDOW_MINUTES // BARS  # 7 minutes with a 60/8 split
    bars = []
    for i in range(BARS):
        # Slice i covers the minutes [now - (BARS-i)*per_bar, ... ), oldest first.
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
