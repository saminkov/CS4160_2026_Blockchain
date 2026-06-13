from __future__ import annotations

import asyncio
import hashlib
import multiprocessing as mp
import struct
from typing import cast

from blockchain.adapters.process_miner import _STOP, ProcessMiner, _Job, _worker_loop
from blockchain.core.pow import has_leading_zero_bits
from blockchain.ports.miner import MinerPort

# mypy conformance: the adapter must satisfy the port.
_m: MinerPort = ProcessMiner()

_PREFIX = b"\x07" * 76


def _digest(prefix: bytes, nonce: int) -> bytes:
    return hashlib.sha256(prefix + struct.pack(">Q", nonce)).digest()


class TestWorkerLoop:
    """Drive the worker loop in-process with real queues/Value (no real fork)."""

    def test_finds_nonce_for_difficulty_zero(self) -> None:
        job_q: mp.Queue[object] = mp.Queue()
        result_q: mp.Queue[object] = mp.Queue()
        current_gen = mp.Value("q", 0)
        current_gen.value = 5

        job_q.put(_Job(prefix=_PREFIX, difficulty=0, generation=5))
        job_q.put(_STOP)
        _worker_loop(job_q, result_q, current_gen, 4096)

        nonce, gen = cast("tuple[int, int]", result_q.get(timeout=5))
        assert nonce == 0
        assert gen == 5

    def test_stop_returns_without_result(self) -> None:
        job_q: mp.Queue[object] = mp.Queue()
        result_q: mp.Queue[object] = mp.Queue()
        current_gen = mp.Value("q", 0)

        job_q.put(_STOP)
        _worker_loop(job_q, result_q, current_gen, 4096)
        assert result_q.empty()

    def test_aborts_when_generation_already_stale(self) -> None:
        job_q: mp.Queue[object] = mp.Queue()
        result_q: mp.Queue[object] = mp.Queue()
        current_gen = mp.Value("q", 0)
        current_gen.value = 99  # differs from the job's generation -> immediate abort

        job_q.put(_Job(prefix=_PREFIX, difficulty=64, generation=1))
        job_q.put(_STOP)
        _worker_loop(job_q, result_q, current_gen, 1)
        assert result_q.empty()


async def _mine_once(miner: ProcessMiner, *, difficulty: int, generation: int) -> tuple[int, int]:
    """Drive one mine() and await the first on_found delivery on this loop."""
    found: asyncio.Future[tuple[int, int]] = asyncio.get_running_loop().create_future()

    def on_found(nonce: int, gen: int) -> None:
        if not found.done():
            found.set_result((nonce, gen))

    miner.mine(_PREFIX, difficulty=difficulty, generation=generation, on_found=on_found)
    return await asyncio.wait_for(found, timeout=15)


class TestProcessMinerIntegration:
    """Exercise the real child process + bridge + call_soon_threadsafe path."""

    def test_mine_delivers_valid_nonce_on_loop(self) -> None:
        async def run() -> None:
            miner = ProcessMiner()
            try:
                nonce, generation = await _mine_once(miner, difficulty=1, generation=7)
                assert generation == 7
                assert has_leading_zero_bits(_digest(_PREFIX, nonce), 1) is True
            finally:
                miner.shutdown()

        asyncio.run(run())

    def test_shutdown_stops_worker_and_is_idempotent(self) -> None:
        async def run() -> ProcessMiner:
            miner = ProcessMiner()
            await _mine_once(miner, difficulty=1, generation=1)
            miner.shutdown()
            return miner

        miner = asyncio.run(run())
        assert miner._proc is None or not miner._proc.is_alive()
        miner.shutdown()  # no-op, must not raise

    def test_auto_restart_after_shutdown(self) -> None:
        async def run() -> None:
            miner = ProcessMiner()
            try:
                await _mine_once(miner, difficulty=1, generation=1)
                miner.shutdown()
                _, gen = await _mine_once(miner, difficulty=1, generation=2)
                assert gen == 2
            finally:
                miner.shutdown()

        asyncio.run(run())


class TestProcessMinerLifecycle:
    def test_cancel_before_mine_is_noop(self) -> None:
        ProcessMiner().cancel()  # must not raise

    def test_shutdown_before_mine_is_noop(self) -> None:
        ProcessMiner().shutdown()  # must not raise
