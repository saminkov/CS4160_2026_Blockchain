from __future__ import annotations

import logging
from collections import defaultdict

from blockchain.adapters.payloads import (
    BlockDataPayload,
    BlockInvPayload,
    BlockResponsePayload,
    ChainHeightResponsePayload,
    GetBlockByHeightPayload,
    GetBlockDataPayload,
    GetBlockPayload,
    GetChainHeightPayload,
    block_data_not_found,
    block_data_payloads_from_block,
    block_from_block_data_payloads,
    block_response_from_block,
    block_response_not_found,
    is_block_data_not_found,
)
from blockchain.core.hashing import block_hash
from blockchain.ports.network import NetworkPort

logger = logging.getLogger(__name__)

_SYNC_INTERVAL = 30.0   # seconds between background gap-fill polls
_LOOKAHEAD = 6          # number of heights to request ahead of our tip


class SyncService:
    """Block propagation, server query serving, and background gap-filling."""

    def __init__(
        self,
        chain: object,  # ChainService — late import avoids circular dependency
        network: NetworkPort,
        params: object,  # ConsensusParams
    ) -> None:
        self._chain = chain
        self._network = network
        self._params = params

        # chunk_buf[block_hash] = list of received BlockDataPayload chunks
        self._chunk_buf: dict[bytes, list[BlockDataPayload]] = defaultdict(list)

        network.register_handler(GetChainHeightPayload, self._on_get_height)
        network.register_handler(GetBlockPayload, self._on_get_block)
        network.register_handler(BlockInvPayload, self._on_block_inv)
        network.register_handler(GetBlockDataPayload, self._on_get_block_data)
        network.register_handler(BlockDataPayload, self._on_block_data)
        network.register_handler(GetBlockByHeightPayload, self._on_get_block_by_height)

        network.register_task("sync_loop", self._sync_loop, _SYNC_INTERVAL)

    # ------------------------------------------------------------------
    # Server-facing handlers
    # ------------------------------------------------------------------

    def _on_get_height(self, peer: bytes, payload: GetChainHeightPayload) -> None:
        height = self._chain.height()  # type: ignore[attr-defined]
        tip_hash = b""
        node = self._chain._blocks.tip()  # type: ignore[attr-defined]
        if node is not None:
            tip_hash = node.block_hash
        self._network.send(
            peer,
            ChainHeightResponsePayload(payload.request_id, height, tip_hash),
        )

    def _on_get_block(self, peer: bytes, payload: GetBlockPayload) -> None:
        node = self._chain._blocks.node_at(payload.height)  # type: ignore[attr-defined]
        if node is None:
            self._network.send(peer, block_response_not_found(payload.height))
            return
        self._network.send(peer, block_response_from_block(node.block, node.height))

    # ------------------------------------------------------------------
    # Peer-facing handlers
    # ------------------------------------------------------------------

    def _on_block_inv(self, peer: bytes, payload: BlockInvPayload) -> None:
        if not self._chain._blocks.has(payload.block_hash):  # type: ignore[attr-defined]
            self._network.send(peer, GetBlockDataPayload(payload.block_hash))

    def _on_get_block_data(self, peer: bytes, payload: GetBlockDataPayload) -> None:
        node = self._chain._blocks.get(payload.block_hash)  # type: ignore[attr-defined]
        if node is None:
            self._network.send(peer, block_data_not_found(0))
            return
        for chunk in block_data_payloads_from_block(node.block, node.height):
            self._network.send(peer, chunk)

    def _on_block_data(self, peer: bytes, payload: BlockDataPayload) -> None:
        if is_block_data_not_found(payload):
            return
        key = payload.block_hash
        self._chunk_buf[key].append(payload)
        chunks = self._chunk_buf[key]
        if len(chunks) < payload.chunk_count:
            return  # still waiting for more chunks
        del self._chunk_buf[key]
        try:
            block = block_from_block_data_payloads(chunks)
        except ValueError as exc:
            logger.warning("bad block data from %s: %s", peer.hex()[:8], exc)
            return
        result = self._chain.connect_block(block, source=peer.hex()[:8])  # type: ignore[attr-defined]
        if result.ok and result.reason not in ("already known", "orphan"):
            logger.info(
                "connected block h=%d hash=%s from %s",
                payload.height,
                key.hex()[:12],
                peer.hex()[:8],
            )

    def _on_get_block_by_height(self, peer: bytes, payload: GetBlockByHeightPayload) -> None:
        node = self._chain._blocks.node_at(payload.height)  # type: ignore[attr-defined]
        if node is None:
            self._network.send(peer, block_data_not_found(payload.height))
            return
        for chunk in block_data_payloads_from_block(node.block, node.height):
            self._network.send(peer, chunk)

    # ------------------------------------------------------------------
    # Background gap-filling
    # ------------------------------------------------------------------

    def _sync_loop(self) -> None:
        my_height = self._chain.height()  # type: ignore[attr-defined]
        online = self._network.members_online()
        if not online:
            return
        for h in range(my_height + 1, my_height + 1 + _LOOKAHEAD):
            if self._chain._blocks.node_at(h) is not None:  # type: ignore[attr-defined]
                continue
            for member in online:
                self._network.send(member, GetBlockByHeightPayload(h))
