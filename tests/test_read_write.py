"""Tests for ``IedConnection.read`` and ``IedConnection.write``."""

from __future__ import annotations

import pytest

import _demo
import iec61850
from iec61850._facade import _compose_reference


def test_compose_reference_passthrough() -> None:
    assert _compose_reference("LD/LN.DO", None, None) == "LD/LN.DO"


def test_compose_reference_array_only() -> None:
    assert _compose_reference("LD/LN.DO", 2, None) == "LD/LN.DO(2)"


def test_compose_reference_array_with_component() -> None:
    assert _compose_reference("LD/LN.DO", 0, "stVal") == "LD/LN.DO(0).stVal"


def test_compose_reference_component_without_array_rejected() -> None:
    with pytest.raises(ValueError, match="component requires array_index"):
        _compose_reference("LD/LN.DO", None, "stVal")


def test_compose_reference_negative_array_rejected() -> None:
    with pytest.raises(ValueError, match="array_index must be >= 0"):
        _compose_reference("LD/LN.DO", -1, None)


async def test_read_int_enumerated(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read(_demo.MODE, iec61850.FC.ST)
        assert isinstance(value, int)
    finally:
        await conn.disconnect()


async def test_read_bool_status(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read(_demo.IND1, iec61850.FC.ST)
        assert value is True
    finally:
        await conn.disconnect()


async def test_read_float_measurand(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read(_demo.POWER, iec61850.FC.MX)
        assert isinstance(value, float)
    finally:
        await conn.disconnect()


async def test_read_visible_string(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read(_demo.VENDOR, iec61850.FC.DC)
        assert value == "rust61850"
    finally:
        await conn.disconnect()


async def test_write_int32_round_trip(writable_server: str) -> None:
    conn = await iec61850.IedConnection.connect(writable_server)
    try:
        await conn.write(_demo.WRITABLE_SETPOINT, iec61850.FC.SP, 52)
        readback = await conn.read(_demo.WRITABLE_SETPOINT, iec61850.FC.SP)
        assert readback == 52
    finally:
        await conn.disconnect()


async def test_write_visible_string_round_trip(writable_server: str) -> None:
    conn = await iec61850.IedConnection.connect(writable_server)
    try:
        await conn.write(_demo.WRITABLE_LABEL, iec61850.FC.SP, "feeder-3")
        readback = await conn.read(_demo.WRITABLE_LABEL, iec61850.FC.SP)
        assert readback == "feeder-3"
    finally:
        await conn.disconnect()


async def test_write_status_attribute_rejected(demo_server: str) -> None:
    """FC=ST is never writable, so the server refuses the Write."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedError):
            await conn.write(_demo.IND1, iec61850.FC.ST, True)
    finally:
        await conn.disconnect()


async def test_write_unsupported_type_rejected(writable_server: str) -> None:
    conn = await iec61850.IedConnection.connect(writable_server)
    try:
        with pytest.raises(ValueError, match="write value must be"):
            await conn.write(_demo.WRITABLE_LABEL, iec61850.FC.SP, object())
    finally:
        await conn.disconnect()
