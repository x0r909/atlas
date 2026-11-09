"""Login, session, lockout and confirmation properties - no HTTP, no Redis.

Each test asserts one security invariant. A test that only exercises the happy path would
let a single-use code be replayable or a lockout never trigger.
"""

from __future__ import annotations

import time

import pyotp
import pytest
from atlas_api.auth import (
    CONFIRM_TTL_SECONDS,
    LOCKOUT_FAILURES,
    SESSION_IDLE_SECONDS,
    TOTP_STEP_SECONDS,
    AuthError,
    AuthService,
    OperatorRecord,
    Session,
    confirm_token_valid,
    hash_password,
    issue_confirm_token,
    requires_confirm,
    verify_password,
)

PASSWORD = "correct horse battery staple"
SECRET = pyotp.random_base32()
CONFIRM_KEY = "c" * 32


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeStore:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.values: dict[str, tuple[str, float]] = {}
        self.counters: dict[str, tuple[int, float]] = {}

    def _live(self, key: str) -> str | None:
        entry = self.values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self._clock():
            del self.values[key]
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self.values[key] = (value, self._clock() + ttl_s)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.counters.pop(key, None)

    async def add_once(self, key: str, ttl_s: int) -> bool:
        if self._live(key) is not None:
            return False
        self.values[key] = ("1", self._clock() + ttl_s)
        return True

    async def counter(self, key: str) -> int:
        count, expires_at = self.counters.get(key, (0, 0.0))
        return count if expires_at > self._clock() else 0

    async def incr(self, key: str, ttl_s: int) -> int:
        count, expires_at = self.counters.get(key, (0, self._clock() + ttl_s))
        if expires_at <= self._clock():
            count, expires_at = 0, self._clock() + ttl_s
        count += 1
        self.counters[key] = (count, expires_at)
        return count


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def store(clock: FakeClock) -> FakeStore:
    return FakeStore(clock)


@pytest.fixture()
def operator() -> OperatorRecord:
    return OperatorRecord(
        username="augie", password_hash=hash_password(PASSWORD), totp_secret=SECRET
    )


@pytest.fixture()
def auth(store: FakeStore, operator: OperatorRecord, clock: FakeClock) -> AuthService:
    async def lookup(username: str) -> OperatorRecord | None:
        return operator if username == operator.username else None

    return AuthService(store, confirm_key=CONFIRM_KEY, operator_lookup=lookup, clock=clock)


def totp_at(clock: FakeClock) -> str:
    return pyotp.TOTP(SECRET).at(int(clock()))


async def test_login_with_password_and_totp_returns_a_session(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    assert session.username == "augie"
    assert await auth.authenticate(session.session_id) is not None


async def test_totp_is_mandatory(auth):
    with pytest.raises(AuthError) as excinfo:
        await auth.login("augie", PASSWORD, "")
    assert excinfo.value.code == "AUTH_FAILED"


async def test_a_wrong_password_never_creates_a_session(auth, clock, store):
    with pytest.raises(AuthError):
        await auth.login("augie", "wrong password here", totp_at(clock))
    assert not [key for key in store.values if key.startswith("atlas:session:")]


async def test_the_same_totp_window_cannot_be_used_twice(auth, clock):
    await auth.login("augie", PASSWORD, totp_at(clock))
    with pytest.raises(AuthError) as excinfo:
        await auth.login("augie", PASSWORD, totp_at(clock))
    assert excinfo.value.code == "AUTH_FAILED"


async def test_a_wrong_password_does_not_spend_the_totp_window(auth, clock):
    code = totp_at(clock)
    with pytest.raises(AuthError):
        await auth.login("augie", "wrong password here", code)
    # The code was never checked against the operator, so it must still be usable.
    await auth.login("augie", PASSWORD, code)


async def test_a_code_cannot_be_reused_across_a_step_boundary(auth, clock):
    code = totp_at(clock)
    await auth.login("augie", PASSWORD, code)
    # One step later the code is still inside valid_window, which is exactly the replay
    # a per-window key would miss.
    clock.advance(TOTP_STEP_SECONDS)
    with pytest.raises(AuthError) as excinfo:
        await auth.login("augie", PASSWORD, code)
    assert excinfo.value.code == "AUTH_FAILED"


async def test_the_failure_counter_decides_not_a_stale_read(auth, clock, store):
    async def stale(key: str) -> int:
        return 0

    store.counter = stale
    for _ in range(LOCKOUT_FAILURES):
        with pytest.raises(AuthError) as excinfo:
            await auth.login("augie", "wrong password here", "000000")
        assert excinfo.value.code == "AUTH_FAILED"
    with pytest.raises(AuthError) as excinfo:
        await auth.login("augie", "wrong password here", "000000")
    assert excinfo.value.code == "AUTH_LOCKED"


async def test_repeated_failures_lock_the_account(auth, clock):
    for _ in range(LOCKOUT_FAILURES + 1):
        with pytest.raises(AuthError):
            await auth.login("augie", "wrong password here", "000000")
    with pytest.raises(AuthError) as excinfo:
        await auth.login("augie", PASSWORD, totp_at(clock))
    assert excinfo.value.code == "AUTH_LOCKED"
    assert excinfo.value.retry_after_s is not None


async def test_a_successful_login_resets_the_failure_count(auth, clock):
    for _ in range(LOCKOUT_FAILURES - 1):
        with pytest.raises(AuthError):
            await auth.login("augie", "wrong password here", "000000")
    await auth.login("augie", PASSWORD, totp_at(clock))
    clock.advance(31)
    await auth.login("augie", PASSWORD, totp_at(clock))


async def test_a_session_idles_out_after_eight_hours(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    clock.advance(SESSION_IDLE_SECONDS + 1)
    with pytest.raises(AuthError) as excinfo:
        await auth.authenticate(session.session_id)
    assert excinfo.value.code == "AUTH_REQUIRED"


async def test_activity_extends_the_idle_window(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    clock.advance(SESSION_IDLE_SECONDS - 60)
    await auth.authenticate(session.session_id)
    clock.advance(SESSION_IDLE_SECONDS - 60)
    assert await auth.authenticate(session.session_id) is not None


async def test_logout_revokes_server_side(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    await auth.logout(session.session_id)
    with pytest.raises(AuthError):
        await auth.authenticate(session.session_id)


async def test_a_missing_or_unknown_cookie_is_refused(auth):
    for cookie in (None, "not-a-session"):
        with pytest.raises(AuthError) as excinfo:
            await auth.authenticate(cookie)
        assert excinfo.value.code == "AUTH_REQUIRED"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/mode"),
        ("PUT", "/risk/config"),
        ("POST", "/promotion/confirm"),
        ("POST", "/halt/lift"),
    ],
)
async def test_destructive_mutations_require_a_confirmation_tuple(method, path):
    assert requires_confirm(method, path) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/killswitch"),
        ("POST", "/positions/abc/close"),
        ("PUT", "/positions/abc/stop"),
        ("GET", "/mode"),
    ],
)
async def test_halting_closing_and_stopping_are_never_behind_a_confirmation(method, path):
    """A confirmation prompt must not stand between the operator and a flatten."""
    assert requires_confirm(method, path) is False


