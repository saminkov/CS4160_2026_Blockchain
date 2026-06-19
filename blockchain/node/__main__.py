"""CLI entrypoint: python -m blockchain.node"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from blockchain.node.config import load_config
from blockchain.node.node import Node


async def _run(argv: list[str] | None) -> None:
    config = load_config(argv)
    node = Node(config)
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        stop_event.set()

    # Graceful shutdown on SIGINT/SIGTERM where the loop supports it. The Windows
    # Proactor loop does not implement add_signal_handler; there we fall back to
    # the KeyboardInterrupt path raised out of asyncio.run().
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    await node.start()
    try:
        await stop_event.wait()
    finally:
        await node.stop()


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
