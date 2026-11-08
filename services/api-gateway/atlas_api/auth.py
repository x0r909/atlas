"""Operator authentication for the API gateway.

Deliberately stdlib-first: scrypt for passwords, HMAC for the confirmation token, pyotp for
TOTP. Every stateful step lives behind ``SessionStore`` so the security properties
(single-use codes, idle expiry, revocation, lockout) are testable without Redis or HTTP.

Nothing here knows about FastAPI; routes call ``AuthService`` and translate errors.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

import pyotp

SESSION_COOKIE = "__Host-atlas_session"
SESSION_IDLE_SECONDS = 8 * 3600
TOTP_STEP_SECONDS = 30
LOCKOUT_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
CONFIRM_TTL_SECONDS = 60

# Mutations that need a fresh operator confirmation. Everything else is exempt, including
# the three actions that must never wait for a human gesture: halt, close, stop.
CONFIRM_REQUIRED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/mode"),
        ("PUT", "/risk/config"),
        ("POST", "/promotion/confirm"),
        ("POST", "/halt/lift"),
    }
)


class AuthError(Exception):
    """An authentication or authorisation failure with a stable code for the API."""

    def __init__(self, code: str, message: str, *, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


class SessionStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_s: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def add_once(self, key: str, ttl_s: int) -> bool: ...
    async def incr(self, key: str, ttl_s: int) -> int: ...
    async def counter(self, key: str) -> int: ...


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    username: str
    issued_at: int
    last_seen_at: int
    passkey_used: bool


@dataclass(frozen=True, slots=True)
class OperatorRecord:
    username: str
    password_hash: str
    totp_secret: str
    passkey_credentials: tuple[str, ...] = ()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """scrypt with an explicit cost, encoded so a stored hash is self-describing."""
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt_hex),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=len(bytes.fromhex(digest_hex)),
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


@functools.lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """Verified against when the username is unknown, so both paths cost one scrypt run."""
    return hash_password(secrets.token_urlsafe(24))


def _passkey_matches(credentials: tuple[str, ...], assertion: str) -> bool:
    """Constant-time membership. The full WebAuthn assertion check lands with the ceremony
    endpoints; until then a static secret is the weakest part and is kept out of any
    comparison whose timing could leak it."""
    presented = hashlib.sha256(assertion.encode()).digest()
    return any(
        hmac.compare_digest(hashlib.sha256(item.encode()).digest(), presented)
        for item in credentials
    )


def _user_key(username: str) -> str:
    """Usernames index Redis keys, so hash them: no separators, no unbounded fragments."""
    return hashlib.sha256(username.encode()).hexdigest()[:32]


def _key_bytes(key: str | bytes) -> bytes:
    return key.encode() if isinstance(key, str) else key


def issue_confirm_token(
    key: str | bytes,
    *,
    session_id: str,
    action: str,
    params_hash: str,
    now: int | None = None,
) -> str:
    """A 60 s, action-bound token: it confirms *this* mutation with *these* parameters."""
    expires_at = int(now if now is not None else time.time()) + CONFIRM_TTL_SECONDS
    message = f"{session_id}\n{action}\n{params_hash}\n{expires_at}".encode()
    signature = hmac.new(_key_bytes(key), message, hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def confirm_token_valid(
    key: str | bytes,
    token: str,
    *,
    session_id: str,
    action: str,
    params_hash: str,
    now: int | None = None,
) -> bool:
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except ValueError:
        return False
    if expires_at < int(now if now is not None else time.time()):
        return False
    message = f"{session_id}\n{action}\n{params_hash}\n{expires_at}".encode()
    expected = hmac.new(_key_bytes(key), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def requires_confirm(method: str, path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    for required_method, required_path in CONFIRM_REQUIRED:
        if required_method == method.upper() and normalized == required_path:
            return True
    return False


class AuthService:
    def __init__(
        self,
        store: SessionStore,
        *,
        confirm_key: str | bytes,
        operator_lookup: OperatorLookup,
        clock: Clock = time.time,
    ) -> None:
        self._store = store
        self._confirm_key: str | bytes = confirm_key
        self._lookup = operator_lookup
        self._clock = clock

    async def login(
        self,
        username: str,
        password: str,
        totp_code: str,
        *,
        passkey_assertion: str | None = None,
    ) -> Session:
        now = int(self._clock())
        await self._check_lockout(username)

        operator = await self._lookup(username)
        # Always pay for a hash and a TOTP computation, so an unknown username is not
        # measurably cheaper than a wrong password.
        password_ok = verify_password(
            password, operator.password_hash if operator is not None else _dummy_password_hash()
        )
        if operator is None or not password_ok:
            await self._record_failure(username)
            raise AuthError("AUTH_FAILED", "invalid credentials")
        # Only now may the code be spent: otherwise anyone could burn the operator's window.
        if not await self._consume_totp(operator, totp_code, now):
            await self._record_failure(username)
            raise AuthError("AUTH_FAILED", "invalid credentials")
        passkey_ok = False
        if passkey_assertion is not None:
            passkey_ok = _passkey_matches(operator.passkey_credentials, passkey_assertion) and (
                await self._store.add_once(
                    f"atlas:pk:{_user_key(username)}:{hashlib.sha256(passkey_assertion.encode()).hexdigest()[:16]}",
                    300,
                )
            )
            if not passkey_ok:
                await self._record_failure(username)
                raise AuthError("AUTH_FAILED", "invalid credentials")

        await self._store.delete(f"atlas:failcount:{_user_key(username)}")
        session_id = secrets.token_urlsafe(32)
        session = Session(
            session_id=session_id,
            username=username,
            issued_at=now,
            last_seen_at=now,
            passkey_used=passkey_ok,
        )
        await self._store.set(
            f"atlas:session:{session_id}", self._encode(session), SESSION_IDLE_SECONDS
        )
        return session

    async def authenticate(self, session_id: str | None) -> Session:
        if not session_id:
            raise AuthError("AUTH_REQUIRED", "session cookie missing")
        raw = await self._store.get(f"atlas:session:{session_id}")
        if raw is None:
            raise AuthError("AUTH_REQUIRED", "session expired or revoked")
        session = self._decode(session_id, raw)
        now = int(self._clock())
        if now - session.last_seen_at > SESSION_IDLE_SECONDS:
            await self.logout(session_id)
            raise AuthError("AUTH_REQUIRED", "session idle timeout")
        refreshed = Session(
            session_id=session.session_id,
            username=session.username,
            issued_at=session.issued_at,
            last_seen_at=now,
            passkey_used=session.passkey_used,
        )
        await self._store.set(
            f"atlas:session:{session_id}", self._encode(refreshed), SESSION_IDLE_SECONDS
        )
        return refreshed

    async def logout(self, session_id: str) -> None:
        await self._store.delete(f"atlas:session:{session_id}")

    async def issue_confirm(self, session: Session, action: str, params_hash: str) -> str:
        token = issue_confirm_token(
            self._confirm_key,
            session_id=session.session_id,
            action=action,
            params_hash=params_hash,
            now=int(self._clock()),
        )
        return token

    async def authorize_mutation(
        self,
        session: Session,
        method: str,
        path: str,
        params_hash: str,
        confirm_token: str | None,
    ) -> None:
        if not requires_confirm(method, path):
            return
        action = f"{method.upper()} {path.rstrip('/') or '/'}"
        if not confirm_token:
            raise AuthError("CONFIRM_REQUIRED", "X-Atlas-Confirm header is required")
        if not confirm_token_valid(
            self._confirm_key,
            confirm_token,
            session_id=session.session_id,
            action=action,
            params_hash=params_hash,
            now=int(self._clock()),
        ):
            raise AuthError("CONFIRM_INVALID", "confirmation token is expired or mismatched")
        single_use_key = f"atlas:confirm:{session.session_id}:{confirm_token}"
        if not await self._store.add_once(single_use_key, CONFIRM_TTL_SECONDS):
            raise AuthError("CONFIRM_REPLAYED", "confirmation token was already used")

    async def _consume_totp(self, operator: OperatorRecord, code: str, now: int) -> bool:
        """Burn the step the code actually belongs to, not the step the clock is in.

        Within valid_window a request straddling a 30 s boundary can present a code from
        the previous step; keying single-use on `now // 30` would then let the same code
        be accepted twice. Burning every step the code matches closes that.
        """
        totp = pyotp.TOTP(operator.totp_secret)
        counter = now // TOTP_STEP_SECONDS
        matched = [
            step
            for step in (counter - 1, counter, counter + 1)
            if hmac.compare_digest(totp.at(step * TOTP_STEP_SECONDS), code)
        ]
        if not matched:
            return False
        for step in matched:
            if not await self._store.add_once(
                f"atlas:totp:{_user_key(operator.username)}:{step}", TOTP_STEP_SECONDS * 3
            ):
                return False
        return True

    async def _check_lockout(self, username: str) -> None:
        attempts = await self._store.counter(f"atlas:failcount:{_user_key(username)}")
        if attempts >= LOCKOUT_FAILURES:
            raise AuthError(
                "AUTH_LOCKED",
                "too many failed attempts; try again later",
                retry_after_s=LOCKOUT_SECONDS,
            )

    async def _record_failure(self, username: str) -> None:
        # The increment is the authority, not a read followed by a write: otherwise
        # parallel attempts all pass the check before any of them counts.
        attempts = await self._store.incr(f"atlas:failcount:{_user_key(username)}", LOCKOUT_SECONDS)
        if attempts > LOCKOUT_FAILURES:
            raise AuthError(
                "AUTH_LOCKED",
                "too many failed attempts; try again later",
                retry_after_s=LOCKOUT_SECONDS,
            )

    @staticmethod
    def _encode(session: Session) -> str:
        return json.dumps(
            {
                "u": session.username,
                "iat": session.issued_at,
                "seen": session.last_seen_at,
                "pk": session.passkey_used,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _decode(session_id: str, raw: str) -> Session:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:  # a corrupt value must not become a session
            raise AuthError("AUTH_REQUIRED", "session record is unreadable") from exc
        try:
            return Session(
                session_id=session_id,
                username=str(data["u"]),
                issued_at=int(data["iat"]),
                last_seen_at=int(data["seen"]),
                passkey_used=bool(data["pk"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("AUTH_REQUIRED", "session record is unreadable") from exc


class OperatorLookup(Protocol):
    async def __call__(self, username: str) -> OperatorRecord | None: ...


class Clock(Protocol):
    def __call__(self) -> float: ...
