"""CLI entrypoint: python -m blockchain.node """

from __future__ import annotations

import argparse

from blockchain.node.config import load_config

def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and start the node"""
    config = load_config(argv)
    
    print(f"Loaded config: {config.params.group_id} - Node member index: {config.member_index}")
    print("TO DO")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
