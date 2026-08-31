"""End-to-end coverage for the high-level ``Iec61850Client`` wrapper and the
tuning keyword arguments on ``IedConnection.connect``."""

from __future__ import annotations

import pytest

import _demo
import iec61850


def _config(address: str, **extra: object) -> iec61850.Iec61850ClientConfig:
    host, _, port = address.partition(":")
    return iec61850.Iec61850ClientConfig(host, int(port), **extra)  # type: ignore[arg-type]


async def test_iec61850_client_basic_read(demo_server: str) -> None:
    async with iec61850.Iec61850Client(_config(demo_server)) as cli:
        assert cli.connection.is_connected
        mode = await cli.connection.read_int32(_demo.MODE, iec61850.FC.ST)
        assert isinstance(mode, int)
    assert cli._connection is None


async def test_iec61850_client_dispatcher_starts_and_stops(demo_server: str) -> None:
    cfg = _config(demo_server, report_dispatcher_interval_ms=50)
    async with iec61850.Iec61850Client(cfg) as cli:
        # dispatcher must be running while the context is open
        assert cli._dispatcher is not None
    # leaving the context tears it down cleanly
    assert cli._dispatcher is None


async def test_iec61850_client_outside_context_raises() -> None:
    cfg = iec61850.Iec61850ClientConfig(address="127.0.0.1", port=1)
    cli = iec61850.Iec61850Client(cfg)
    with pytest.raises(RuntimeError, match="not entered"):
        _ = cli.connection


async def test_connect_with_tuning_kwargs(demo_server: str) -> None:
    """``request_timeout_ms`` / ``max_outstanding`` / ``local_max_pdu_size``
    are accepted by ``IedConnection.connect`` and the resulting connection
    behaves like a default one for normal reads."""
    conn = await iec61850.IedConnection.connect(
        demo_server,
        request_timeout_ms=2000,
        max_outstanding=4,
        local_max_pdu_size=8192,
    )
    try:
        mode = await conn.read_int32(_demo.MODE, iec61850.FC.ST)
        assert isinstance(mode, int)
    finally:
        await conn.disconnect()


async def test_client_with_tuning_in_config(demo_server: str) -> None:
    cfg = _config(
        demo_server,
        request_timeout_ms=3000,
        max_outstanding=2,
        local_max_pdu_size=16384,
    )
    async with iec61850.Iec61850Client(cfg) as cli:
        val = await cli.connection.read_float(_demo.POWER, iec61850.FC.MX)
        assert isinstance(val, float)
