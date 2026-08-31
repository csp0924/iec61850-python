"""End-to-end tests for control select / operate / cancel.

`models/demo.cid` carries one controllable point, `GGIO1.SPCSO1`. Each test
hosts the demonstration IED with that object bound to the control model
under test, which is how a server chooses between the five models of
IEC 61850-7-2 Table 67.
"""

from __future__ import annotations

import pytest

import _demo
import iec61850


async def test_direct_normal_operate_success() -> None:
    async with _demo.hosted_control("direct-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_NORMAL
            )
            outcome = await spc.operate(True)
            assert outcome.success is True
            assert outcome.add_cause is None
            stval = await conn.read_bool(f"{_demo.SWITCH}.stVal", iec61850.FC.ST)
            assert stval is True
        finally:
            await conn.disconnect()


async def test_direct_enhanced_operate_success_with_ct() -> None:
    """Operate on a direct-enhanced SPC drives Write Oper plus CommandTermination+."""
    async with _demo.hosted_control("direct-enhanced") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            outcome = await spc.operate(True)
            assert outcome.success is True
        finally:
            await conn.disconnect()


async def test_sbo_normal_select_then_operate() -> None:
    async with _demo.hosted_control("sbo-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_NORMAL
            )
            selected = await spc.select()
            assert selected is True
            outcome = await spc.operate(True)
            assert outcome.success is True
        finally:
            await conn.disconnect()


async def test_sbo_enhanced_select_with_value_then_operate() -> None:
    """SBOw and Oper share the same ctlNum and ctlVal; CT+ closes the loop."""
    async with _demo.hosted_control("sbo-enhanced") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_ENHANCED
            )
            # ctlNum auto-increments on select_with_value (counter is 1 after this).
            sel = await spc.select_with_value(True)
            assert sel.success is True
            # Oper in sbo-enhanced reuses the SBOw ctlNum; the client tracks
            # this internally, so the caller just sends the same ctlVal.
            outcome = await spc.operate(True)
            assert outcome.success is True
        finally:
            await conn.disconnect()


async def test_direct_normal_check_blocked() -> None:
    """A check handler answering BlockedByInterlocking fails the Operate.

    On direct-normal the rejection rides on the confirmed-error rather than a
    CommandTermination-, so the reported add cause is Unknown.
    """
    async with _demo.hosted_control("direct-normal", blocked=True) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_NORMAL
            )
            outcome = await spc.operate(True)
            assert outcome.success is False
            assert outcome.add_cause is iec61850.ControlAddCause.UNKNOWN
        finally:
            await conn.disconnect()


async def test_sbo_normal_operate_without_select_fails() -> None:
    """Operating an SBO-normal SPC without selecting must be rejected."""
    async with _demo.hosted_control("sbo-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_NORMAL
            )
            outcome = await spc.operate(True)
            assert outcome.success is False
        finally:
            await conn.disconnect()


async def test_cancel_after_select() -> None:
    """select() then cancel() on an SBO-normal SPC must succeed."""
    async with _demo.hosted_control("sbo-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_NORMAL
            )
            assert (await spc.select()) is True
            outcome = await spc.cancel(True)
            assert outcome.success is True
        finally:
            await conn.disconnect()


async def test_create_unknown_model_raises() -> None:
    with pytest.raises(ValueError):
        iec61850.ControlModel("not-a-model")


async def test_create_object_ref_missing_dot_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            conn.create_control_object(
                f"{_demo.DOMAIN}/GGIO1", iec61850.ControlModel.DIRECT_NORMAL
            )
    finally:
        await conn.disconnect()


async def test_origin_setter_and_test_flag() -> None:
    """Setters issue no MMS traffic; check they compose and operate still works."""
    async with _demo.hosted_control("direct-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_NORMAL
            )
            spc.set_origin(iec61850.OriginValue(or_cat=5, or_ident=b"py-test"))
            spc.set_test(True)
            spc.set_test(False)
            spc.set_synchro_check(False)
            spc.set_interlock_check(False)
            outcome = await spc.operate(True)
            assert outcome.success is True
        finally:
            await conn.disconnect()
