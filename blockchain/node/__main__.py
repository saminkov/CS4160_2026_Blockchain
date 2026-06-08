"""CLI entrypoint: python -m blockchain.node """

from __future__ import annotations

import argparse

def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and start the node"""
    parser = argparse.ArgumentParser(
        prog="blockchain-node",
        description="CS4160 Lab 3 PoW blockchain node",
    )
    parser.add_argument("--config", help="Path to node config file (TOML/JSON)")
    parser.add_argument("--key", help="Path to member private key PEM")
    parser.add_argument(
        "--registrar",
        action="store_true",
        help="Act as the designated registrar for group registration",
    )
    _args = parser.parse_args(argv)

    print("TO DO")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
