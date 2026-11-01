"""End-to-end tests for the MMS Identify service against a hosted server."""

from __future__ import annotations

import pytest

import _demo
import iec61850


async def test_identity_reads_back_configured_strings() -> None:
    server = iec61850.IedServer.from_scl(_demo.DEMO_CID, ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    server.vendor = "ACME Grid"
    server.model_name = "Bay Controller 7"
    server.revision = "3.1.4"
    async with server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            identity = await conn.get_server_identity()
        finally:
            await conn.disconnect()
    assert isinstance(identity, iec61850.ServerIdentity)
    assert identity.vendor == "ACME Grid"
    assert identity.model == "Bay Controller 7"
    assert identity.revision == "3.1.4"
    assert identity.abstract_syntaxes == ()


async def test_identity_of_unconfigured_server_differs(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        identity = await conn.get_server_identity()
    finally:
        await conn.disconnect()
    assert identity.vendor != "ACME Grid"
    assert identity.vendor
    assert identity.model
    assert identity.revision


async def test_identity_after_disconnect_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    await conn.disconnect()
    with pytest.raises(iec61850.IedConnectionError):
        await conn.get_server_identity()


def test_identity_from_native_keeps_abstract_syntaxes() -> None:
    identity = iec61850.ServerIdentity.from_native(
        {
            "vendor": "v",
            "model": "m",
            "revision": "r",
            "abstract_syntaxes": ["1.0.9506.2.1", "1.0.9506.2.3"],
        }
    )
    assert identity.abstract_syntaxes == ("1.0.9506.2.1", "1.0.9506.2.3")


@pytest.mark.parametrize("extended_derivation", [False, True])
async def test_status_reports_operational_server(
    demo_server: str, extended_derivation: bool
) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        status = await conn.get_server_status(extended_derivation)
    finally:
        await conn.disconnect()
    assert isinstance(status, iec61850.ServerStatus)
    assert status.logical == "state-changes-allowed"
    assert status.logical_code == 0
    assert status.physical == "operational"
    assert status.physical_code == 0
    assert status.local_detail is None


async def test_status_after_disconnect_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    await conn.disconnect()
    with pytest.raises(iec61850.IedConnectionError):
        await conn.get_server_status()


def test_status_from_native_keeps_other_value_and_detail() -> None:
    status = iec61850.ServerStatus.from_native(
        {
            "logical": "other",
            "logical_code": 7,
            "physical": "needs-commissioning",
            "physical_code": 3,
            "local_detail": [True, False, True],
        }
    )
    assert status.logical == "other"
    assert status.logical_code == 7
    assert status.physical == "needs-commissioning"
    assert status.local_detail == (True, False, True)
