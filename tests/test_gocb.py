"""Tests for the GOOSE control block client surface.

A server hosted by this package registers no GOOSE control block, so the
end-to-end cases cover the refusals of a server that holds none; the decoding
of a populated GoCB is covered from the native mapping.
"""

from __future__ import annotations

import pytest

import _demo
import iec61850

GOCB_IEC = f"{_demo.DOMAIN}/LLN0.gcbStatus"
GOCB_MMS = f"{_demo.DOMAIN}/LLN0$GO$gcbStatus"


@pytest.mark.parametrize("reference", [GOCB_IEC, GOCB_MMS])
async def test_get_gocb_values_of_unregistered_block_raises(
    demo_server: str, reference: str
) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.get_gocb_values(reference)
    finally:
        await conn.disconnect()


async def test_get_gocb_values_rejects_malformed_reference(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.get_gocb_values("no-slash")
    finally:
        await conn.disconnect()


async def test_set_gocb_values_on_unregistered_block_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.set_gocb_values(GOCB_IEC, go_ena=True)
    finally:
        await conn.disconnect()


async def test_set_gocb_values_with_nothing_sends_nothing(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        await conn.set_gocb_values(GOCB_IEC)
        assert conn.is_connected
    finally:
        await conn.disconnect()


async def test_gocb_after_disconnect_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    await conn.disconnect()
    with pytest.raises(iec61850.IedConnectionError):
        await conn.get_gocb_values(GOCB_IEC)


def test_gocb_values_from_native() -> None:
    values = iec61850.GoCBValues.from_native(
        {
            "go_ena": True,
            "go_id": "DemoGoose",
            "dat_set": f"{_demo.DOMAIN}/LLN0$dsStatus",
            "conf_rev": 3,
            "nds_com": False,
            "dst_address": {
                "addr": b"\x01\x0c\xcd\x01\x00\x01",
                "priority": 4,
                "vlan_id": 0x123,
                "app_id": 0x3001,
            },
            "min_time_ms": 4,
            "max_time_ms": 1000,
            "fixed_offs": None,
        }
    )
    assert values.go_ena is True
    assert values.go_id == "DemoGoose"
    assert values.conf_rev == 3
    assert values.dst_address == iec61850.PhyComAddress(
        addr=b"\x01\x0c\xcd\x01\x00\x01", priority=4, vlan_id=0x123, app_id=0x3001
    )
    assert values.min_time_ms == 4
    assert values.max_time_ms == 1000
    assert values.fixed_offs is None
