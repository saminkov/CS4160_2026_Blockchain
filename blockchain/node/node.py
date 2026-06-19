from __future__ import annotations

import asyncio
import contextlib
import logging
from concurrent.futures import ProcessPoolExecutor

from blockchain.adapters.block_store import InMemoryBlockStore
from blockchain.adapters.ecc_crypto import ECCryptoAdapter
from blockchain.adapters.ipv8_network import (
    IPv8NetworkBundle,
    build_ipv8,
    connect_to_peers,
)
from blockchain.adapters.mempool import InMemoryMempool
from blockchain.adapters.process_miner import ProcessMiner
from blockchain.adapters.system_clock import SystemClock
from blockchain.adapters.utxo_store import InMemoryUTXOStore
from blockchain.app.chain_service import ChainService
from blockchain.app.mempool_service import MempoolService
from blockchain.app.mining_service import MiningService
from blockchain.app.registration_service import RegistrationService
from blockchain.app.sync_service import SyncService
from blockchain.app.validation_service import ValidationService
from blockchain.core.entities import BlockNode
from blockchain.logging_setup import configure_logging, set_logging_height
from blockchain.node.config import NodeConfig

logger = logging.getLogger(__name__)

_STATUS_INTERVAL = 10.0  # seconds between status log lines


class _TipCallbackHolder:
    """Forwarding proxy so ChainService can be built before MiningService."""

    def __init__(self) -> None:
        self._inner: object | None = None

    def set(self, cb: object) -> None:
        self._inner = cb

    def __call__(self, tip: BlockNode) -> None:
        if self._inner is not None:
            self._inner(tip)  # type: ignore[operator]


class Node:
    """Top-level orchestrator: wires adapters → services → IPv8."""

    def __init__(self, config: NodeConfig) -> None:
        self._config = config

        # Adapters that don't need IPv8
        self._block_store = InMemoryBlockStore()
        self._utxo_store = InMemoryUTXOStore()
        self._mempool = InMemoryMempool()
        self._clock = SystemClock()
        self._crypto = ECCryptoAdapter()
        self._miner = ProcessMiner()
        self._pool = ProcessPoolExecutor()  # stateless Phase-A validation offload

        # Filled in start()
        self._bundle: IPv8NetworkBundle | None = None
        self._chain: ChainService | None = None
        self._mining: MiningService | None = None
        self._status_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        cfg = self._config

        # 1. Configure logging
        member_label = (
            f"m{cfg.member_index}" if cfg.member_index is not None else "node"
        )
        configure_logging(
            level=getattr(logging, cfg.log_level.upper(), logging.INFO),
            member=member_label,
        )
        logger.info(
            "starting node group=%s member=%s port=%d",
            cfg.params.group_id,
            member_label,
            cfg.port,
        )

        # 2. Start IPv8
        self._bundle = await build_ipv8(cfg.key_path, cfg.params, port=cfg.port)
        if cfg.peers:
            connect_to_peers(self._bundle, cfg.peers)
        elif len(cfg.params.member_pubkeys) > 1:
            logger.warning(
                "no --peer addresses configured; other members must be reachable "
                "via host:port (required when running on separate machines)"
            )
        network = self._bundle.network
        registration_port = self._bundle.registration

        # 3. Build services (order matters: ChainService before Mining)
        validation = ValidationService(
            cfg.params, self._pool, self._utxo_store, self._crypto
        )

        tip_holder = _TipCallbackHolder()
        self._chain = ChainService(
            cfg.params,
            self._block_store,
            self._utxo_store,
            self._mempool,
            validation,
            self._clock,
            on_tip_changed=tip_holder,
        )

        self._mining = MiningService(
            cfg.params,
            self._chain,
            self._mempool,
            self._miner,
            network,
            self._clock,
            self._crypto,
            cfg.pubkey,
            cfg.privkey,
        )
        tip_holder.set(self._mining.on_tip_changed)

        MempoolService(
            self._mempool, network, self._crypto, cfg.params, self._utxo_store, self._chain
        )
        SyncService(self._chain, network, cfg.params)
        RegistrationService(cfg.params, network, registration_port, cfg.is_registrar)

        # 4. Connect genesis (fires tip_changed → starts mining)
        result = self._chain.initialize_genesis()
        if not result.ok:
            logger.warning("genesis already connected: %s", result.reason)

        # 5. Periodic status line
        self._status_task = asyncio.get_event_loop().create_task(
            self._status_loop(), name="node_status"
        )

        logger.info("node started — height=%d", self._chain.height())

    async def stop(self) -> None:
        logger.info("node shutting down …")

        if self._status_task is not None:
            self._status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._status_task

        if self._mining is not None:
            self._mining.shutdown()

        self._pool.shutdown(wait=False, cancel_futures=True)

        if self._bundle is not None:
            await self._bundle.stop()

        logger.info("node stopped")

    # ------------------------------------------------------------------
    # Periodic status line
    # ------------------------------------------------------------------

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(_STATUS_INTERVAL)
            self._emit_status()

    def _emit_status(self) -> None:
        if self._chain is None or self._bundle is None:
            return
        height = self._chain.height()
        set_logging_height(height)
        mempool_size = len(self._mempool)
        expected_peers = len(self._config.params.member_pubkeys) - 1
        online = len(self._bundle.network.members_online())
        logger.info(
            "status — height=%d mempool=%d peers=%d/%d",
            height,
            mempool_size,
            online,
            expected_peers,
        )
        if online < expected_peers and self._config.peers:
            connect_to_peers(self._bundle, self._config.peers)
