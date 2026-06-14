from __future__ import annotations

import logging

from blockchain.app.chain_service import ChainService
from blockchain.core.entities import Block, BlockNode
from blockchain.ports.network import NetworkPort
from blockchain.ports.stores import BlockStorePort

logger = logging.getLogger(__name__)


class SyncService:
    """Block propagation and catch-up: inv/getdata/blockdata + gap/orphan re-scan (A16, D7/D8)."""

    def __init__(
        self,
        network: NetworkPort,
        blocks: BlockStorePort,
        chain: ChainService,
    ) -> None:
        self._network = network
        self._blocks = blocks
        self._chain = chain
        # Heights still owed a pull; the periodic re-scan re-asks until they link.
        self._needed: set[int] = set()

    def on_block_inv(self, sender: bytes, block_hash: bytes, height: int) -> None:
        """A peer announced a block; pull it if we do not already have it (D7)."""
        try:
            if not self._blocks.has(block_hash):
                self._network.request_block_data(sender, block_hash)
        except Exception:
            logger.exception("on_block_inv failed for %s", block_hash.hex())

    def serve_block_data(self, sender: bytes, block_hash: bytes) -> None:
        """Answer a ``GetBlockData`` pull with the full block, if known (D7)."""
        try:
            node = self._blocks.get(block_hash)
            if node is not None:
                self._network.send_block(sender, node.block, node.height)
        except Exception:
            logger.exception("serve_block_data failed for %s", block_hash.hex())

    def on_block(self, sender: bytes, block: Block, height: int) -> None:
        """Feed a received block to the single writer; pull its parent if orphaned."""
        try:
            result = self._chain.connect_block(block, source="sync")
            if result.reason == "orphan":
                # Missing parent: walk down by height until the branch links.
                missing = height - 1
                self._needed.add(missing)
                self._network.request_block_by_height(sender, missing)
            elif result.ok and result.reason == "connected":
                self._needed.discard(height)
        except Exception:
            logger.exception("on_block failed at height %d", height)

    def serve_block_by_height(self, sender: bytes, height: int) -> None:
        """Answer a ``GetBlockByHeight`` pull; always reply (sentinel if absent, D20)."""
        try:
            node = self._blocks.node_at(height)
            if node is not None:
                self._network.send_block(sender, node.block, height)
            else:
                self._network.send_block_not_found(sender, height)
        except Exception:
            logger.exception("serve_block_by_height failed at height %d", height)

    def announce_tip(self, node: BlockNode) -> None:
        """Announce a newly-accepted tip to members as ``BlockInv`` (wired to on_tip_changed)."""
        try:
            self._network.announce_block(node.block_hash, node.height)
        except Exception:
            logger.exception("announce_tip failed at height %d", node.height)

    def rescan(self) -> None:
        """Idempotent self-heal (register_task): re-ask peers for unlinked/missing heights."""
        try:
            members = self._network.members_online()
            if not members:
                return
            # Drop heights that have since linked; re-request the rest
            self._needed = {h for h in self._needed if self._blocks.node_at(h) is None}
            # Also probe the next height so a missed BlockInv still gets caught up
            wanted = set(self._needed)
            wanted.add(self._chain.height() + 1)
            for height in wanted:
                if height < 0:
                    continue
                for peer in members:
                    self._network.request_block_by_height(peer, height)
        except Exception:
            logger.exception("sync rescan failed")
