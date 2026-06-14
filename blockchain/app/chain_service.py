from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from blockchain.app.validation_service import ValidationService
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockNode, Result, Transaction, UndoRecord
from blockchain.core.forkchoice import better_tip
from blockchain.core.hashing import block_hash, tx_hash
from blockchain.core.validation import TxClass, classify_tx, validate_transfer_tx
from blockchain.ports.clock import ClockPort
from blockchain.ports.stores import BlockStorePort, MempoolPort, UTXOStorePort

logger = logging.getLogger(__name__)

TipChangedCallback = Callable[[BlockNode], None]
ConnectSource = str


@dataclass(frozen=True, slots=True)
class BlockResponse:
    height: int
    prev_hash: bytes
    txs_hash: bytes
    timestamp: int
    difficulty: int
    nonce: int
    block_hash: bytes
    tx_hashes: bytes


def block_response_sentinel(height: int) -> BlockResponse:
    return BlockResponse(
        height=height,
        prev_hash=b"",
        txs_hash=b"",
        timestamp=0,
        difficulty=0,
        nonce=0,
        block_hash=b"",
        tx_hashes=b"",
    )


class ChainService:
    """Single-writer chain coordinator: connect, reorg, and tip queries"""

    def __init__(
        self,
        params: ConsensusParams,
        blocks: BlockStorePort,
        utxo: UTXOStorePort,
        mempool: MempoolPort,
        validation: ValidationService,
        clock: ClockPort,
        *,
        on_tip_changed: TipChangedCallback | None = None,
    ) -> None:
        self._params = params
        self._blocks = blocks
        self._utxo = utxo
        self._mempool = mempool
        self._validation = validation
        self._clock = clock
        self._on_tip_changed = on_tip_changed
        self._undos: dict[bytes, UndoRecord] = {}

    def initialize_genesis(self) -> Result:
        genesis = self._params.build_genesis()
        return self.connect_block(genesis.block, source="genesis")

    def height(self) -> int:
        tip = self._blocks.tip()
        return -1 if tip is None else tip.height

    def block_response_at(self, height: int) -> BlockResponse:
        node = self._blocks.node_at(height)
        if node is None:
            return block_response_sentinel(height)
        header = node.block.header
        return BlockResponse(
            height=height,
            prev_hash=header.prev_hash,
            txs_hash=header.txs_hash,
            timestamp=header.timestamp,
            difficulty=header.difficulty,
            nonce=header.nonce,
            block_hash=node.block_hash,
            tx_hashes=b"".join(tx_hash(tx) for tx in node.block.transactions),
        )

    def connect_block(self, block: Block, source: ConnectSource) -> Result:
        digest = block_hash(block.header)
        if self._blocks.has(digest):
            return Result(ok=True, reason="already known")

        parent = self._blocks.get(block.header.prev_hash)
        if parent is None and not self._is_genesis(block, digest):
            orphan = BlockNode(
                block=block,
                block_hash=digest,
                height=0,
                parent_hash=block.header.prev_hash,
            )
            self._blocks.add_orphan(orphan)
            logger.info("buffered orphan block %s from %s", digest.hex(), source)
            return Result(ok=True, reason="orphan")

        height = 0 if parent is None else parent.height + 1
        parent_header = parent.block.header if parent is not None else None
        parent_block_hash = parent.block_hash if parent is not None else None
        validation = self._validation.validate_block(
            block,
            height,
            parent_header=parent_header,
            parent_block_hash=parent_block_hash,
            now=self._clock.now(),
        )
        if not validation.ok:
            logger.info(
                "rejected block %s from %s: %s",
                digest.hex(),
                source,
                validation.reason,
            )
            return validation

        node = BlockNode(
            block=block,
            block_hash=digest,
            height=height,
            parent_hash=block.header.prev_hash,
        )
        self._blocks.add(node)

        current_tip = self._blocks.tip()
        winner = node if current_tip is None else better_tip(current_tip, node)
        tip_changed = current_tip is None or winner.block_hash != current_tip.block_hash
        if tip_changed:
            reorg = self._reorg_to_tip(winner)
            if not reorg.ok:
                return reorg

        self._resolve_orphans(digest)

        if tip_changed and self._on_tip_changed is not None:
            tip = self._blocks.tip()
            if tip is not None:
                self._on_tip_changed(tip)

        return Result(ok=True, reason="connected")

    def _is_genesis(self, block: Block, digest: bytes) -> bool:
        genesis = self._params.build_genesis()
        return digest == genesis.block_hash and block.header.prev_hash == genesis.block.header.prev_hash

    def _reorg_to_tip(self, new_tip: BlockNode) -> Result:
        old_tip = self._blocks.tip()
        if old_tip is not None and old_tip.block_hash == new_tip.block_hash:
            return Result(ok=True, reason="")

        if old_tip is None:
            disconnect: list[BlockNode] = []
            connect = list(reversed(self._blocks.ancestors(new_tip.block_hash)))
        else:
            ancestor = self._common_ancestor(old_tip, new_tip)
            if ancestor is None:
                return Result(ok=False, reason="missing common ancestor")
            disconnect = self._path_above(ancestor, old_tip)
            connect = list(reversed(self._path_above(ancestor, new_tip)))

        for node in disconnect:
            undo = self._undos.pop(node.block_hash, None)
            if undo is None:
                return Result(ok=False, reason="missing undo record")
            self._utxo.rollback(undo)
            self._restore_block_txs_to_mempool(node.block)
            self._blocks.remove_main_from(node.height)

        for node in connect:
            applied = self._apply_main_block(node)
            if not applied.ok:
                return applied

        self._reconcile_mempool()
        return Result(ok=True, reason="")

    def _apply_main_block(self, node: BlockNode) -> Result:
        parent = self._blocks.get(node.parent_hash)
        parent_header = parent.block.header if parent is not None else None
        parent_block_hash = parent.block_hash if parent is not None else None
        validation = self._validation.validate_block(
            node.block,
            node.height,
            parent_header=parent_header,
            parent_block_hash=parent_block_hash,
            now=self._clock.now(),
        )
        if not validation.ok:
            return validation

        undo = self._utxo.apply(node.block, node.height)
        self._undos[node.block_hash] = undo
        self._remove_block_txs_from_mempool(node.block)
        self._blocks.set_main(node.height, node.block_hash)
        return Result(ok=True, reason="")

    def _common_ancestor(
        self,
        old_tip: BlockNode | None,
        new_tip: BlockNode,
    ) -> BlockNode | None:
        if old_tip is None:
            return None
        old_ancestors = {node.block_hash: node for node in self._blocks.ancestors(old_tip.block_hash)}
        for node in self._blocks.ancestors(new_tip.block_hash):
            if node.block_hash in old_ancestors:
                return node
        return None

    def _path_above(self, ancestor: BlockNode, tip: BlockNode | None) -> list[BlockNode]:
        if tip is None or tip.block_hash == ancestor.block_hash:
            return []
        path: list[BlockNode] = []
        current: BlockNode | None = tip
        while current is not None and current.block_hash != ancestor.block_hash:
            path.append(current)
            current = self._blocks.get(current.parent_hash)
        return path

    def _resolve_orphans(self, parent_hash: bytes) -> None:
        for orphan in self._blocks.take_orphans(parent_hash):
            self.connect_block(orphan.block, source="orphan")

    def _restore_block_txs_to_mempool(self, block: Block) -> None:
        txs = tuple(tx for tx in block.transactions if classify_tx(tx) != TxClass.COINBASE)
        if txs:
            self._mempool.restore(txs)

    def _remove_block_txs_from_mempool(self, block: Block) -> None:
        for tx in block.transactions:
            if classify_tx(tx) == TxClass.COINBASE:
                continue
            self._mempool.remove(tx_hash(tx))

    def _reconcile_mempool(self) -> None:
        view = self._utxo.read_view()
        tip = self._blocks.tip()
        next_height = 0 if tip is None else tip.height + 1
        for tx in self._mempool.snapshot():
            if classify_tx(tx) != TxClass.TRANSFER:
                continue
            result = validate_transfer_tx(
                tx,
                next_height,
                self._params,
                view,
                spent_in_block=set(),
            )
            if not result.ok:
                self._mempool.remove(tx_hash(tx))
