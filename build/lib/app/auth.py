"""
Portal lock authentication helpers.

Passwords are hashed with PBKDF2-HMAC-SHA256 (no extra deps).
API tokens are HMAC-SHA256(key=app_secret, msg=password_hash), so they
are invalidated automatically when the password changes.
"""
import hashlib
import hmac
import secrets

_ITERATIONS = 260_000
_ALGORITHM = "sha256"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a storable PBKDF2 hash string for *plain*."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(_ALGORITHM, plain.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2${_ALGORITHM}${_ITERATIONS}${salt}${dk.hex()}"


def check_password(plain: str, stored: str) -> bool:
    """Return True if *plain* matches *stored* hash (constant-time compare)."""
    try:
        _, alg, iters_str, salt, hx = stored.split("$")
        dk = hashlib.pbkdf2_hmac(alg, plain.encode(), salt.encode(), int(iters_str))
        return hmac.compare_digest(dk.hex(), hx)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# API token (deterministic from password_hash + app secret_key)
# ---------------------------------------------------------------------------

def make_api_token(password_hash: str, secret_key: str) -> str:
    """Derive a stable Bearer token from the current password hash."""
    return hmac.new(
        secret_key.encode(), password_hash.encode(), "sha256"
    ).hexdigest()


def verify_api_token(token: str, password_hash: str, secret_key: str) -> bool:
    """Return True if *token* matches the expected Bearer token."""
    expected = make_api_token(password_hash, secret_key)
    return hmac.compare_digest(expected, token)
