"""Encrypt secrets (SMTP password, alert webhook URLs) before they are written to the database."""
import base64
import hashlib
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from .database import DATABASE_URL

log = logging.getLogger(__name__)

PREFIX = "enc:"
_fernet: Fernet | None = None


def _key_file() -> Path:
    if os.getenv("SECRET_KEY_FILE"):
        return Path(os.environ["SECRET_KEY_FILE"])
    if DATABASE_URL.startswith("sqlite:///"):
        return Path(DATABASE_URL[len("sqlite:///"):]).resolve().parent / "secret.key"
    return Path("secret.key").resolve()


def _load_key() -> bytes:
    secret = os.getenv("SECRET_KEY")
    if secret:
        return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    path = _key_file()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
        log.warning("Created encryption key %s - back it up, or set SECRET_KEY instead", path)
    return path.read_bytes().strip()


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(value: str | None) -> str | None:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _cipher().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    # Values saved before encryption existed have no prefix and are returned as they are
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _cipher().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        log.error("Cannot decrypt a stored secret - was SECRET_KEY or secret.key changed? Enter it again in Settings.")
        return ""


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)
