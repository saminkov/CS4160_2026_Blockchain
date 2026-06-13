from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

from blockchain.core.consensus_params import ConsensusParams
from blockchain.core.entities import Block, BlockHeader, Result
from blockchain.core.validation import (
    MAX_BLOCK_BYTES,
    UTXOReader,
    validate_block_stateful,
    validate_block_stateless,
)
from blockchain.ports.crypto import CryptoPort
from blockchain.ports.stores import UTXOStorePort

DEFAULT_OFFLOAD_TX_THRESHOLD = 4


def _phase_a_task(
    block: Block,
    height: int,
    params: ConsensusParams,
    max_block_bytes: int,
) -> Result:
    from blockchain.adapters.ecc_crypto import ECCryptoAdapter

    crypto = ECCryptoAdapter()
    return validate_block_stateless(
        block,
        height,
        params,
        crypto.verify,
        max_block_bytes=max_block_bytes,
    )


class ValidationService:
    """Two-phase block validation orchestrator (A4)."""

    def __init__(
        self,
        params: ConsensusParams,
        pool: ProcessPoolExecutor | None,
        utxo_store: UTXOStorePort,
        crypto: CryptoPort,
        *,
        offload_tx_threshold: int = DEFAULT_OFFLOAD_TX_THRESHOLD,
        max_block_bytes: int = MAX_BLOCK_BYTES,
    ) -> None:
        self._params = params
        self._pool = pool
        self._utxo_store = utxo_store
        self._crypto = crypto
        self._offload_tx_threshold = offload_tx_threshold
        self._max_block_bytes = max_block_bytes

    def validate_phase_a(self, block: Block, height: int) -> Result:
        if self._pool is not None and len(block.transactions) >= self._offload_tx_threshold:
            future = self._pool.submit(
                _phase_a_task,
                block,
                height,
                self._params,
                self._max_block_bytes,
            )
            return future.result()
        return validate_block_stateless(
            block,
            height,
            self._params,
            self._crypto.verify,
            max_block_bytes=self._max_block_bytes,
        )

    def validate_phase_b(
        self,
        block: Block,
        height: int,
        *,
        parent_header: BlockHeader | None,
        parent_block_hash: bytes | None,
        now: int,
        utxo_view: UTXOReader | None = None,
    ) -> Result:
        view = utxo_view if utxo_view is not None else self._utxo_store.read_view()
        return validate_block_stateful(
            block,
            height,
            self._params,
            view,
            parent_header=parent_header,
            parent_block_hash=parent_block_hash,
            now=now,
        )

    def validate_block(
        self,
        block: Block,
        height: int,
        *,
        parent_header: BlockHeader | None,
        parent_block_hash: bytes | None,
        now: int,
        utxo_view: UTXOReader | None = None,
    ) -> Result:
        phase_a = self.validate_phase_a(block, height)
        if not phase_a.ok:
            return phase_a
        return self.validate_phase_b(
            block,
            height,
            parent_header=parent_header,
            parent_block_hash=parent_block_hash,
            now=now,
            utxo_view=utxo_view,
        )
