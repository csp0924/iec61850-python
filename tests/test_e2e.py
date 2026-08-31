"""Positive end-to-end tests against a server this package hosts.

Every case drives the whole stack — Python facade -> PyO3 ->
pyo3-async-runtimes -> iec61850-client -> MMS -> the server built from
`models/demo.cid` — with nothing mocked and no external peer.

Model references used here:

- `LLN0.Mod.stVal` (FC=ST) — Enumerated, carried as an integer on the wire
- `GGIO1.Ind1.stVal` (FC=ST) — Boolean, the type-mismatch cases
- `GGIO1.Ind1.q` (FC=ST) — Quality bit string
- `GGIO1.Ind1.t` (FC=ST) — UtcTime, 8 bytes
- `MMXU1.TotW.mag.f` (FC=MX) — Float32
- `LLN0.NamPlt.vendor` (FC=DC) — VisibleString
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import _demo
import iec61850

IND1_Q = f"{_demo.DOMAIN}/GGIO1.Ind1.q"
IND1_T = f"{_demo.DOMAIN}/GGIO1.Ind1.t"
MISSING = f"{_demo.DOMAIN}/GGIO1.DoesNotExist.stVal"


async def test_e2e_connect_then_is_connected_true(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert conn.is_connected is True
    finally:
        await conn.disconnect()


async def test_e2e_is_connected_false_after_disconnect(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    await conn.disconnect()
    assert conn.is_connected is False


async def test_e2e_read_int32_enumerated_status(demo_server: str) -> None:
    """`LLN0.Mod.stVal` is an ENS; an Enumerated travels as an integer."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read_int32(_demo.MODE, iec61850.FC.ST)
        assert value == 1
    finally:
        await conn.disconnect()


async def test_e2e_read_int32_on_boolean_raises_data_access(demo_server: str) -> None:
    """`Ind1.stVal` is a Boolean, so the integer reader must refuse it."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.read_int32(_demo.IND1, iec61850.FC.ST)
    finally:
        await conn.disconnect()


async def test_e2e_read_int32_on_float_raises_data_access(demo_server: str) -> None:
    """`TotW.mag.f` is a Float32, so the integer reader must refuse it."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.read_int32(_demo.POWER, iec61850.FC.MX)
    finally:
        await conn.disconnect()


async def test_e2e_read_int32_unknown_object_raises(demo_server: str) -> None:
    """A reference the model does not carry fails inside the error hierarchy.

    Which variant the server picks is its own choice; the contract is only
    that the failure is an `IedError`.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedError):
            await conn.read_int32(MISSING, iec61850.FC.ST)
    finally:
        await conn.disconnect()


async def test_e2e_double_connect_two_independent_sessions(demo_server: str) -> None:
    """Two associations against one server each read on their own."""
    a = await iec61850.IedConnection.connect(demo_server)
    b = await iec61850.IedConnection.connect(demo_server)
    try:
        assert a.is_connected is True
        assert b.is_connected is True
        assert await a.read_int32(_demo.MODE, iec61850.FC.ST) == 1
        assert await b.read_int32(_demo.MODE, iec61850.FC.ST) == 1
    finally:
        await a.disconnect()
        await b.disconnect()


# --- typed scalar surface ---------------------------------------------------


async def test_e2e_read_bool_on_sps(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.read_bool(_demo.IND1, iec61850.FC.ST) is True
        assert await conn.read_bool(_demo.IND2, iec61850.FC.ST) is False
    finally:
        await conn.disconnect()


async def test_e2e_read_int64_on_status(demo_server: str) -> None:
    """The 64-bit reader widens the server's 32-bit Enumerated."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read_int64(_demo.MODE, iec61850.FC.ST)
        assert value == 1
    finally:
        await conn.disconnect()


async def test_e2e_read_uint32_on_nonneg_status(demo_server: str) -> None:
    """`Mod.stVal` is a non-negative Enumerated, so Integer converts to Unsigned."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read_uint32(_demo.MODE, iec61850.FC.ST)
        assert value >= 0
    finally:
        await conn.disconnect()


async def test_e2e_read_float_on_mv(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.read_float(_demo.POWER, iec61850.FC.MX) == pytest.approx(
            230.5
        )
    finally:
        await conn.disconnect()


async def test_e2e_read_float64_on_float32_rejected(demo_server: str) -> None:
    """The 64-bit float reader refuses a Float32 rather than lose precision."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.read_float64(_demo.POWER, iec61850.FC.MX)
    finally:
        await conn.disconnect()


