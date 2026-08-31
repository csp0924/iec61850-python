"""Connection-level tests: address validation, refusal, timeout, and abort.

The negative cases exercise the whole PyO3 -> pyo3-async-runtimes ->
iec61850-client -> tokio stack without needing a server; the abort case runs
against the demonstration IED this package hosts.
"""

from __future__ import annotations

import socket

import pytest

import iec61850


def test_address_missing_port() -> None:
    coro = iec61850.IedConnection.connect("127.0.0.1")
    with pytest.raises(ValueError, match="must be 'host:port'"):
        coro.send(None)  # drive the coroutine far enough to surface the sync error


def test_address_empty_host() -> None:
    coro = iec61850.IedConnection.connect(":102")
    with pytest.raises(ValueError, match="host portion .* is empty"):
        coro.send(None)


def test_address_bad_port() -> None:
    coro = iec61850.IedConnection.connect("127.0.0.1:notanumber")
    with pytest.raises(ValueError, match="not a valid u16"):
        coro.send(None)


async def test_connect_refused() -> None:
    """A closed TCP port surfaces as IedConnectionError.

    Some stacks swallow the RST and let the deadline expire instead, so
    IedTimeoutError is equally valid. The port is one just released back to
    the ephemeral range, where nothing is listening.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    with pytest.raises((iec61850.IedConnectionError, iec61850.IedTimeoutError)):
        await iec61850.IedConnection.connect(
            f"127.0.0.1:{closed_port}", timeout_ms=2000
        )


async def test_connect_timeout_non_routable() -> None:
    """RFC 5737 TEST-NET-1 (192.0.2.0/24) with a short timeout.

    Network stacks classify the failure differently, so either
    IedTimeoutError (the deadline elapsed) or IedConnectionError (the kernel
    short-circuited) is accepted. What matters is that the call returns an
    iec61850 error rather than hanging.
    """
    with pytest.raises((iec61850.IedTimeoutError, iec61850.IedConnectionError)):
        await iec61850.IedConnection.connect("192.0.2.1:102", timeout_ms=300)


async def test_abort_drops_connection(demo_server: str) -> None:
    """``abort`` clears the connected state without exchanging an MMS Conclude."""
    conn = await iec61850.IedConnection.connect(demo_server)
    assert conn.is_connected
    await conn.abort()
    assert conn.is_connected is False
