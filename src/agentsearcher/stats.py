"""In-process counters and system load for the dashboard (GET /stats). Reset on restart."""

import asyncio
import functools
import os
import time
from collections import Counter, deque
from contextlib import asynccontextmanager

import psutil

from agentsearcher import config

started = time.time()
requests: dict[str, dict[str, float]] = {
    k: {"ok": 0, "failed": 0, "seconds": 0.0} for k in ("search", "extract", "crawl", "map")
}
pages: Counter[str] = Counter()
recent: deque[dict] = deque(maxlen=30)
_procs: dict[int, psutil.Process] = {}


class Busy(Exception):
    """No request slot freed up within QUEUE_TIMEOUT."""


_requests = asyncio.Semaphore(config.MAX_REQUESTS)
_crawls = asyncio.Semaphore(config.MAX_CRAWLS)
queue = {"active": 0, "queued": 0}


@asynccontextmanager
async def _slot(kind: str):
    """Wait (FIFO) for a request slot; crawl and map also need one of the fewer crawl slots,
    taken first so a waiting crawl doesn't hold a slot that a search could use."""
    needed = [_crawls, _requests] if kind in ("crawl", "map") else [_requests]
    taken: list[asyncio.Semaphore] = []
    queue["queued"] += 1
    try:
        async with asyncio.timeout(config.QUEUE_TIMEOUT):
            for sem in needed:
                await sem.acquire()
                taken.append(sem)
    except BaseException as e:
        for sem in taken:
            sem.release()
        if isinstance(e, TimeoutError):
            raise Busy(f"server busy: {queue['queued']} requests queued, retry later") from None
        raise
    finally:
        queue["queued"] -= 1
    queue["active"] += 1
    try:
        yield
    finally:
        queue["active"] -= 1
        for sem in taken:
            sem.release()


def tracked(kind: str, target):
    """Queue and count calls of an async pipeline entry point; `target(req)` names it in
    `recent`. The request's own time budget starts once it leaves the queue."""

    def wrap(func):
        @functools.wraps(func)
        async def call(req):
            queued_at = begin = time.monotonic()
            error = None
            try:
                async with _slot(kind):
                    begin = time.monotonic()
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
                        "waited": round(begin - queued_at, 2),
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
