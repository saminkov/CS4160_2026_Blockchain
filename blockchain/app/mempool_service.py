from __future__ import annotations

import logging

from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Transaction
from blockchain.core.hashing import tx_hash
from blockchain.core.validation import (
    TxClass,
    classify_tx,
    transfer_fee,
    validate_transaction_signature,
    validate_transfer_tx,
)
from blockchain.ports.clock import ClockPort
from blockchain.ports.crypto import CryptoPort
from blockchain.ports.network import NetworkPort
from blockchain.ports.stores import BlockStorePort, MempoolPort, UTXOStorePort

logger = logging.getLogger(__name__)

# Per-transaction data cap 
MAX_DATA_BYTES = 65_536

SubmitResult = tuple[bool, bytes, str]


class MempoolService:
    """Transaction acceptance, classification, and gossip (single-writer on the loop)."""

    def __init__(
        self,
        params: ConsensusParams,
        mempool: MempoolPort,
        crypto: CryptoPort,
        network: NetworkPort,
        clock: ClockPort,
        blocks: BlockStorePort,
        utxo: UTXOStorePort,
        *,
        max_data_bytes: int = MAX_DATA_BYTES,
    ) -> None:
        self._params = params
        self._mempool = mempool
        self._crypto = crypto
        self._network = network
        self._clock = clock
        self._blocks = blocks
        self._utxo = utxo
        self._max_data_bytes = max_data_bytes

    def submit_tx(self, tx: Transaction, from_server: bool) -> SubmitResult:
        """Validate ``tx`` for the mempool, add it, gossip it. Returns (success, tx_hash, message)."""
        h = tx_hash(tx)
        try:
            return self._submit_tx(tx, h, from_server)
        except Exception:
            logger.exception("mempool submit crashed for tx %s", h.hex())
            return (False, h, "error: internal failure")

    def _submit_tx(self, tx: Transaction, h: bytes, from_server: bool) -> SubmitResult:
        if self._mempool.contains(h):
            return (True, h, "already in mempool")

        signature = validate_transaction_signature(tx, self._crypto.verify)
        if not signature.ok:
            logger.info("rejected tx %s: %s", h.hex(), signature.reason)
            return (False, h, signature.reason)

        if len(tx.data) > self._max_data_bytes:
            reason = f"data exceeds {self._max_data_bytes} bytes"
            logger.info("rejected tx %s: %s", h.hex(), reason)
            return (False, h, reason)

        horizon = self._clock.now() + self._params.timestamp_tolerance_seconds
        if tx.timestamp > horizon:
            reason = "timestamp too far in the future"
            logger.info("rejected tx %s: %s", h.hex(), reason)
            return (False, h, reason)

        tx_class = classify_tx(tx)
        if tx_class == TxClass.COINBASE:
            reason = "coinbase cannot be submitted to mempool"
            logger.info("rejected tx %s: %s", h.hex(), reason)
            return (False, h, reason)

        fee = 0
        if tx_class == TxClass.TRANSFER:
            view = self._utxo.read_view()
            tip = self._blocks.tip()
            next_height = 0 if tip is None else tip.height + 1
            transfer = validate_transfer_tx(
                tx,
                next_height,
                self._params,
                view,
                spent_in_block=set(),
            )
            if not transfer.ok:
                logger.info("rejected transfer tx %s: %s", h.hex(), transfer.reason)
                return (False, h, transfer.reason)
            fee = transfer_fee(tx, view)

        self._mempool.add(tx, fee)
        self._gossip(tx, h)
        logger.info(
            "accepted %s tx %s (fee=%d, from_server=%s)",
            tx_class.value,
            h.hex(),
            fee,
            from_server,
        )
        return (True, h, "accepted")

    def _gossip(self, tx: Transaction, h: bytes) -> None:
        try:
            self._network.gossip_tx(tx)
        except Exception:
            logger.exception("failed to gossip tx %s", h.hex())
