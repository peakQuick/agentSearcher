"""In-process counters and system load for the dashboard (GET /stats). Reset on restart."""

import functools
import os
import time
from collections import Counter, deque

import psutil

started = time.time()
requests: dict[str, dict[str, float]] = {
    k: {"ok": 0, "failed": 0, "seconds": 0.0} for k in ("search", "extract", "crawl", "map")
}
pages: Counter[str] = Counter()
recent: deque[dict] = deque(maxlen=30)
_procs: dict[int, psutil.Process] = {}


def tracked(kind: str, target):
    """Count calls of an async pipeline entry point; `target(req)` names it in `recent`."""

    def wrap(func):
        @functools.wraps(func)
        async def call(req):
            begin = time.monotonic()
            error = None
            try:
                return await func(req)
            except Exception as e:
                error = str(e) or type(e).__name__
                raise
            finally:
                seconds = time.monotonic() - begin
                requests[kind]["failed" if error else "ok"] += 1
                requests[kind]["seconds"] += seconds
                recent.appendleft(
                    {
                        "time": time.time(),
                        "kind": kind,
                        "target": target(req)[:200],
                        "seconds": round(seconds, 2),
                        "error": error,
                    }
                )

        return call

    return wrap


def _memory(proc: psutil.Process) -> int:
    """PSS where the OS has it (Linux): Chromium processes share memory, RSS overcounts."""
    try:
        return getattr(proc.memory_full_info(), "pss", 0) or proc.memory_info().rss
    except psutil.AccessDenied:
        return proc.memory_info().rss


def system() -> dict:
    """Server process plus its children (browser, node driver), and the whole machine.

    Process objects are kept between calls: cpu_percent() measures since the previous call.
    """
    me = psutil.Process()
    alive = {}
    memory, cpu = 0, 0.0
    for proc in [me, *me.children(recursive=True)]:
        proc = _procs.get(proc.pid, proc)
        try:
            cpu += proc.cpu_percent()
            memory += _memory(proc)
        except psutil.NoSuchProcess:
            continue
        alive[proc.pid] = proc
    _procs.clear()
    _procs.update(alive)
    vm = psutil.virtual_memory()
    return {
        "processes": len(alive),
        "cpu_percent": round(cpu, 1),
        "memory_mb": round(memory / 2**20),
        "system_cpu_percent": psutil.cpu_percent(),
        "system_memory_used_mb": round((vm.total - vm.available) / 2**20),
        "system_memory_total_mb": round(vm.total / 2**20),
        "cpus": os.cpu_count(),
        "load_average": [round(x, 2) for x in os.getloadavg()]
        if hasattr(os, "getloadavg")
        else None,
    }
