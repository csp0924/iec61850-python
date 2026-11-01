"""End-to-end tests for the cap on the names one directory walk collects."""

from __future__ import annotations

import pytest

import _demo
import iec61850

LLN0 = f"{_demo.DOMAIN}/LLN0"
GGIO1 = f"{_demo.DOMAIN}/GGIO1"


async def test_small_cap_aborts_directory_walk(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server, max_name_list_names=2)
    try:
        with pytest.raises(iec61850.IedServiceError) as info:
            await conn.get_logical_node_directory(GGIO1, iec61850.AcsiClass.DATA_OBJECT)
    finally:
        await conn.disconnect()
    assert info.value.reason == "name-list-limit"
    assert info.value.limit == 2


async def test_large_cap_lets_directory_walk_finish(demo_server: str) -> None:
    default = await iec61850.IedConnection.connect(demo_server)
    capped = await iec61850.IedConnection.connect(
        demo_server, max_name_list_names=100_000
    )
    try:
        expected = await default.get_logical_node_directory(
            GGIO1, iec61850.AcsiClass.DATA_OBJECT
        )
        names = await capped.get_logical_node_directory(
            GGIO1, iec61850.AcsiClass.DATA_OBJECT
        )
    finally:
        await default.disconnect()
        await capped.disconnect()
    assert names == expected
    assert "Ind1" in names


async def test_cap_applies_to_device_model_walk(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server, max_name_list_names=1)
    try:
        with pytest.raises(iec61850.IedServiceError) as info:
            await conn.get_server_directory()
        with pytest.raises(iec61850.IedServiceError):
            await conn.get_device_model(refresh=True)
    finally:
        await conn.disconnect()
    assert info.value.reason == "name-list-limit"
    assert info.value.limit == 1


async def test_client_config_passes_cap(demo_server: str) -> None:
    host, port = demo_server.rsplit(":", 1)
    cfg = iec61850.Iec61850ClientConfig(
        address=host,
        port=int(port),
        report_dispatcher_interval_ms=None,
        max_name_list_names=2,
    )
    async with iec61850.Iec61850Client(cfg) as cli:
        with pytest.raises(iec61850.IedServiceError) as info:
            await cli.connection.get_logical_node_directory(
                LLN0, iec61850.AcsiClass.DATA_OBJECT
            )
    assert info.value.reason == "name-list-limit"
