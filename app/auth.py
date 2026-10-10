"""
Security primitives for CIRCULATE accounts. Standard library only.

Passwords: scrypt with a random 16-byte salt per user; compared with hmac.compare_digest.
Sessions: a cookie value "<user id>|<token version>|<expiry>|<view as>|<HMAC-SHA256>", signed with SECRET_KEY.
          Changing a password increments the token version, so every older cookie stops working.
Throttle: 5 failed logins for one email within 10 minutes lock that email for 5 minutes.
Nothing here logs or returns a password or a hash.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

MIN_PASSWORD = 8
SESSION_DAYS = 7
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> tuple[str, str]:
    """Returns (salt hex, hash hex) for a new random salt."""
    salt = secrets.token_bytes(16)
    return salt.hex(), hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT).hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, bytes.fromhex(hash_hex))


def burn_time(password: str) -> None:
    """Spend the same work as a real check, so an unknown email takes as long as a wrong password."""
    hashlib.scrypt(password.encode("utf-8"), salt=b"\0" * 16, **_SCRYPT)


_key_cache: dict[str, bytes] = {}


def secret_key(root: Path) -> bytes:
    """SECRET_KEY from the environment; otherwise a random key kept in <root>/.secret_key (git-ignored)."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode("utf-8")
    path = root / ".secret_key"
    if str(path) not in _key_cache:
        if not path.exists():
            path.write_text(secrets.token_hex(32), encoding="utf-8")
        _key_cache[str(path)] = path.read_text(encoding="utf-8").strip().encode("utf-8")
    return _key_cache[str(path)]


def make_token(key: bytes, user_id: int, token_version: int, view: str = "", now: float | None = None,
               days: float = SESSION_DAYS) -> str:
    exp = int((now if now is not None else time.time()) + days * 86400)
    body = f"{int(user_id)}|{int(token_version)}|{exp}|{view}"
    return body + "|" + hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def read_token(key: bytes, token: str | None, now: float | None = None) -> dict | None:
    """{user_id, token_version, view} for a valid, unexpired token; None for anything else."""
    if not token or token.count("|") != 4:
        return None
    body, sig = token.rsplit("|", 1)
    if not hmac.compare_digest(hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest(), sig):
        return None
    uid, tv, exp, view = body.split("|")
    try:
        if int(exp) < (now if now is not None else time.time()):
            return None
        return {"user_id": int(uid), "token_version": int(tv), "view": view}
    except ValueError:
        return None


class Throttle:
    """Failed logins per email, kept in memory. 5 failures in 10 minutes lock the email for 5 minutes."""

    def __init__(self, limit: int = 5, window: float = 600, lock: float = 300):
        self.limit, self.window, self.lock = limit, window, lock
        self.fails: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}

    def wait(self, email: str, now: float | None = None) -> float:
        """Seconds until this email may try again (0 = may try now)."""
        now = now if now is not None else time.time()
        return max(0.0, self.locked.get(email, 0) - now)

    def fail(self, email: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        recent = [t for t in self.fails.get(email, []) if now - t < self.window] + [now]
        self.fails[email] = recent
        if len(recent) >= self.limit:
            self.locked[email] = now + self.lock
            self.fails[email] = []

    def success(self, email: str) -> None:
        self.fails.pop(email, None)
        self.locked.pop(email, None)

    def reset(self) -> None:
        self.fails.clear()
        self.locked.clear()
