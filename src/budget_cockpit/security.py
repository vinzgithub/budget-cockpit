"""Password hashing, API tokens, CSRF and login throttling (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from threading import Lock

_SCRYPT = dict(n=2**14, r=8, p=1)
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32,
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session: dict) -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def csrf_valid(session: dict, provided: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


class LoginThrottle:
    """After ``max_failures`` failed logins from one address, refuse further
    attempts for ``lock_seconds``. In memory: a restart clears it."""

    def __init__(self, max_failures: int = 5, lock_seconds: int = 300):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._state: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def locked(self, key: str) -> bool:
        with self._lock:
            failures, until = self._state.get(key, (0, 0.0))
            return failures >= self.max_failures and time.monotonic() < until

    def failure(self, key: str) -> None:
        with self._lock:
            failures, until = self._state.get(key, (0, 0.0))
            if failures >= self.max_failures and time.monotonic() >= until:
                failures = 0
            failures += 1
            self._state[key] = (failures, time.monotonic() + self.lock_seconds)

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)
