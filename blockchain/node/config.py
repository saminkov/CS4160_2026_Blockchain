from __future__ import annotations

import argparse
import dataclasses
import json
import tomllib
from pathlib import Path
from typing import Any

from ipv8.keyvault.crypto import default_eccrypto

from blockchain.core.consensus_params import ConsensusParams, community_id_from_group_id


def parse_peer_address(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not port_text.isdigit():
        raise ValueError(f"peer address must be host:port, got {value!r}")
    port = int(port_text)
    if port <= 0 or port > 65535:
        raise ValueError(f"peer port out of range: {port}")
    return host, port


@dataclasses.dataclass(frozen=True, slots=True)
class NodeConfig:
    params: ConsensusParams
    privkey: bytes
    pubkey: bytes
    key_path: str
    member_index: int | None
    is_registrar: bool
    log_level: str
    port: int
    peers: tuple[tuple[str, int], ...]


def load_config(argv: list[str] | None = None) -> NodeConfig:
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
    parser.add_argument("--difficulty", type=int, help="Override difficulty bits")
    parser.add_argument("--log-level", help="Logging level (default: INFO)")
    parser.add_argument("--port", type=int, help="UDP port to bind for IPv8")
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="HOST:PORT",
        help="Static peer to walk to (repeatable; also [node] peers in config)",
    )
    parser.add_argument("--group-id", help="Override group ID")
    parser.add_argument("--community-id", help="Override community ID (hex)")

    args = parser.parse_args(argv)

    config_dict: dict[str, Any] = {}
    if args.config:
        config_path = Path(args.config)
        if config_path.suffix.lower() == ".json":
            config_dict = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            config_dict = tomllib.loads(config_path.read_text(encoding="utf-8"))

    # Extract sections
    consensus_cfg = config_dict.get("consensus", {})
    node_cfg = config_dict.get("node", {})

    # Start with default params
    params = ConsensusParams.default()

    # Override group_id / community_id from config then CLI
    new_group_id = args.group_id or consensus_cfg.get("group_id")
    if new_group_id is not None:
        new_community_id = args.community_id or consensus_cfg.get("community_id")
        if new_community_id is None:
            new_community_id = community_id_from_group_id(new_group_id)
        elif isinstance(new_community_id, str):
            new_community_id = bytes.fromhex(new_community_id)
        params = dataclasses.replace(
            params, group_id=new_group_id, community_id=new_community_id
        )
    else:
        new_community_id = args.community_id or consensus_cfg.get("community_id")
        if new_community_id is not None:
            if isinstance(new_community_id, str):
                new_community_id = bytes.fromhex(new_community_id)
            params = dataclasses.replace(params, community_id=new_community_id)

    # Difficulty bits override
    difficulty_bits = (
        args.difficulty
        if args.difficulty is not None
        else consensus_cfg.get("difficulty_bits")
    )
    if difficulty_bits is not None:
        params = dataclasses.replace(params, difficulty_bits=int(difficulty_bits))

    # Read node private key
    key_path_str = args.key or node_cfg.get("key")
    if not key_path_str:
        raise ValueError(
            "Node key path must be specified via --key or [node] key config"
        )

    key_path = Path(key_path_str)
    privkey_bytes = key_path.read_bytes()

    # Derive pubkey
    ipv8_key = default_eccrypto.key_from_private_bin(privkey_bytes)
    pubkey_bytes = default_eccrypto.key_to_bin(ipv8_key.pub())

    # Find member index
    member_index = None
    for i, member_pubkey in enumerate(params.member_pubkeys):
        if pubkey_bytes == member_pubkey:
            member_index = i
            break

    # Registrar
    is_registrar = args.registrar or node_cfg.get("registrar", False)

    # Log level
    log_level = args.log_level or node_cfg.get("log_level", "INFO")

    # Port
    port = args.port if args.port is not None else node_cfg.get("port", 8090)

    peer_values = list(args.peer or ())
    peer_values.extend(node_cfg.get("peers", ()))
    peers = tuple(parse_peer_address(value) for value in peer_values)

    return NodeConfig(
        params=params,
        privkey=privkey_bytes,
        pubkey=pubkey_bytes,
        key_path=str(key_path),
        member_index=member_index,
        is_registrar=bool(is_registrar),
        log_level=log_level,
        port=int(port),
        peers=peers,
    )