async def test_e2e_read_timestamp_returns_datetime(demo_server: str) -> None:
    """`Ind1.t` was never written, so its eight zero bytes decode to the epoch."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        value = await conn.read_timestamp(IND1_T, iec61850.FC.ST)
        assert isinstance(value, datetime)
        assert value.tzinfo == timezone.utc
    finally:
        await conn.disconnect()


async def test_e2e_read_quality_returns_dataclass(demo_server: str) -> None:
    """`Ind1.q` defaults to Quality(0): valid, with every detail flag clear."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        q = await conn.read_quality(IND1_Q, iec61850.FC.ST)
        assert isinstance(q, iec61850.Quality)
        assert q.validity == iec61850.Validity.GOOD
        assert q.detail.overflow is False
        assert q.detail.failure is False
        assert q.test is False
        assert q.operator_blocked is False
    finally:
        await conn.disconnect()


# --- identification -------------------------------------------------------


async def test_e2e_nameplate_carries_identification_defaults(demo_server: str) -> None:
    """The CID name plates carry the same strings the MMS Identify defaults use.

    `IdentificationStrings` defaults to vendor `rust61850`, model
    `iec61850-rust`, and the crate version; the SCL states the model-level
    counterpart so a client that browses the model sees the same identity.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.read_string(_demo.VENDOR, iec61850.FC.DC) == "rust61850"
        sw_rev = await conn.read_string(
            f"{_demo.DOMAIN}/LLN0.NamPlt.swRev", iec61850.FC.DC
        )
        assert sw_rev == "0.1.0"
        model = await conn.read_string(
            f"{_demo.DOMAIN}/LPHD1.PhyNam.model", iec61850.FC.DC
        )
        assert model == "iec61850-rust"
        phy_vendor = await conn.read_string(
            f"{_demo.DOMAIN}/LPHD1.PhyNam.vendor", iec61850.FC.DC
        )
        assert phy_vendor == "rust61850"
    finally:
        await conn.disconnect()


# --- write access policy --------------------------------------------------


async def test_e2e_write_int32_disallowed_fc_rejected(demo_server: str) -> None:
    """FC=ST is never writable, whatever the write access policy says."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedError):
            await conn.write_int32(_demo.MODE, iec61850.FC.ST, 5)
    finally:
        await conn.disconnect()


async def test_e2e_write_bool_on_disallowed_fc_rejected(demo_server: str) -> None:
    """`SPS.stVal` sits under FC=ST, which the server refuses to write."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedError):
            await conn.write_bool(_demo.IND1, iec61850.FC.ST, True)
    finally:
        await conn.disconnect()


async def test_e2e_write_float_on_disallowed_fc_rejected(demo_server: str) -> None:
    """`MV.mag.f` sits under FC=MX, which the server refuses to write."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedError):
            await conn.write_float(_demo.POWER, iec61850.FC.MX, 1.0)
    finally:
        await conn.disconnect()


async def test_e2e_write_visible_string_roundtrip(writable_server: str) -> None:
    """A VisibleString under FC=SP is inside the default write policy."""
    conn = await iec61850.IedConnection.connect(writable_server)
    try:
        await conn.write_visible_string(
            _demo.WRITABLE_LABEL, iec61850.FC.SP, "feeder-7"
        )
        readback = await conn.read_string(_demo.WRITABLE_LABEL, iec61850.FC.SP)
        assert readback == "feeder-7"
    finally:
        await conn.disconnect()


# --- directory query surface ----------------------------------------------


async def test_e2e_get_server_directory_lists_ld(demo_server: str) -> None:
    """`DemoIED` plus logical device `LD0` gives MMS domain `DemoIEDLD0`."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.get_server_directory() == [_demo.DOMAIN]
    finally:
        await conn.disconnect()


async def test_e2e_get_logical_device_directory_lists_lns(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        lns = await conn.get_logical_device_directory(_demo.DOMAIN)
        assert set(lns) == {"LLN0", "LPHD1", "MMXU1", "GGIO1"}
    finally:
        await conn.disconnect()


async def test_e2e_get_logical_node_directory_data_objects(demo_server: str) -> None:
    """`AcsiClass.DATA_OBJECT` lists the data objects of one logical node."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        dos = await conn.get_logical_node_directory(
            f"{_demo.DOMAIN}/GGIO1", iec61850.AcsiClass.DATA_OBJECT
        )
        assert set(dos) == {"Ind1", "Ind2", "Ind3", "Ind4", "SPCSO1"}
    finally:
        await conn.disconnect()


async def test_e2e_get_logical_node_directory_control_rejected(
    demo_server: str,
) -> None:
    """Control objects are reached through `create_control_object`.

    They are not a directory class, so the query is refused locally.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.get_logical_node_directory(
                f"{_demo.DOMAIN}/GGIO1", iec61850.AcsiClass.CONTROL
            )
    finally:
        await conn.disconnect()


async def test_e2e_get_data_directory_returns_members(demo_server: str) -> None:
    """One level below a data object are its data attributes, without FC tags."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        children = await conn.get_data_directory(f"{_demo.DOMAIN}/GGIO1.Ind1")
        assert set(children) == {"stVal", "q", "t"}
    finally:
        await conn.disconnect()
