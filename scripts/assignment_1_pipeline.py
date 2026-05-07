import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ipv8.keyvault.crypto import default_eccrypto

from client import IPv8Client
from pow_utils import PoWUtils
from protocol import COMMUNITY_ID, SERVER_PUBLIC_KEY, ResponsePayload, SubmissionPayload

EMAIL      = "S.A.Minkov-1@student.tudelft.nl"
GITHUB_URL = "https://github.com/saminkov/CS4160_2026_Blockchain"

REPO_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
KEYS_DIR  = os.path.join(REPO_DIR, "keys")
KEY_PEM   = os.path.join(KEYS_DIR, "my_key.pem")
KEY_JSON  = os.path.join(KEYS_DIR, "my_key.json")
KEY_TXT   = os.path.join(KEYS_DIR, "my_key.txt")
NONCE_TXT = os.path.join(KEYS_DIR, "nonce.txt")


def _write_backups(priv_bin: bytes, pub_bin: bytes) -> None:
    data = {"private_key_hex": priv_bin.hex(), "public_key_hex": pub_bin.hex()}
    with open(KEY_JSON, "w") as f:
        json.dump(data, f, indent=4)
    with open(KEY_TXT, "w") as f:
        f.write(f"private_key_hex: {priv_bin.hex()}\n")
        f.write(f"public_key_hex:  {pub_bin.hex()}\n")


def ensure_keys() -> None:
    os.makedirs(KEYS_DIR, exist_ok=True)
    if os.path.exists(KEY_PEM):
        print("[Phase1] Loading existing key from keys/my_key.pem")
        with open(KEY_PEM, "rb") as f:
            priv_bin = f.read()
        key = default_eccrypto.key_from_private_bin(priv_bin)
        if not os.path.exists(KEY_JSON) or not os.path.exists(KEY_TXT):
            print("[Phase1] Regenerating missing key backups.")
            _write_backups(priv_bin, default_eccrypto.key_to_bin(key.pub()))
    else:
        print("[Phase1] Generating new curve25519 key pair...")
        key = default_eccrypto.generate_key("curve25519")
        priv_bin = default_eccrypto.key_to_bin(key)
        pub_bin  = default_eccrypto.key_to_bin(key.pub())
        with open(KEY_PEM, "wb") as f:
            f.write(priv_bin)
        _write_backups(priv_bin, pub_bin)
        print("[Phase1] Key saved to keys/ (pem + json + txt).")


def ensure_nonce() -> int:
    if os.path.exists(NONCE_TXT):
        with open(NONCE_TXT, "r") as f:
            cached = int(f.read().strip())
        if PoWUtils.verify(EMAIL, GITHUB_URL, cached):
            print(f"[Phase2] Cached nonce is valid: {cached}")
            return cached

    nonce, digest = PoWUtils.ass_1_proof_of_work(EMAIL, GITHUB_URL)
    with open(NONCE_TXT, "w") as f:
        f.write(str(nonce))
    print(f"[Phase2] PoW solved — nonce={nonce}, hash={digest.hex()}")
    return nonce


async def run() -> None:
    ensure_keys()
    nonce = ensure_nonce()

    print("[Phase3] Starting IPv8...")
    async with await IPv8Client.build(COMMUNITY_ID, KEY_PEM) as client:
        response: asyncio.Future = asyncio.get_event_loop().create_future()

        @client.on_message(ResponsePayload)
        def handle_response(peer, payload):
            if peer.public_key.key_to_bin() != SERVER_PUBLIC_KEY:
                return
            print(f"[Phase4] Server response — success={payload.success}, message='{payload.message}'")
            if not response.done():
                response.set_result((payload.success, payload.message))

        print("[Phase3] Waiting for server peer discovery; timeout=120s")
        server = await client.wait_for_peer(SERVER_PUBLIC_KEY, timeout=120.0)
        print("[Phase3] Server found. Sending submission")
        client.start_retry_send(server, SubmissionPayload(EMAIL, GITHUB_URL, nonce), response)

        try:
            success, message = await asyncio.wait_for(response, timeout=120.0)
            print(f"Status: { "ACCEPTED" if success else "REJECTED"}")
            print(f"Server message: {message}")
        except asyncio.TimeoutError:
            print("[Phase4] Timed out waiting for server response.")


if __name__ == "__main__":
    asyncio.run(run())
