import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("dummy-password-used-to-equalise-timing")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_verify(password: str) -> None:
    """Spend the same time as a real check when the user does not exist."""
    verify_password(_DUMMY_HASH, password)


def validate_password_policy(password: str) -> None:
    if len(password) < 10 or not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password) \
            or not re.search(r"\d", password):
        raise ValueError("Password needs at least 10 characters with upper case, lower case and a digit.")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


class SecretBox:
    """Encrypts small secrets (school database passwords) with a key from the environment."""

    def __init__(self, key: str):
        self._f = Fernet(key.encode())

    def encrypt(self, plain: str) -> str:
        return self._f.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()
