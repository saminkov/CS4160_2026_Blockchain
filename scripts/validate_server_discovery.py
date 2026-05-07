import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from client import IPv8Client
from protocol import COMMUNITY_ID, SERVER_PUBLIC_KEY

TIMEOUT = 120


async def run() -> None:
    key_path = os.path.join(os.path.dirname(__file__), "..", "keys", "validate_tmp.pem")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    print(f"Joining community, polling for server ;timeout={TIMEOUT}s")
    async with await IPv8Client.build(COMMUNITY_ID, key_path) as client:
        found = await client.validate_visible(SERVER_PUBLIC_KEY, timeout=TIMEOUT)

    if found:
        print(f"[SERVER] :server is reachable.")
        sys.exit(0)
    else:
        print(f"[SERVER] : server peer not found within {TIMEOUT}s")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
