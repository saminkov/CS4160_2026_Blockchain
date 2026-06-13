from __future__ import annotations

import asyncio
import multiprocessing as mp
import threading
from dataclasses import dataclass
from multiprocessing.queues import Queue
from multiprocessing.sharedctypes import Synchronized
from typing import Final, cast

from blockchain.core.pow import search_nonce
from blockchain.logging_setup import get_logger
from blockchain.ports.miner import OnFound

_log = get_logger("miner")

# Generation value that no live job can match -> aborts the in-flight search.
_CANCELLED: Final = -1
_JOIN_TIMEOUT: Final = 2.0


@dataclass(frozen=True)
class _Job:
    """A unit of mining work handed to the worker (picklable for spawn)."""

    prefix: bytes
    difficulty: int
    generation: int


class _Stop:
    """Sentinel telling the worker loop / bridge to terminate."""


_STOP: Final = _Stop()


def _worker_loop(
    job_q: Queue[object],
    result_q: Queue[object],
    current_gen: Synchronized[int],
    check_interval: int,
) -> None:
    """Child-process entry point: search nonces, abandoning stale generations.

    Blocks on ``job_q`` between jobs. For each ``_Job`` it searches for a nonce,
    aborting cooperatively when ``current_gen`` no longer equals the job's
    generation (a newer job arrived, or the search was cancelled). Found nonces
    are pushed to ``result_q`` as ``(nonce, generation)``.
    """
    while True:
        raw = job_q.get()
        if isinstance(raw, _Stop):
            return
        job = cast(_Job, raw)
        gen = job.generation
        # The lambda is consumed synchronously by search_nonce within this
        nonce = search_nonce(
            job.prefix,
            job.difficulty,
            should_abort=lambda: current_gen.value != gen,  # noqa: B023
            check_interval=check_interval,
        )
        if nonce is not None and current_gen.value == gen:
            result_q.put((nonce, gen))


class ProcessMiner:
    """A cancellable PoW engine backed by a single long-lived child process.

    Construction is side-effect-free; the worker process and bridge thread are
    spawned lazily on the first :meth:`mine` call and respawned if the worker
    has died (auto-restart). Found nonces are delivered back onto the event loop
    via ``loop.call_soon_threadsafe``.
    """

    def __init__(self, check_interval: int = 4096) -> None:
        if check_interval < 1:
            raise ValueError("check_interval must be >= 1")
        self._check_interval = check_interval
        self._proc: mp.Process | None = None
        self._bridge: threading.Thread | None = None
        self._job_q: Queue[object] | None = None
        self._result_q: Queue[object] | None = None
        self._current_gen: Synchronized[int] = cast("Synchronized[int]", mp.Value("q", _CANCELLED))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_found: OnFound | None = None

    def mine(
        self,
        header_prefix: bytes,
        difficulty: int,
        generation: int,
        on_found: OnFound,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._on_found = on_found
        self._ensure_worker()
        # Abort any in-flight search of an older generation, then enqueue the new job.
        self._current_gen.value = generation
        assert self._job_q is not None
        self._job_q.put(_Job(prefix=header_prefix, difficulty=difficulty, generation=generation))
        _log.debug("submitted mining job gen=%d difficulty=%d", generation, difficulty)

    def cancel(self) -> None:
        if self._proc is None:
            return
        self._current_gen.value = _CANCELLED
        _log.debug("mining cancelled")

    def shutdown(self) -> None:
        if self._proc is None:
            return
        _log.info("shutting down miner")
        assert self._job_q is not None and self._result_q is not None
        self._current_gen.value = _CANCELLED
        self._job_q.put(_STOP)
        self._result_q.put(_STOP)
        self._proc.join(_JOIN_TIMEOUT)
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(_JOIN_TIMEOUT)
        if self._bridge is not None:
            self._bridge.join(_JOIN_TIMEOUT)
        self._proc = None
        self._bridge = None
        self._job_q = None
        self._result_q = None


    def _ensure_worker(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        if self._proc is not None:
            _log.info("miner worker died; restarting")
        self._job_q = mp.Queue()
        self._result_q = mp.Queue()
        self._proc = mp.Process(
            target=_worker_loop,
            args=(self._job_q, self._result_q, self._current_gen, self._check_interval),
            daemon=True,
        )
        self._proc.start()
        self._bridge = threading.Thread(
            target=self._bridge_loop, args=(self._result_q,), daemon=True
        )
        self._bridge.start()
        _log.info("miner worker started (pid=%s)", self._proc.pid)

    def _bridge_loop(self, result_q: Queue[object]) -> None:
        """Daemon thread: forward worker results onto the event loop."""
        while True:
            result = result_q.get()
            if isinstance(result, _Stop):
                return
            nonce, generation = cast("tuple[int, int]", result)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._deliver, nonce, generation)

    def _deliver(self, nonce: int, generation: int) -> None:
        """Runs on the event loop: hand the found nonce to the caller."""
        _log.debug("nonce found gen=%d nonce=%d", generation, nonce)
        if self._on_found is not None:
            self._on_found(nonce, generation)
