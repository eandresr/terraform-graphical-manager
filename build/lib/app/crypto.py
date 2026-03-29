"""
Symmetric encryption for sensitive variable values.

Algorithm: Fernet (AES-128-CBC + HMAC-SHA-256).
Key:       SHA-256 of the portal plaintext password, base64url-encoded.

The plaintext password is used (not its PBKDF2 hash) so the same
password can encrypt and decrypt consistently across sessions.
"""
import base64
import hashlib


def _derive_key(password: str) -> bytes:
    """Return a 32-byte Fernet-compatible key derived from *password*."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext: str, password: str) -> str:
    """Encrypt *plaintext* and return a Fernet token string."""
    from cryptography.fernet import Fernet
    return Fernet(_derive_key(password)).encrypt(
        plaintext.encode("utf-8")
    ).decode("ascii")


def decrypt(ciphertext: str, password: str) -> str:
    """
    Decrypt *ciphertext*.  Raises ``ValueError`` on wrong password or
    corrupted data.
    """
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_derive_key(password)).decrypt(
            ciphertext.encode("ascii")
        ).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        raise ValueError(
            "Decryption failed — wrong password or corrupted value."
        ) from exc
