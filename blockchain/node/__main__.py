"""CLI entrypoint: python -m blockchain.node"""

from __future__ import annotations

import asyncio
import logging

from blockchain.node.config import load_config
from blockchain.node.node import Node

logger = logging.getLogger(__name__)


async def _run(argv: list[str] | None) -> None:
    config = load_config(argv)
    node = Node(config)
    await node.start()
    try:
        # Keep the event loop alive; IPv8 tasks do the real work.
        await asyncio.get_running_loop().create_future()
    except asyncio.CancelledError:
        pass
    finally:
        await node.stop()


def main(argv: list[str] | None = None) -> int:
    try:
        asyncio.run(_run(argv))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
