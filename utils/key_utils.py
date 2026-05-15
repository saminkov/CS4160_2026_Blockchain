import os

from ipv8.keyvault.crypto import default_eccrypto
from ipv8.keyvault.keys import Key


class KeyUtils:

    @staticmethod
    def load_curve25519_key(pem_path: str) -> tuple[Key, Key]:
        """Load a curve25519 key pair from *pem_path*.

        Returns (private_key, public_key).
        """
        with open(pem_path, "rb") as f:
            priv_bin = f.read()

        private_key = default_eccrypto.key_from_private_bin(priv_bin)

        pub_path = pem_path + ".pub"
        if os.path.exists(pub_path):
            with open(pub_path, "rb") as f:
                pub_bin = f.read()
            public_key = default_eccrypto.key_from_public_bin(pub_bin)
        else:
            public_key = private_key.pub()

        return private_key, public_key
    
    @staticmethod
    def sign_message(private_key: Key, message: bytes) -> bytes:
        """Sign *message* with *private_key*."""
        return default_eccrypto.sign(private_key, message)
    
    @staticmethod
    def verify_signature(public_key: Key, message: bytes, signature: bytes) -> bool:
        """Verify that *signature* on *message* is valid for *public_key*."""
        return default_eccrypto.verify(public_key, message, signature)
