from __future__ import annotations

import logging

from blockchain.adapters.payloads import BlockInvPayload
from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    encode_coinbase_data,
    pack_timestamp_for_signing,
    pack_tx,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockHeader, BlockNode, Transaction
from blockchain.core.hashing import block_hash, header_mining_prefix, txs_hash
from blockchain.core.validation import MAX_BLOCK_BYTES, transfer_fee
from blockchain.ports.clock import ClockPort
from blockchain.ports.crypto import CryptoPort
from blockchain.ports.miner import MinerPort
from blockchain.ports.network import NetworkPort
from blockchain.ports.stores import MempoolPort, UTXOStorePort

logger = logging.getLogger(__name__)


class MiningService:
    """Listens for tip changes, builds candidates, drives the miner, and publishes found blocks."""

    def __init__(
        self,
        params: ConsensusParams,
        chain: object,  # ChainService — imported lazily to avoid circular import
        mempool: MempoolPort,
        utxo: UTXOStorePort,
        miner: MinerPort,
        network: NetworkPort,
        clock: ClockPort,
        crypto: CryptoPort,
        my_pubkey: bytes,
        my_privkey: bytes,
    ) -> None:
        self._params = params
        self._chain = chain
        self._mempool = mempool
        self._utxo = utxo
        self._miner = miner
        self._network = network
        self._clock = clock
        self._crypto = crypto
        self._my_pubkey = my_pubkey
        self._my_privkey = my_privkey
        self._generation = 0
        self._candidate: Block | None = None

    # ------------------------------------------------------------------
    # Public callback – wired into ChainService as on_tip_changed
    # ------------------------------------------------------------------

    def on_tip_changed(self, tip: BlockNode) -> None:
        self._generation += 1
        self._candidate = self._build_candidate(tip)
        prefix = header_mining_prefix(self._candidate.header)
        self._miner.mine(
            prefix,
            self._params.difficulty_bits,
            self._generation,
            self._on_found,
        )
        logger.info(
            "mining height=%d gen=%d on tip=%s",
            tip.height + 1,
            self._generation,
            tip.block_hash.hex()[:12],
        )

    def shutdown(self) -> None:
        self._miner.cancel()
        self._miner.shutdown()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_found(self, nonce: int, generation: int) -> None:
        if generation != self._generation or self._candidate is None:
            return
        candidate = self._candidate
        finalized = Block(
            header=BlockHeader(
                prev_hash=candidate.header.prev_hash,
                txs_hash=candidate.header.txs_hash,
                timestamp=candidate.header.timestamp,
                difficulty=candidate.header.difficulty,
                nonce=nonce,
            ),
            transactions=candidate.transactions,
        )
        digest = block_hash(finalized.header)
        result = self._chain.connect_block(finalized, source="self")  # type: ignore[attr-defined]
        if result.ok and result.reason not in ("already known", "orphan"):
            height = self._chain.height()  # type: ignore[attr-defined]
            logger.info("found block h=%d hash=%s", height, digest.hex()[:12])
            self._network.broadcast_members(BlockInvPayload(digest, height))

    def _make_coinbase(self, height: int, amount: int, timestamp: int) -> Transaction:
        coinbase_body = encode_coinbase_data(
            CoinbaseData(
                height=height,
                outputs=(
                    CoinbaseOutput(recipient_pubkey=self._my_pubkey, amount=amount),
                ),
            )
        )
        msg = self._my_pubkey + coinbase_body + pack_timestamp_for_signing(timestamp)
        signature = self._crypto.sign(self._my_privkey, msg)
        return Transaction(
            sender_key=self._my_pubkey,
            data=coinbase_body,
            timestamp=timestamp,
            signature=signature,
        )

    def _build_candidate(self, tip: BlockNode) -> Block:
        height = tip.height + 1
        reward = self._params.reward(height)

        timestamp = self._clock.now()
        # Ensure timestamp is strictly greater than parent block's timestamp
        if timestamp <= tip.block.header.timestamp:
            timestamp = tip.block.header.timestamp + 1

        # Size the coinbase against the reward-only output (its byte length barely
        # moves with the fee amount) so we can budget the mempool selection.
        coinbase_size = len(pack_tx(self._make_coinbase(height, reward, timestamp)))
        selected = self._mempool.select(MAX_BLOCK_BYTES - coinbase_size)

        # The coinbase must pay reward + total fees of the included transfers
        # (validate_coinbase_value), so compute fees against the current UTXO view.
        view = self._utxo.read_view()
        fees = 0
        kept: list[Transaction] = []
        for tx in selected:
            try:
                fees += transfer_fee(tx, view)
            except ValueError:
                # Input already spent relative to the tip — exclude rather than wedge.
                continue
            kept.append(tx)

        coinbase_tx = self._make_coinbase(height, reward + fees, timestamp)
        transactions: tuple[Transaction, ...] = (coinbase_tx, *kept)
        header = BlockHeader(
            prev_hash=tip.block_hash,
            txs_hash=txs_hash(transactions),
            timestamp=timestamp,
            difficulty=self._params.difficulty_bits,
            nonce=0,
        )
        return Block(header=header, transactions=transactions)
