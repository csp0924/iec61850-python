"""Tests for the GOOSE control block client surface.

The session `demo_server` registers no GOOSE control block, so the cases that
use it cover the refusals of a server that holds none. The success path runs
against a server that registers the model's `gcbStatus` with
`register_from_model()`.
"""

from __future__ import annotations

import pytest

import _demo
import iec61850
from iec61850 import IedConnection, IedServer

GOCB_IEC = f"{_demo.DOMAIN}/LLN0.gcbStatus"
GOCB_MMS = f"{_demo.DOMAIN}/LLN0$GO$gcbStatus"
DS_STATUS_REF = f"{_demo.DOMAIN}/{_demo.DS_STATUS}"
DS_MEAS_REF = f"{_demo.DOMAIN}/{_demo.DS_MEAS}"



def _gocb_server() -> IedServer:
    """The demonstration IED on a free loopback port with every control block
    of its model registered."""
    server = IedServer.from_scl(_demo.DEMO_CID, ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    server.register_from_model()
    return server


@pytest.mark.parametrize("reference", [GOCB_IEC, GOCB_MMS])
async def test_get_gocb_values_reads_the_registered_block(reference: str) -> None:
    async with _gocb_server() as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            values = await conn.get_gocb_values(reference)
        finally:
            await conn.disconnect()
    assert values.go_ena is False
    assert values.go_id == "DemoIED/LLN0$GO$gcbStatus"
    assert values.dat_set == f"{_demo.DOMAIN}/{_demo.DS_STATUS}"
    assert values.conf_rev == 1
    assert values.nds_com is False
    assert values.fixed_offs is False
    assert values.min_time_ms is not None
    assert values.max_time_ms == 1000
    assert 0 < values.min_time_ms <= values.max_time_ms


async def test_set_gocb_values_enables_the_block_and_reads_back() -> None:
    async with _gocb_server() as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.set_gocb_values(GOCB_IEC, go_id="renamed")
            await conn.set_gocb_values(GOCB_IEC, go_ena=True)
            enabled = await conn.get_gocb_values(GOCB_IEC)
            await conn.set_gocb_values(GOCB_IEC, go_ena=False)
            disabled = await conn.get_gocb_values(GOCB_MMS)
        finally:
            await conn.disconnect()
    assert enabled.go_ena is True
    assert enabled.go_id == "renamed"
    assert enabled.dat_set == f"{_demo.DOMAIN}/{_demo.DS_STATUS}"
    assert disabled.go_ena is False
    assert disabled.go_id == "renamed"


async def test_set_gocb_values_refuses_go_id_while_enabled() -> None:
    async with _gocb_server() as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.set_gocb_values(GOCB_IEC, go_ena=True)
            with pytest.raises(iec61850.IedDataAccessError) as excinfo:
                await conn.set_gocb_values(GOCB_IEC, go_id="renamed")
            values = await conn.get_gocb_values(GOCB_IEC)
        finally:
            await conn.disconnect()
    assert excinfo.value.code == 2
    assert values.go_ena is True
    assert values.go_id == "DemoIED/LLN0$GO$gcbStatus"

async def test_set_gocb_values_changes_the_data_set_while_disabled() -> None:
    async with _gocb_server() as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.set_gocb_values(GOCB_IEC, dat_set=DS_MEAS_REF)
            values = await conn.get_gocb_values(GOCB_IEC)
        finally:
            await conn.disconnect()
    assert values.dat_set == DS_MEAS_REF
    assert values.go_ena is False


async def test_set_gocb_values_refuses_dat_set_while_enabled() -> None:
    async with _gocb_server() as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.set_gocb_values(GOCB_IEC, go_ena=True)
            with pytest.raises(iec61850.IedDataAccessError) as excinfo:
                await conn.set_gocb_values(GOCB_IEC, dat_set=DS_MEAS_REF)
            values = await conn.get_gocb_values(GOCB_IEC)
        finally:
            await conn.disconnect()
    assert excinfo.value.code == 2
    assert values.dat_set == DS_STATUS_REF


async def test_set_gocb_values_refuses_dat_set_under_ed2_1() -> None:
    server = IedServer.from_scl(_demo.DEMO_CID, ied_name=_demo.IED_NAME)
    server.set_edition("ed2.1")
    server.bind("127.0.0.1:0")
    server.register_from_model()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            with pytest.raises(iec61850.IedDataAccessError) as excinfo:
                await conn.set_gocb_values(GOCB_IEC, dat_set=DS_MEAS_REF)
            values = await conn.get_gocb_values(GOCB_IEC)
        finally:
            await conn.disconnect()
    assert excinfo.value.code is not None
    assert values.dat_set == DS_STATUS_REF


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
