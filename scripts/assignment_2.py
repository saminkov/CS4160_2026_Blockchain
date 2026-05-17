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
    SOFI_KEY_PUBLIC_KEY,
    ChallengeRequestPayload,
    ChallengeResponsePayload,
    GroupRegistrationPayload,
    GroupRegistrationResponsePayload,
    PeerSignaturePayload,
    RoundResultPayload,
    SignatureBundlePayload,
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
    _KEY_PEM: str = os.path.join(_KEYS_DIR, "my_key.pem")
    _GROUP_JSON: str = os.path.join(_KEYS_DIR, "group_registration.json")

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

    _IDENTITY_KEY_MAP: dict[str, str] = {
        "me": "my_key.pem",
        "polly": "polly_key.pem",
        "sofi": "sofi_key.pem",
    }

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

        if os.path.exists(cls._GROUP_JSON):
            try:
                with open(cls._GROUP_JSON) as f:
                    data = json.load(f)
                executor._group_id = data["group_id"]
                _logger.info("Loaded cached group_id=%s", executor._group_id)
            except (KeyError, json.JSONDecodeError):
                _logger.warning("Could not parse %s; group_id left unset", cls._GROUP_JSON)

        return executor

    async def discover_peers(self) -> None:
        """Discover the other 2 group peers and the server on the network (timeout=60s)."""
        remote_keys = [kb for num, kb in self._peer_pub_keys.items() if num != self._peer_me]
        all_keys = remote_keys + [SERVER_PUBLIC_KEY]
        _logger.info("Discovering %d peers (timeout=60s)…", len(all_keys))
        print("Discovering peers… (timeout=60s)")
        discovered = await self._client.discover_peers(all_keys, timeout=60.0)
        self._peer_objects = {}
        for num in self._peer_pub_keys:
            if num == self._peer_me:
                continue
            key_bin = self._peer_pub_keys[num]
            if key_bin not in discovered:
                _logger.error("Peer %d was not discovered (key not in results); skipping", num)
                continue
            self._peer_objects[num] = discovered[key_bin]
        self._server_peer = discovered[SERVER_PUBLIC_KEY]
        _logger.info("All peers discovered")
        print("All peers discovered.")

  

    def _register_handlers(self) -> None:
        """Register IPv8 message handlers for the protocol."""
        @self._client.on_message(GroupRegistrationResponsePayload)
        def _on_group_reg_response(peer: Peer, payload: GroupRegistrationResponsePayload) -> None:
            self._handle_group_reg_response(peer, payload)

        @self._client.on_message(ChallengeResponsePayload)
        def _on_challenge_response(peer: Peer, payload: ChallengeResponsePayload) -> None:
            self._handle_challenge_response(peer, payload)

        @self._client.on_message(PeerSignaturePayload)
        def _on_peer_signature(peer: Peer, payload: PeerSignaturePayload) -> None:
            self._handle_peer_signature(peer, payload)

        @self._client.on_message(RoundResultPayload)
        def _on_round_result(peer: Peer, payload: RoundResultPayload) -> None:
            self._handle_round_result(peer, payload)


    def _handle_group_reg_response(
        self, peer: Peer, payload: GroupRegistrationResponsePayload
    ) -> None:
        """
        Handle the server grouip registration response
        """
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        _logger.info(
            "Group registration response: success=%s, group_id=%s, message=%s",
            payload.success, payload.group_id, payload.message,
        )
        if payload.success:
            self._group_id = payload.group_id
            data = {
                "group_id": payload.group_id,
                "timestamp": time.time(),
                "message": payload.message,
            }
            with open(self._GROUP_JSON, "w") as f:
                json.dump(data, f, indent=4)
            _logger.info("Saved group registration to %s", self._GROUP_JSON)

        if self._reg_future and not self._reg_future.done():
            self._reg_future.set_result(payload)

    def _handle_challenge_response(
        self, peer: Peer, payload: ChallengeResponsePayload
    ) -> None:
        """Handle the server's challenge response for a round request."""
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        _logger.debug(
            "ChallengeResponse: round=%d, deadline=%.2f, nonce=%s",
            payload.round_number, payload.deadline, payload.nonce.hex(),
        )

        self._current_nonce = payload.nonce
        self._current_round = payload.round_number

        if payload.round_number >= self._round_number and payload.round_number != self._peer_me:
            my_key = self._client._community.my_peer.key
            sig = default_eccrypto.sign(my_key, payload.nonce)
            _logger.info(
                "Round %d: signing nonce and sending to designated peer %d",
                payload.round_number, payload.round_number,
            )
            target_peer = self._peer_objects[payload.round_number]
            sig_payload = PeerSignaturePayload(
                nonce=payload.nonce,
                round_number=payload.round_number,
                signature=sig,
            )
            asyncio.create_task(self._send_sig_with_retries(target_peer, sig_payload))

        elif payload.round_number == self._peer_me:
            my_key = self._client._community.my_peer.key
            my_sig = default_eccrypto.sign(my_key, payload.nonce)
            self._pending_sigs = {self._peer_me: my_sig}
            _logger.info(
                "Round %d: we are the designated submitter; awaiting peer signatures",
                payload.round_number,
            )

    async def _send_sig_with_retries(
        self, target: Peer, payload: PeerSignaturePayload
    ) -> None:
        """Send a PeerSignaturePayload to a target peer with retries."""
        for attempt in range(3):
            try:
                self._client.send(target, payload)
                _logger.debug("Sent PeerSignature attempt %d to %s", attempt + 1, target)
            except Exception:
                _logger.exception("Error sending PeerSignature attempt %d", attempt + 1)
            if attempt < 2:
                await asyncio.sleep(0.003)

    def _handle_peer_signature(self, peer: Peer, payload: PeerSignaturePayload) -> None:
        """Handle a PeerSignaturePayload from a peer."""
        if self._current_round != self._peer_me:
            return

        sender_key = peer.public_key.key_to_bin()
        sender_num: int | None = None
        for num, key_bin in self._peer_pub_keys.items():
            if key_bin == sender_key:
                sender_num = num
                break

        if sender_num is None:
            _logger.warning("Received PeerSignature from unknown peer; ignoring")
            return

        _logger.debug("Received signature from peer %d for round %d", sender_num, payload.round_number)
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
        """Handle a RoundResultPayload from the server."""
        if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
            return
        status = "SUCCESS" if payload.success else "FAILED"
        _logger.info(
            "RoundResult: %s | round=%d, completed=%d/%d | %s",
            status, payload.round_number, payload.rounds_completed, 3, payload.message,
        )
        if payload.success:
            self._round_number = payload.rounds_completed
        if payload.rounds_completed >= 3:
            _logger.info("All 3 rounds completed — stopping poll task")
            self._client._community.cancel_pending_task("_poll_rounds")

    async def send_group_registration(self) -> None:
        """Sends the group registration payload. ORDER: Stefan -> Polly -> Sofi"""
        loop = asyncio.get_running_loop()
        print("Registering group with server using peers 1, 2, 3 in order.")
        self._reg_future = loop.create_future()

        reg_payload = GroupRegistrationPayload(
            member1_key=self._peer_pub_keys[1],
            member2_key=self._peer_pub_keys[2],
            member3_key=self._peer_pub_keys[3],
        )
        self._client.start_retry_send(self._server_peer, reg_payload, self._reg_future, interval=10.0)
        _logger.info("Sent GroupRegistration; awaiting response (timeout=60s)…")

        try:
            response: GroupRegistrationResponsePayload = await asyncio.wait_for(
                self._reg_future, timeout=60.0
            )
            print(f"Registration: {'OK' if response.success else 'FAILED'} — {response.message}")
            if response.success:
                print(f"group_id = {response.group_id}")
        except asyncio.TimeoutError:
            _logger.error("Timed out waiting for group registration response")
            print("ERROR: timed out waiting for server response.")

    def send_round_request(self) -> None:
        """Sends a ChallengeRequestPayload to the server to request the next round."""
        if not self._group_id or self._server_peer is None or self._round_number >= 3:
            return
        self._client.send(self._server_peer, ChallengeRequestPayload(group_id=self._group_id))

    def start_polling(self) -> None:
        """Start the periodic polling of the server for round requests 100HZ."""
        if self._server_peer is None:
            raise ValueError("Cannot start polling: peers not discovered yet.")
        if not self._group_id:
            raise ValueError("Cannot start polling: group not registered yet.")
        _logger.info("Starting round-request polling (interval=10ms)")
        self._client._community.register_task(
            "_poll_rounds", self.send_round_request, interval=0.01, delay=0.0
        )

    async def run_assignment_2_cli(self) -> None:
        loop = asyncio.get_running_loop()
        print("Assignment 2 CLI ready. Commands: discover | walkto <ip> <port> | register_group | start | exit")
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
            elif cmd == "register_group":
                await self.send_group_registration()
            elif cmd == "start":
                try:
                    self.start_polling()
                    print("Polling started.")
                except ValueError as exc:
                    print(f"ERROR: {exc}")
            elif cmd in ("exit", "quit"):
                print("Exiting.")
                break
            elif cmd:
                print(f"Unknown command: {cmd!r}. Commands: discover | walkto <ip> <port> | register_group | start | exit")


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
