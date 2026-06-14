from __future__ import annotations

import logging

from blockchain.app.chain_service import ChainService
from blockchain.core.codec import (
    CoinbaseData,
    CoinbaseOutput,
    encode_coinbase_data,
    pack_tx,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import (
    HEADER_SIZE,
    Block,
    BlockHeader,
    BlockNode,
    Outpoint,
    Transaction,
)
from blockchain.core.hashing import block_hash, header_mining_prefix, txs_hash
from blockchain.core.validation import (
    MAX_BLOCK_BYTES,
    TxClass,
    classify_tx,
    signing_message,
    transfer_fee,
    validate_transfer_tx,
)
from blockchain.ports.clock import ClockPort
from blockchain.ports.crypto import CryptoPort
from blockchain.ports.miner import MinerPort
from blockchain.ports.stores import MempoolPort, UTXOStorePort

logger = logging.getLogger(__name__)

# Body framing overhead in front of the concatenated transactions (uint32 tx count).
_BODY_COUNT_PREFIX = 4


class MiningService:
    """Continuous miner: build candidate, drive MinerPort, assemble + connect (A3, D2/D3)."""

    def __init__(
        self,
        params: ConsensusParams,
        mempool: MempoolPort,
        chain: ChainService,
        miner: MinerPort,
        clock: ClockPort,
        crypto: CryptoPort,
        utxo: UTXOStorePort,
        *,
        miner_pubkey: bytes,
        miner_privkey: bytes,
        max_block_bytes: int = MAX_BLOCK_BYTES,
    ) -> None:
        self._params = params
        self._mempool = mempool
        self._chain = chain
        self._miner = miner
        self._clock = clock
        self._crypto = crypto
        self._utxo = utxo
        self._miner_pubkey = miner_pubkey
        self._miner_privkey = miner_privkey
        self._max_block_bytes = max_block_bytes

        self._tip: BlockNode | None = None
        self._generation = 0
        self._candidate_txs: tuple[Transaction, ...] = ()
        self._candidate_header: BlockHeader | None = None
        self._mining = False

    def on_tip_changed(self, tip: BlockNode) -> None:
        """Primary driver: ChainService calls this after genesis init and every accepted block."""
        self._tip = tip
        self._start_candidate()

    def supervise(self) -> None:
        """Fail-safe (register_task): restart a candidate if mining has stalled."""
        if self._tip is not None and not self._mining:
            self._start_candidate()

    def _start_candidate(self) -> None:
        tip = self._tip
        if tip is None:
            return
        try:
            height = tip.height + 1
            timestamp = max(self._clock.now(), tip.block.header.timestamp + 1)

            provisional = self._sign_coinbase(height, self._params.reward(height), timestamp)
            overhead = HEADER_SIZE + _BODY_COUNT_PREFIX + len(pack_tx(provisional))
            size_limit = self._max_block_bytes - overhead

            kept, fees = self._select_transfers(height, size_limit)
            coinbase = self._sign_coinbase(height, self._params.reward(height) + fees, timestamp)
            transactions = (coinbase, *kept)

            header = BlockHeader(
                prev_hash=tip.block_hash,
                txs_hash=txs_hash(transactions),
                timestamp=timestamp,
                difficulty=self._params.difficulty_bits,
                nonce=0,
            )
            self._candidate_txs = transactions
            self._candidate_header = header
            self._generation += 1
            self._mining = True
            self._miner.mine(
                header_mining_prefix(header),
                self._params.difficulty_bits,
                self._generation,
                self.on_found,
            )
            logger.debug(
                "mining candidate h=%d gen=%d txs=%d", height, self._generation, len(transactions)
            )
        except Exception:
            logger.exception("failed to build mining candidate")

    def _select_transfers(
        self, height: int, size_limit: int
    ) -> tuple[tuple[Transaction, ...], int]:
        if size_limit <= 0:
            return (), 0
        view = self._utxo.read_view()
        spent: set[Outpoint] = set()
        kept: list[Transaction] = []
        fees = 0
        for tx in self._mempool.select(size_limit):
            tx_class = classify_tx(tx)
            if tx_class == TxClass.DATA_CARRIER:
                kept.append(tx)
            elif tx_class == TxClass.TRANSFER:
                result = validate_transfer_tx(tx, height, self._params, view, spent)
                if result.ok:
                    kept.append(tx)
                    fees += transfer_fee(tx, view)
        return tuple(kept), fees

    def _sign_coinbase(self, height: int, amount: int, timestamp: int) -> Transaction:
        data = encode_coinbase_data(
            CoinbaseData(
                height=height,
                outputs=(CoinbaseOutput(recipient_pubkey=self._miner_pubkey, amount=amount),),
            )
        )
        unsigned = Transaction(
            sender_key=self._miner_pubkey,
            data=data,
            timestamp=timestamp,
            signature=b"",
        )
        signature = self._crypto.sign(self._miner_privkey, signing_message(unsigned))
        return Transaction(
            sender_key=self._miner_pubkey,
            data=data,
            timestamp=timestamp,
            signature=signature,
        )

    def on_found(self, nonce: int, generation: int) -> None:
        """Runs on the loop (via the miner bridge) when a satisfying nonce is found."""
        if generation != self._generation:
            return
        self._mining = False
        header = self._candidate_header
        if header is None:
            return
        try:
            block = Block(
                header=BlockHeader(
                    prev_hash=header.prev_hash,
                    txs_hash=header.txs_hash,
                    timestamp=header.timestamp,
                    difficulty=header.difficulty,
                    nonce=nonce,
                ),
                transactions=self._candidate_txs,
            )
            logger.info("mined block %s", block_hash(block.header).hex())
            gen_before = self._generation
            self._chain.connect_block(block, source="local")
            # A valid self-mined block becomes the tip and reentrantly rebuilds via
            # on_tip_changed; if it did not, keep mining so we never stall.
            if self._generation == gen_before:
                self._start_candidate()
        except Exception:
            logger.exception("failed to assemble or connect mined block")
