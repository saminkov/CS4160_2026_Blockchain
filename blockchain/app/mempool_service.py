from __future__ import annotations

import logging
import struct

from blockchain.adapters.payloads import (
    SubmitTransactionPayload,
    SubmitTransactionResponsePayload,
    TxGossipPayload,
    transaction_from_gossip,
    transaction_from_submit,
)
from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Result, Transaction
from blockchain.core.hashing import tx_hash
from blockchain.core.validation import TxClass, classify_tx, validate_transfer_tx
from blockchain.ports.crypto import CryptoPort
from blockchain.ports.network import NetworkPort
from blockchain.ports.stores import MempoolPort, UTXOStorePort

logger = logging.getLogger(__name__)

_TX_FEE = 0  # all incoming txs treated as fee-0 (data-carrier / unknown-UTXO)


class MempoolService:
    """Accepts transactions from the server and from peers; maintains the mempool."""

    def __init__(
        self,
        mempool: MempoolPort,
        network: NetworkPort,
        crypto: CryptoPort,
        params: ConsensusParams,
        utxo: UTXOStorePort,
        chain: object,  # ChainService — duck-typed for height(), avoids circular import
    ) -> None:
        self._mempool = mempool
        self._network = network
        self._crypto = crypto
        self._params = params
        self._utxo = utxo
        self._chain = chain

        network.register_handler(SubmitTransactionPayload, self._on_submit)
        network.register_handler(TxGossipPayload, self._on_gossip)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_submit(self, peer: bytes, payload: SubmitTransactionPayload) -> None:
        tx = transaction_from_submit(payload)
        digest = tx_hash(tx)

        if not self._verify_sig(tx):
            logger.warning("rejected tx %s: bad signature", digest.hex()[:12])
            self._network.send(
                peer,
                SubmitTransactionResponsePayload(False, digest, "bad signature"),
            )
            return

        already_known = self._mempool.contains(digest)
        if not already_known:
            check = self._passes_utxo_check(tx)
            if not check.ok:
                logger.warning("rejected tx %s: %s", digest.hex()[:12], check.reason)
                self._network.send(
                    peer,
                    SubmitTransactionResponsePayload(False, digest, check.reason),
                )
                return
            self._mempool.add(tx, _TX_FEE)
            logger.info("accepted tx %s from server", digest.hex()[:12])

        self._network.send(
            peer,
            SubmitTransactionResponsePayload(True, digest, "ok"),
        )

    def _on_gossip(self, peer: bytes, payload: TxGossipPayload) -> None:
        tx = transaction_from_gossip(payload)
        digest = tx_hash(tx)

        if self._mempool.contains(digest):
            return
        if not self._verify_sig(tx):
            logger.debug("dropped gossip tx %s: bad signature", digest.hex()[:12])
            return
        check = self._passes_utxo_check(tx)
        if not check.ok:
            logger.debug("dropped gossip tx %s: %s", digest.hex()[:12], check.reason)
            return

        self._mempool.add(tx, _TX_FEE)
        logger.debug("gossiped tx %s from peer %s", digest.hex()[:12], peer.hex()[:8])

        gossip = TxGossipPayload(
            sender_key=tx.sender_key,
            data=tx.data,
            timestamp=tx.timestamp,
            signature=tx.signature,
        )
        for member in self._params.member_pubkeys:
            if member != peer:
                self._network.send(member, gossip)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _passes_utxo_check(self, tx: Transaction) -> Result:
        """Reject transfers that spend a missing/already-spent UTXO (Task 18 / §8).

        Coinbase and data-carrier transactions carry no spendable inputs, so they
        skip the check and are accepted as fee-0 entries.
        """
        if classify_tx(tx) != TxClass.TRANSFER:
            return Result(True, "")
        view = self._utxo.read_view()
        next_height = self._chain.height() + 1  # type: ignore[attr-defined]
        return validate_transfer_tx(tx, next_height, self._params, view, spent_in_block=set())

    def _verify_sig(self, tx: Transaction) -> bool:
        msg = tx.sender_key + tx.data + struct.pack(">Q", tx.timestamp)
        return self._crypto.verify(tx.sender_key, msg, tx.signature)
