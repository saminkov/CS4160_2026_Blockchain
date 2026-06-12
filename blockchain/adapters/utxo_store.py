from __future__ import annotations

from blockchain.core.codec import decode_coinbase_data, decode_transfer_data
from blockchain.core.entities import UTXO, Block, Outpoint, UndoRecord
from blockchain.core.hashing import tx_hash
from blockchain.core.validation import TxClass, classify_tx
from blockchain.ports.stores import UTXOStorePort, UTXOView


class _FrozenUTXOView(UTXOView):
    """Read-only snapshot of the UTXO set."""

    def __init__(self, utxos: dict[Outpoint, UTXO]) -> None:
        self._utxos = utxos

    def get(self, outpoint: Outpoint) -> UTXO | None:
        return self._utxos.get(outpoint)

    def is_unspent(self, outpoint: Outpoint) -> bool:
        return outpoint in self._utxos


class InMemoryUTXOStore(UTXOStorePort):
    """In-memory UTXO set keyed by outpoint, with undo support."""

    def __init__(self) -> None:
        self._utxos: dict[Outpoint, UTXO] = {}

    def get(self, outpoint: Outpoint) -> UTXO | None:
        return self._utxos.get(outpoint)

    def is_unspent(self, outpoint: Outpoint) -> bool:
        return outpoint in self._utxos

    def apply(self, block: Block, height: int) -> UndoRecord:
        spent: list[UTXO] = []
        created: list[Outpoint] = []

        for tx in block.transactions:
            h = tx_hash(tx)
            cls = classify_tx(tx)

            if cls == TxClass.COINBASE:
                cb_data = decode_coinbase_data(tx.data)
                for i, cb_out in enumerate(cb_data.outputs):
                    op = Outpoint(txid=h, index=i)
                    utxo = UTXO(
                        outpoint=op,
                        amount=cb_out.amount,
                        recipient_pubkey=cb_out.recipient_pubkey,
                        is_coinbase=True,
                        height_created=height,
                    )
                    self._utxos[op] = utxo
                    created.append(op)

            elif cls == TxClass.TRANSFER:
                tf_data = decode_transfer_data(tx.data)
                for inp in tf_data.inputs:
                    prev_op = Outpoint(txid=inp.prev_txid, index=inp.output_index)
                    try:
                        utxo = self._utxos.pop(prev_op)
                    except KeyError as e:
                        raise ValueError(f"missing input: {prev_op}") from e
                    spent.append(utxo)

                for i, tf_out in enumerate(tf_data.outputs):
                    op = Outpoint(txid=h, index=i)
                    utxo = UTXO(
                        outpoint=op,
                        amount=tf_out.amount,
                        recipient_pubkey=tf_out.recipient_pubkey,
                        is_coinbase=False,
                        height_created=height,
                    )
                    self._utxos[op] = utxo
                    created.append(op)

        return UndoRecord(spent=tuple(spent), created=tuple(created))

    def rollback(self, undo: UndoRecord) -> None:
        for utxo in undo.spent:
            self._utxos[utxo.outpoint] = utxo
        for outpoint in undo.created:
            self._utxos.pop(outpoint, None)

    def read_view(self) -> UTXOView:
        return _FrozenUTXOView(dict(self._utxos))
