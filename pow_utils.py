import hashlib
import struct

from tqdm import tqdm

# 28 leading zero bits: hash as 256-bit int must be <= this value
# = 0x0000000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
DIFFICULTY_TARGET_28: int = (1 << 228) - 1


class PoWUtils:

    @staticmethod
    def proof_of_work(prefix: bytes, target: int) -> int:
        """Generic PoW"""
        buf = bytearray(prefix + b"\x00" * 8)
        offset = len(prefix)
        nonce = 0
        with tqdm(desc="[Phase2] Mining PoW", unit=" H", unit_scale=True) as bar:
            while True:
                struct.pack_into(">q", buf, offset, nonce)
                digest = hashlib.sha256(buf).digest()
                if int.from_bytes(digest, "big") <= target:
                    return nonce
                nonce += 1
                bar.update(1)

    @staticmethod
    def ass_1_proof_of_work(
        email: str, github_url: str, target: int = DIFFICULTY_TARGET_28
    ) -> tuple[int, bytes]:
        """Assignment-1 PoW over email + github_url."""
        prefix = email.encode("utf-8") + b"\n" + github_url.encode("utf-8") + b"\n"
        nonce = PoWUtils.proof_of_work(prefix, target)
        digest = hashlib.sha256(
            prefix + struct.pack(">q", nonce)
        ).digest()
        return nonce, digest

    @staticmethod
    def verify(
        email: str, github_url: str, nonce: int, target: int = DIFFICULTY_TARGET_28
    ) -> bool:
        prefix = email.encode("utf-8") + b"\n" + github_url.encode("utf-8") + b"\n"
        digest = hashlib.sha256(prefix + struct.pack(">q", nonce)).digest()
        return int.from_bytes(digest, "big") <= target
