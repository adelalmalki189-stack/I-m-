"""Password hashing, session tokens, and a small in-memory rate limiter (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

# scrypt parameters: one of OWASP's equivalent minimum configurations (N=2^14, r=8, p=5;
# 16 MiB per hash). Stored inside each hash so they can be raised later without
# breaking existing passwords.
_N, _R, _P = 2**14, 8, 5
_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def password_problem(password: str) -> str | None:
    """Arabic reason the password is too weak, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"كلمة المرور يجب ألا تقل عن {MIN_PASSWORD_LENGTH} أحرف"
    if password.isdigit() or password.isalpha():
        return "كلمة المرور يجب أن تجمع بين حروف وأرقام أو رموز"
    return None


def new_token() -> tuple[str, str]:
    """Return (token for the client, sha256 hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Sliding-window counter per key. In-memory: per process, reset on restart.

    Good enough for a single app instance; move to Redis/DB when running several.
    """

    def __init__(self, limit: int, window_seconds: float):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return hits

    def blocked(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()