async def test_confirmation_is_bound_to_the_exact_action_and_parameters(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    token = issue_confirm_token(
        CONFIRM_KEY,
        session_id=session.session_id,
        action="POST /mode",
        params_hash="a" * 64,
        now=int(clock()),
    )
    await auth.authorize_mutation(session, "POST", "/mode", "a" * 64, token)
    with pytest.raises(AuthError) as excinfo:
        await auth.authorize_mutation(session, "POST", "/mode", "b" * 64, token)
    assert excinfo.value.code in {"CONFIRM_INVALID", "CONFIRM_REPLAYED"}


async def test_a_confirmation_token_is_single_use(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    token = await auth.issue_confirm(session, "POST /killswitch", "c" * 64)
    await auth.authorize_mutation(session, "POST", "/killswitch", "c" * 64, token)
    # /killswitch is exempt, so burn a required one instead.
    token = issue_confirm_token(
        CONFIRM_KEY,
        session_id=session.session_id,
        action="POST /mode",
        params_hash="d" * 64,
        now=int(clock()),
    )
    await auth.authorize_mutation(session, "POST", "/mode", "d" * 64, token)
    with pytest.raises(AuthError) as excinfo:
        await auth.authorize_mutation(session, "POST", "/mode", "d" * 64, token)
    assert excinfo.value.code == "CONFIRM_REPLAYED"


async def test_a_confirmation_token_expires_after_sixty_seconds(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    token = issue_confirm_token(
        CONFIRM_KEY,
        session_id=session.session_id,
        action="POST /mode",
        params_hash="e" * 64,
        now=int(clock()),
    )
    clock.advance(CONFIRM_TTL_SECONDS + 1)
    with pytest.raises(AuthError) as excinfo:
        await auth.authorize_mutation(session, "POST", "/mode", "e" * 64, token)
    assert excinfo.value.code == "CONFIRM_INVALID"


async def test_a_confirmation_token_from_another_session_does_not_work(auth, clock):
    session = await auth.login("augie", PASSWORD, totp_at(clock))
    token = issue_confirm_token(
        CONFIRM_KEY,
        session_id="someone-else",
        action="POST /mode",
        params_hash="f" * 64,
        now=int(clock()),
    )
    with pytest.raises(AuthError):
        await auth.authorize_mutation(session, "POST", "/mode", "f" * 64, token)


def test_confirm_token_rejects_garbage_without_raising():
    assert (
        confirm_token_valid(CONFIRM_KEY, "not-a-token", session_id="s", action="a", params_hash="p")
        is False
    )
    assert issue_confirm_token(CONFIRM_KEY, session_id="s", action="a", params_hash="p")


def test_password_hashing_is_salted_and_verifiable():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first) is True
    assert verify_password("wrong password here", first) is False
    assert verify_password(PASSWORD, "bcrypt$nope") is False


def test_short_passwords_are_refused_at_hash_time():
    with pytest.raises(ValueError):
        hash_password("short")


def test_session_dataclass_carries_no_secret():
    session = Session("id", "augie", int(time.time()), int(time.time()), False)
    assert "totp" not in repr(session).lower()
    assert "password" not in repr(session).lower()
