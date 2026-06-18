from __future__ import annotations

import asyncio
import signal

from blockchain.node.config import load_config
from blockchain.node.node import Node


async def _run(config_path: list[str] | None = None) -> int:
    config = load_config(config_path)
    node = Node(config)
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    await node.start()
    try:
        await stop_event.wait()
    finally:
        await node.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(argv))
