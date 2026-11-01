"""ACSE authentication: a client password and a server authenticator.

Every case opens a real association between this package's client and a
server it hosts; the refusal travels as a Session REFUSE whose AARE carries
the acse-service-user diagnostic.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import _demo
import iec61850
from iec61850 import IedAuthenticationError, IedConnection, IsoConnectionParameters

PASSWORD = "s3cret"
CLIENT_AP_TITLE = "1.1.1.999"


def _password_gate(seen: list[dict] | None = None):
    def authenticate(request: dict):
        if seen is not None:
            seen.append(request)
        return request["password"] == PASSWORD.encode()

    return authenticate


async def _connect(addr: str, **iso) -> IedConnection:
    return await IedConnection.connect(addr, iso=IsoConnectionParameters(**iso))


async def test_correct_password_is_accepted_and_reaches_the_authenticator() -> None:
    seen: list[dict] = []
    async with _demo.hosted_demo(
        configure=lambda s: s.set_authenticator(_password_gate(seen))
    ) as server:
        conn = await _connect(server.bound_addr, password=PASSWORD)
        try:
            assert await conn.read_bool(_demo.IND1, iec61850.FC.ST) is True
        finally:
            await conn.disconnect()
    assert len(seen) == 1
    request = seen[0]
    assert request["mechanism"] == "password"
    assert request["password"] == PASSWORD.encode()
    assert request["calling_ap_title"] == CLIENT_AP_TITLE
    assert request["calling_ae_qualifier"] == 12
    assert request["called_ap_title"] == "1.1.1.999.1"
    host, _, port = request["peer"].rpartition(":")
    assert host == "127.0.0.1" and int(port) > 0


@pytest.mark.parametrize("password", ["wrong", None], ids=["wrong", "missing"])
async def test_refused_password_raises_authentication_error(password) -> None:
    async with _demo.hosted_demo(
        configure=lambda s: s.set_authenticator(_password_gate())
    ) as server:
        with pytest.raises(IedAuthenticationError) as info:
            await _connect(server.bound_addr, password=password)
        assert server.connection_count == 0
    err = info.value
    assert isinstance(err, iec61850.IedConnectionError)
    assert err.diagnostic == 13
    assert err.diagnostic_name == "authentication-failure"
    assert err.reason == "authentication-rejected"


async def test_a_password_given_as_bytes_is_sent_unchanged() -> None:
    async with _demo.hosted_demo(
        configure=lambda s: s.set_authenticator(_password_gate())
    ) as server:
        conn = await _connect(server.bound_addr, password=PASSWORD.encode())
        await conn.disconnect()


async def test_a_named_diagnostic_refuses_with_that_diagnostic() -> None:
    """An AP-title allow list refuses an unknown calling AP-title with
    calling-AP-title-not-recognized."""

    def allow_list(request: dict):
        if request["calling_ap_title"] != "1.3.9999.1":
            return "calling-AP-title-not-recognized"
        return None

    async with _demo.hosted_demo(configure=lambda s: s.set_authenticator(allow_list)) as server:
        with pytest.raises(IedAuthenticationError) as info:
            await _connect(server.bound_addr)
        assert info.value.diagnostic == 3
        assert info.value.diagnostic_name == "calling-AP-title-not-recognized"
        conn = await _connect(server.bound_addr, local_ap_title="1.3.9999.1")
        await conn.disconnect()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize(
    "decision",
    [lambda _r: [], lambda _r: "no-such-diagnostic", lambda _r: 1 / 0],
    ids=["other-type", "unknown-name", "raises"],
)
async def test_any_other_answer_refuses_with_authentication_failure(decision) -> None:
    async with _demo.hosted_demo(configure=lambda s: s.set_authenticator(decision)) as server:
        with pytest.raises(IedAuthenticationError) as info:
            await _connect(server.bound_addr)
    assert info.value.diagnostic == 13


async def test_without_an_authenticator_every_association_is_accepted() -> None:
    async with _demo.hosted_demo() as server:
        conn = await _connect(server.bound_addr, password="anything")
        await conn.disconnect()


async def test_an_authenticator_installed_while_running_applies_to_new_associations() -> None:
    async with _demo.hosted_demo() as server:
        before = await _connect(server.bound_addr)
        server.set_authenticator(_password_gate())
        try:
            with pytest.raises(IedAuthenticationError):
                await _connect(server.bound_addr)
            assert await before.read_bool(_demo.IND1, iec61850.FC.ST) is True
            ok = await _connect(server.bound_addr, password=PASSWORD)
            await ok.disconnect()
            server.set_authenticator(None)
            open_again = await _connect(server.bound_addr)
            await open_again.disconnect()
        finally:
            await before.disconnect()


async def test_a_blocked_authenticator_does_not_hold_up_other_associations() -> None:
    """While the authenticator waits on one request, another association is
    decided and served; the waiting one completes once released."""
    release = threading.Event()
    entered = threading.Event()

    def authenticate(request: dict):
        if request["password"] == b"slow":
            entered.set()
            release.wait(10)
        return True

    async with _demo.hosted_demo(configure=lambda s: s.set_authenticator(authenticate)) as server:
        slow = asyncio.ensure_future(_connect(server.bound_addr, password="slow"))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            fast = await _connect(server.bound_addr, password="fast")
            try:
                assert await fast.read_bool(_demo.IND1, iec61850.FC.ST) is True
            finally:
                await fast.disconnect()
            assert not slow.done()
        finally:
            release.set()
        conn = await slow
        await conn.disconnect()


def test_set_authenticator_refuses_a_non_callable() -> None:
    server = iec61850.IedServer.from_scl(_demo.DEMO_CID, ied_name=_demo.IED_NAME)
    with pytest.raises(ValueError, match="callable"):
        server.set_authenticator(42)


def test_password_stays_out_of_the_repr() -> None:
    params = IsoConnectionParameters(password=PASSWORD)
    assert PASSWORD not in repr(params)
    assert params.to_native()["password"] == PASSWORD
    with pytest.raises(ValueError, match="password"):
        IsoConnectionParameters(password=1234)  # type: ignore[arg-type]


async def test_a_diagnostic_code_refuses_with_that_diagnostic() -> None:
    async with _demo.hosted_demo(
        configure=lambda s: s.set_authenticator(lambda _r: 14)
    ) as server:
        with pytest.raises(IedAuthenticationError) as info:
            await _connect(server.bound_addr)
    assert info.value.diagnostic == 14
    assert info.value.diagnostic_name == "authentication-required"
