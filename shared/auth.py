"""Password hashing, session token generation and API key helpers."""

import hashlib
import secrets

from pwdlib import PasswordHash

SESSION_COOKIE_NAME = "session_id"
SESSION_TTL_DAYS = 30
API_KEY_PREFIX = "aia_"
API_KEY_DISPLAY_PREFIX_LEN = 8

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plaintext password using argon2id."""
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Return True when the plaintext password matches the stored hash."""
    valid, _ = password_hash.verify_and_update(password, hashed)
    return valid


def generate_session_token() -> str:
    """Generate a cryptographically secure opaque session token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for at-rest storage (never store the raw token)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new API key: a fixed prefix plus 256 bits of URL-safe randomness."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Hash an API key for at-rest storage (sha256 is enough for a 256-bit random secret)."""
    return hashlib.sha256(key.encode()).hexdigest()


def api_key_display_prefix(key: str) -> str:
    """Return the leading characters of a key that are safe to show in a list."""
    return key[:API_KEY_DISPLAY_PREFIX_LEN]
