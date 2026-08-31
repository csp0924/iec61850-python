"""End-to-end tests for ``get_variable_specification`` and ``get_device_model``."""

from __future__ import annotations

import pytest

import _demo
import iec61850


async def test_variable_spec_scalar_int(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        ts = await conn.get_variable_specification(
            f"{_demo.DOMAIN}/LLN0.Mod", iec61850.FC.ST
        )
    finally:
        await conn.disconnect()
    # Mod is an ENS: a structure carrying stVal (enumerated), q, and t.
    assert ts["kind"] == "structure"
    by_name = {c["name"]: c["type"] for c in ts["components"]}
    assert "stVal" in by_name
    st = by_name["stVal"]
    assert st["kind"] in {"integer", "unsigned"}
    assert "width_bits" in st


async def test_variable_spec_constructed_da(demo_server: str) -> None:
    """`.mag` of an MV is a structure carrying `.f`, the analogue value."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        ts = await conn.get_variable_specification(
            f"{_demo.DOMAIN}/MMXU1.TotW.mag", iec61850.FC.MX
        )
    finally:
        await conn.disconnect()
    assert ts["kind"] == "structure"
    by_name = {c["name"]: c["type"] for c in ts["components"]}
    assert "f" in by_name
    assert by_name["f"]["kind"] == "float"
    assert by_name["f"]["format_width"] in {32, 64}


async def test_variable_spec_rejects_array_index(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.get_variable_specification(
                f"{_demo.DOMAIN}/GGIO1.Ind1(0).stVal", iec61850.FC.ST
            )
    finally:
        await conn.disconnect()


async def test_device_model_lists_logical_devices(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        model = await conn.get_device_model()
    finally:
        await conn.disconnect()
    assert "logical_devices" in model
    names = {ld["name"] for ld in model["logical_devices"]}
    assert _demo.DOMAIN in names
    ld = next(ld for ld in model["logical_devices"] if ld["name"] == _demo.DOMAIN)
    # MMS NamedVariable names use $ as the path separator.
    assert any("GGIO1" in v for v in ld["variables"])


async def test_device_model_refresh_returns_same_shape(demo_server: str) -> None:
    """``refresh=True`` round-trips to the server again and must return the
    same shape as a cached call."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        cached = await conn.get_device_model()
        forced = await conn.get_device_model(refresh=True)
    finally:
        await conn.disconnect()
    assert cached["logical_devices"] == forced["logical_devices"]


async def test_variable_spec_via_high_level_client(demo_server: str) -> None:
    host, _, port = demo_server.partition(":")
    cfg = iec61850.Iec61850ClientConfig(address=host, port=int(port))
    async with iec61850.Iec61850Client(cfg) as cli:
        ts = await cli.connection.get_variable_specification(
            _demo.VENDOR, iec61850.FC.DC
        )
    assert ts["kind"] == "visible_string"
    assert "max_chars" in ts
