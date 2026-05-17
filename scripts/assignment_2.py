import argparse
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ipv8.keyvault.crypto import default_eccrypto
from ipv8.peer import Peer

from client import IPv8Client
from protocol_2 import (
    COMMUNITY_ID,
    SERVER_PUBLIC_KEY,
    ChallengeRequestPayload,
    ChallengeResponsePayload,
    GroupRegistrationPayload,
    GroupRegistrationResponsePayload,
    GroupIdPayload,
    GroupIdAckPayload,
    PeerSignaturePayload,
    RoundResultPayload,
    SignatureBundlePayload,
    SignatureRequestPayload,
    StartPollingPayload,
)

_REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_KEYS_DIR = os.path.join(_REPO_DIR, "keys")
_LOG_FILE = os.path.join(_REPO_DIR, "logs", "assignment_2.log")

_logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


class Assignment2Executor:

    _PEERS: dict[int, str] = {
        1: os.path.join(_KEYS_DIR, "my_key.pem.pub"),
        2: os.path.join(_KEYS_DIR, "polly_key.pem.pub"),
        3: os.path.join(_KEYS_DIR, "sofi_key.pem.pub"),
    }
    _GROUP_JSON: str = os.path.join(_KEYS_DIR, "group_registration.json")

    _IDENTITY_KEY_MAP: dict[str, str] = {
        "me": "my_key.pem",
        "polly": "polly_key.pem",
        "sofi": "sofi_key.pem",
    }

    def __init__(
        self,
        client: IPv8Client,
        peer_me: int,
        peer_pub_keys: dict[int, bytes],
    ) -> None:
        self._client = client
        self._peer_me = peer_me
        self._peer_pub_keys = peer_pub_keys
        self._peer_objects: dict[int, Peer] = {}
        self._server_peer: Peer | None = None

        self._group_id: str | None = None
        self._round_number: int = 0
        self._current_nonce: bytes | None = None
        self._current_round: int | None = None
        self._pending_sigs: dict[int, bytes] = {}
        self._reg_future: asyncio.Future | None = None
        self._ack_futures: dict[int, asyncio.Future] = {}

    @classmethod
    async def create(cls, identity: str = "me") -> "Assignment2Executor":
        _logger.info("Loading peer public keys from %s", _KEYS_DIR)
        peer_pub_keys: dict[int, bytes] = {}
        for num, path in cls._PEERS.items():
            with open(path, "rb") as f:
                raw = f.read()
            key = default_eccrypto.key_from_public_bin(raw)
            key_bin = default_eccrypto.key_to_bin(key)
            peer_pub_keys[num] = key_bin
            _logger.debug("Loaded peer %d: …%s", num, key_bin.hex()[20:40])

        key_pem = os.path.join(_KEYS_DIR, cls._IDENTITY_KEY_MAP[identity])
        _logger.info("Starting IPv8 node (community=%s, identity=%s)", COMMUNITY_ID.hex(), identity)
        client = await IPv8Client.build(COMMUNITY_ID, key_pem)

        my_pub_bin = client._community.my_peer.public_key.key_to_bin()
        peer_me: int | None = None
        for num, key_bin in peer_pub_keys.items():
            if key_bin == my_pub_bin:
                peer_me = num
                break
        if peer_me is None:
            raise RuntimeError(
                "Could not match our public key against any of the 3 peers. "
                "Check that the correct .pem.pub files are in keys/."
            )
        _logger.info("We are peer %d", peer_me)

        executor = cls(client, peer_me, peer_pub_keys)
        executor._register_handlers()
        return executor

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    async def discover_peers(self) -> None:
        """Discover the other 2 group peers and the server (timeout=60s)."""
        remote_keys = [kb for num, kb in self._peer_pub_keys.items() if num != self._peer_me]
        all_keys = [SERVER_PUBLIC_KEY] + remote_keys
        _logger.info("Discovering %d peers (timeout=60s)…", len(all_keys))
        print("Discovering peers… (timeout=60s)")
        discovered = await self._client.discover_peers(all_keys, timeout=60.0)
        self._peer_objects = {}
        for num in self._peer_pub_keys:
            if num == self._peer_me:
                continue
            key_bin = self._peer_pub_keys[num]
            if key_bin not in discovered:
                _logger.error("Peer %d not discovered; skipping", num)
                continue
            self._peer_objects[num] = discovered[key_bin]
        self._server_peer = discovered[SERVER_PUBLIC_KEY]
        _logger.info("All peers discovered")
        print("All peers discovered.")

    # -------------------------------------------------------------------------
    # Message handlers
    # -------------------------------------------------------------------------

    def _register_handlers(self) -> None:
        @self._client.on_message(GroupRegistrationResponsePayload)
        def _(peer: Peer, payload: GroupRegistrationResponsePayload) -> None:
            self._handle_group_reg_response(peer, payload)

        @self._client.on_message(GroupIdPayload)
        def _(peer: Peer, payload: GroupIdPayload) -> None:
            self._handle_group_id(peer, payload)

        @self._client.on_message(GroupIdAckPayload)
        def _(peer: Peer, payload: GroupIdAckPayload) -> None:
            self._handle_group_id_ack(peer, payload)

        @self._client.on_message(ChallengeResponsePayload)
        def _(peer: Peer, payload: ChallengeResponsePayload) -> None:
            self._handle_challenge_response(peer, payload)

        @self._client.on_message(SignatureRequestPayload)
        def _(peer: Peer, payload: SignatureRequestPayload) -> None:
            self._handle_signature_request(peer, payload)

        @self._client.on_message(PeerSignaturePayload)
        def _(peer: Peer, payload: PeerSignaturePayload) -> None:
            self._handle_peer_signature(peer, payload)

        @self._client.on_message(RoundResultPayload)
        def _(peer: Peer, payload: RoundResultPayload) -> None:
            self._handle_round_result(peer, payload)

        @self._client.on_message(StartPollingPayload)
        def _(peer: Peer, payload: StartPollingPayload) -> None:
            self._handle_start_polling(peer, payload)

    def _handle_group_reg_response(
        self, peer: Peer, payload: GroupRegistrationResponsePayload
    ) -> None:
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        _logger.info(
            "Group registration response: success=%s, group_id=%s, message=%s",
            payload.success, payload.group_id, payload.message,
        )
        if payload.success:
            self._group_id = payload.group_id
            data = {"group_id": payload.group_id, "timestamp": time.time(), "message": payload.message}
            with open(self._GROUP_JSON, "w") as f:
                json.dump(data, f, indent=4)
            _logger.info("Saved group_id to %s", self._GROUP_JSON)
        if self._reg_future and not self._reg_future.done():
            self._reg_future.set_result(payload)

    def _handle_group_id(self, peer: Peer, payload: GroupIdPayload) -> None:
        """Peers 2 & 3: receive group_id from peer 1, save it, send ack."""
        sender_key = peer.public_key.key_to_bin()
        sender_num = next((n for n, k in self._peer_pub_keys.items() if k == sender_key), None)
        if sender_num is None:
            _logger.warning("GroupIdPayload from unknown peer; ignoring")
            return
        self._group_id = payload.group_id
        data = {"group_id": payload.group_id, "timestamp": time.time(), "message": f"received from peer {sender_num}"}
        with open(self._GROUP_JSON, "w") as f:
            json.dump(data, f, indent=4)
        _logger.info("Received group_id=%s from peer %d; saved, sending ack", payload.group_id, sender_num)
        print(f"Received group_id from peer {sender_num}: {payload.group_id}")
        self._client.send(peer, GroupIdAckPayload(group_id=payload.group_id))

    def _handle_group_id_ack(self, peer: Peer, payload: GroupIdAckPayload) -> None:
        """Peer 1: receive ack from a peer confirming they have the group_id."""
        sender_key = peer.public_key.key_to_bin()
        sender_num = next((n for n, k in self._peer_pub_keys.items() if k == sender_key), None)
        if sender_num is None:
            _logger.warning("GroupIdAck from unknown peer; ignoring")
            return
        _logger.info("GroupIdAck from peer %d for group_id=%s", sender_num, payload.group_id)
        fut = self._ack_futures.get(sender_num)
        if fut and not fut.done():
            fut.set_result(True)

    def _handle_challenge_response(
        self, peer: Peer, payload: ChallengeResponsePayload
    ) -> None:
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        _logger.debug(
            "ChallengeResponse: round=%d, deadline=%.2f, nonce=%s",
            payload.round_number, payload.deadline, payload.nonce.hex(),
        )
        if payload.round_number != self._peer_me:
            _logger.warning(
                "Challenge for round %d but we are peer %d; ignoring",
                payload.round_number, self._peer_me,
            )
            return

        self._client._community.cancel_pending_task("_poll_rounds")
        _logger.info("Round %d: challenge received, polling stopped", payload.round_number)

        self._current_nonce = payload.nonce
        self._current_round = payload.round_number

        my_key = self._client._community.my_peer.key
        my_sig = default_eccrypto.create_signature(my_key, payload.nonce)
        self._pending_sigs = {self._peer_me: my_sig}
        _logger.info(
            "Round %d: signed nonce, forwarding SignatureRequest to peers %s",
            payload.round_number,
            [n for n in self._peer_objects if n != self._peer_me],
        )

        sig_req = SignatureRequestPayload(nonce=payload.nonce, round_number=payload.round_number)
        for num, peer_obj in self._peer_objects.items():
            if num != self._peer_me:
                asyncio.create_task(
                    self._send_with_retries(peer_obj, sig_req, label=f"SignatureRequest→peer{num}")
                )

    def _handle_signature_request(self, peer: Peer, payload: SignatureRequestPayload) -> None:
        """Receive nonce from submitter, sign it, return PeerSignaturePayload."""
        sender_key = peer.public_key.key_to_bin()
        sender_num = next((n for n, k in self._peer_pub_keys.items() if k == sender_key), None)
        if sender_num is None:
            _logger.warning("SignatureRequest from unknown peer; ignoring")
            return
        _logger.info(
            "Round %d: SignatureRequest from peer %d, signing nonce",
            payload.round_number, sender_num,
        )
        my_key = self._client._community.my_peer.key
        sig = default_eccrypto.create_signature(my_key, payload.nonce)
        asyncio.create_task(
            self._send_with_retries(
                peer,
                PeerSignaturePayload(nonce=payload.nonce, round_number=payload.round_number, signature=sig),
                label=f"PeerSignature→peer{sender_num}",
            )
        )

    def _handle_peer_signature(self, peer: Peer, payload: PeerSignaturePayload) -> None:
        """Collect a peer signature; submit the bundle once all 3 are in."""
        if self._current_round != self._peer_me:
            return
        sender_key = peer.public_key.key_to_bin()
        sender_num = next((n for n, k in self._peer_pub_keys.items() if k == sender_key), None)
        if sender_num is None:
            _logger.warning("PeerSignature from unknown peer; ignoring")
            return
        _logger.debug("Signature from peer %d for round %d", sender_num, payload.round_number)
        self._pending_sigs[sender_num] = payload.signature

        if len(self._pending_sigs) == 3:
            _logger.info("All 3 signatures collected; submitting bundle for round %d", self._current_round)
            bundle = SignatureBundlePayload(
                group_id=self._group_id,
                round_number=self._current_round,
                sig1=self._pending_sigs[1],
                sig2=self._pending_sigs[2],
                sig3=self._pending_sigs[3],
            )
            self._client.send(self._server_peer, bundle)

    def _handle_round_result(self, peer: Peer, payload: RoundResultPayload) -> None:
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        status = "SUCCESS" if payload.success else "FAILED"
        _logger.info(
            "RoundResult: %s | round=%d, completed=%d/3 | %s",
            status, payload.round_number, payload.rounds_completed, payload.message,
        )
        if not payload.success:
            return

        self._round_number = payload.rounds_completed

        if payload.rounds_completed >= 3:
            _logger.info("All 3 rounds completed — protocol done.")
            self._client._community.cancel_pending_task("_poll_rounds")
            return

        # Only the peer who just submitted sends the handoff.
        if payload.round_number != self._peer_me:
            return

        next_peer_num = (payload.rounds_completed % 3) + 1
        if next_peer_num in self._peer_objects:
            _logger.info(
                "Round %d done; handing off to peer %d",
                payload.rounds_completed, next_peer_num,
            )
            asyncio.create_task(
                self._send_with_retries(
                    self._peer_objects[next_peer_num],
                    StartPollingPayload(round_number=next_peer_num),
                    label=f"StartPolling→peer{next_peer_num}",
                )
            )
        else:
            _logger.warning("Next peer %d not in peer_objects; cannot hand off", next_peer_num)

    def _handle_start_polling(self, peer: Peer, payload: StartPollingPayload) -> None:
        """Receive handoff from previous submitter; begin polling for our round."""
        sender_key = peer.public_key.key_to_bin()
        sender_num = next((n for n, k in self._peer_pub_keys.items() if k == sender_key), None)
        if payload.round_number != self._peer_me:
            _logger.warning(
                "StartPolling for round %d received but we are peer %d; ignoring",
                payload.round_number, self._peer_me,
            )
            return
        _logger.info(
            "StartPolling from peer %s for round %d; starting polling",
            sender_num, payload.round_number,
        )
        self._start_polling_task()

    # -------------------------------------------------------------------------
    # Polling
    # -------------------------------------------------------------------------

    def send_round_request(self) -> None:
        if not self._group_id or self._server_peer is None or self._round_number >= 3:
            return
        self._client.send(self._server_peer, ChallengeRequestPayload(group_id=self._group_id))

    def _start_polling_task(self) -> None:
        _logger.info("Peer %d starting round-request polling (interval=1s)", self._peer_me)
        self._client._community.register_task(
            "_poll_rounds", self.send_round_request, interval=1.0, delay=0.0
        )

    # -------------------------------------------------------------------------
    # Start sequence (peer 1 only)
    # -------------------------------------------------------------------------

    async def run_start(self) -> None:
        """Full start sequence: register group → share group_id → start polling."""
        if self._server_peer is None or not self._peer_objects:
            raise ValueError("Run 'discover' first.")
        if self._peer_me != 1:
            raise ValueError(
                f"Only peer 1 runs 'start' (we are peer {self._peer_me}). "
                "Other peers are triggered automatically."
            )

        # Step 1: register group with server.
        _logger.info("Step 1/3: registering group with server")
        print("Registering group with server…")
        loop = asyncio.get_running_loop()
        self._reg_future = loop.create_future()
        reg_payload = GroupRegistrationPayload(
            member1_key=self._peer_pub_keys[1],
            member2_key=self._peer_pub_keys[2],
            member3_key=self._peer_pub_keys[3],
        )
        self._client.start_retry_send(self._server_peer, reg_payload, self._reg_future, interval=10.0)
        try:
            response: GroupRegistrationResponsePayload = await asyncio.wait_for(
                self._reg_future, timeout=60.0
            )
        except asyncio.TimeoutError:
            _logger.error("Timed out waiting for group registration response")
            print("ERROR: timed out waiting for server registration response.")
            return
        print(f"Registration: {'OK' if response.success else 'FAILED'} — {response.message}")
        if not response.success:
            return
        print(f"group_id = {self._group_id}")

        # Step 2: share group_id with peers 2 & 3 and wait for acks.
        _logger.info("Step 2/3: sharing group_id with peers 2 & 3")
        print("Sharing group_id with peers 2 & 3…")
        other_peers = [n for n in self._peer_objects if n != self._peer_me]
        self._ack_futures = {n: loop.create_future() for n in other_peers}

        gid_payload = GroupIdPayload(group_id=self._group_id)
        for num in other_peers:
            asyncio.create_task(
                self._send_with_retries(
                    self._peer_objects[num], gid_payload,
                    label=f"GroupId→peer{num}", attempts=6, delay=5.0,
                )
            )

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._ack_futures.values()),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            missing = [n for n, f in self._ack_futures.items() if not f.done()]
            _logger.error("Timed out waiting for GroupIdAck from peers %s", missing)
            print(f"ERROR: peers {missing} did not acknowledge group_id within 60s.")
            return

        _logger.info("All peers acknowledged group_id")
        print("All peers acknowledged group_id.")

        # Step 3: start polling.
        _logger.info("Step 3/3: starting polling for round 1")
        print("Starting polling for round 1…")
        self._start_polling_task()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    async def _send_with_retries(
        self, target: Peer, payload, *, label: str, attempts: int = 3, delay: float = 0.05
    ) -> None:
        for i in range(attempts):
            try:
                self._client.send(target, payload)
                _logger.debug("%s: sent (attempt %d)", label, i + 1)
            except Exception:
                _logger.exception("%s: error on attempt %d", label, i + 1)
            if i < attempts - 1:
                await asyncio.sleep(delay)

    # -------------------------------------------------------------------------
    # CLI
    # -------------------------------------------------------------------------

    async def run_assignment_2_cli(self) -> None:
        loop = asyncio.get_running_loop()
        print(
            f"Assignment 2 CLI ready (we are peer {self._peer_me}). "
            "Commands: discover | walkto <ip> <port> | start (peer 1 only) | exit"
        )
        while True:
            try:
                line: str = await loop.run_in_executor(None, input, "> ")
            except EOFError:
                break
            parts = line.strip().split()
            cmd = parts[0].lower() if parts else ""
            if cmd == "discover":
                try:
                    await self.discover_peers()
                except Exception as exc:
                    _logger.exception("Peer discovery failed")
                    print(f"ERROR: {exc}")
            elif cmd == "walkto":
                if len(parts) != 3:
                    print("Usage: walkto <ip> <port>")
                else:
                    addr = (parts[1], int(parts[2]))
                    self._client._community.walk_to(addr)
                    print(f"Sent introduction request to {addr}")
            elif cmd == "start":
                try:
                    await self.run_start()
                except ValueError as exc:
                    print(f"ERROR: {exc}")
            elif cmd in ("exit", "quit"):
                print("Exiting.")
                break
            elif cmd:
                print(
                    f"Unknown command: {cmd!r}. "
                    "Commands: discover | walkto <ip> <port> | start (peer 1 only) | exit"
                )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Assignment 2")
    parser.add_argument(
        "--identity",
        choices=["me", "polly", "sofi"],
        default="me",
        help="Which private key to use (default: me)",
    )
    args = parser.parse_args()

    _setup_logging()
    executor = await Assignment2Executor.create(identity=args.identity)
    try:
        await executor.run_assignment_2_cli()
    finally:
        await executor._client.stop()


if __name__ == "__main__":
    asyncio.run(main())
