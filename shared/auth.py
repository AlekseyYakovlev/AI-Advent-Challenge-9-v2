"""Password hashing and session token generation."""

import hashlib
import secrets

from pwdlib import PasswordHash

SESSION_COOKIE_NAME = "session_id"
SESSION_TTL_DAYS = 30

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
