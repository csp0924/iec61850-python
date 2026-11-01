"""End-to-end tests for control select / operate / cancel.

`models/demo.cid` carries one controllable point, `GGIO1.SPCSO1`. Each test
hosts the demonstration IED with that object bound to the control model
under test, which is how a server chooses between the five models of
IEC 61850-7-2 Table 67.
"""

from __future__ import annotations

import asyncio

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


async def test_operate_accepts_a_tagged_value() -> None:
    async with _demo.hosted_control("direct-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_NORMAL
            )
            outcome = await spc.operate({"type": "bool", "value": True})
            assert outcome.success is True
            stval = await conn.read_bool(f"{_demo.SWITCH}.stVal", iec61850.FC.ST)
            assert stval is True
            with pytest.raises(ValueError, match="control value is missing the 'type' key"):
                await spc.operate({"value": True})
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
            assert selected.success is True
            assert selected
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

    The server reports the refusal with a LastApplError ahead of the negative
    response, so the outcome carries the add cause and the error itself.
    """
    async with _demo.hosted_control("direct-normal", blocked=True) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_NORMAL
            )
            outcome = await spc.operate(True)
            assert outcome.success is False
            assert not outcome
            assert outcome.add_cause is iec61850.ControlAddCause.BLOCKED_BY_INTERLOCKING
            lae = outcome.last_appl_error
            assert lae is not None
            assert lae.ctl_obj == f"{_demo.DOMAIN}/GGIO1$CO$SPCSO1$Oper"
            assert lae.add_cause is iec61850.ControlAddCause.BLOCKED_BY_INTERLOCKING
            assert lae.error == "no-error"
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
            assert (await spc.select()).success is True
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


async def test_sbo_enhanced_refused_select_carries_last_appl_error() -> None:
    """A refused SelectWithValue reports the LastApplError of the SBOw."""
    async with _demo.hosted_control("sbo-enhanced", blocked=True) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_ENHANCED
            )
            sel = await spc.select_with_value(True)
            assert not sel
            assert sel.add_cause is iec61850.ControlAddCause.BLOCKED_BY_INTERLOCKING
            lae = sel.last_appl_error
            assert lae is not None
            assert lae.ctl_obj == f"{_demo.DOMAIN}/GGIO1$CO$SPCSO1$SBOw"
            assert lae.ctl_num == 1
            assert lae.or_cat == 3
        finally:
            await conn.disconnect()


async def test_sbo_normal_second_select_is_refused() -> None:
    """A selection the client already holds cannot be taken again."""
    async with _demo.hosted_control("sbo-normal") as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.SBO_NORMAL
            )
            assert await spc.select()
            again = await spc.select()
            assert again.success is False
            assert again.add_cause is not None
        finally:
            await conn.disconnect()


async def _operate_under_oper_timeout(
    oper_timeout_ms: int, *, hold: bool
) -> iec61850.ControlOutcome:
    """Operate `GGIO1.SPCSO1` under direct-enhanced with an operate time limit.

    With `hold` the operate callback waits on an event that is set only once
    the client has its outcome, so the server's time limit is what ends the
    command; otherwise the callback returns at once.
    """
    release = asyncio.Event()

    def configure(server: iec61850.IedServer) -> None:
        async def operate(_path: str, value: object, _action: dict) -> None:
            if hold:
                await release.wait()
            server.update_bool(f"{_demo.SRV_SWITCH}.stVal", bool(value))

        server.on_control(
            _demo.SRV_SWITCH,
            ctl_model="direct-enhanced",
            operate=operate,
            oper_timeout_ms=oper_timeout_ms,
        )

    async with _demo.hosted_demo(
        control_blocks=False, configure=configure, ctl_model="direct-enhanced"
    ) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            return await spc.operate(True)
        finally:
            release.set()
            await asyncio.sleep(0)
            await conn.disconnect()


async def test_operate_past_the_oper_timeout_ends_with_time_limit_over() -> None:
    outcome = await _operate_under_oper_timeout(200, hold=True)
    assert outcome.success is False
    assert outcome.add_cause is iec61850.ControlAddCause.TIME_LIMIT_OVER
    assert outcome.last_appl_error is not None
    assert outcome.last_appl_error.add_cause is iec61850.ControlAddCause.TIME_LIMIT_OVER


async def test_operate_within_the_oper_timeout_succeeds() -> None:
    outcome = await _operate_under_oper_timeout(5000, hold=False)
    assert outcome.success is True
    assert outcome.add_cause is None


def test_blocked_by_command_keeps_its_former_name() -> None:
    cause = iec61850.ControlAddCause
    assert cause.ABORTION_BY_COMMAND is cause.BLOCKED_BY_COMMAND
    assert cause("abortion-by-command") is cause.BLOCKED_BY_COMMAND
    assert cause("blocked-by-command") is cause.BLOCKED_BY_COMMAND


def test_control_outcome_from_native() -> None:
    outcome = iec61850.ControlOutcome.from_native(
        {
            "success": False,
            "add_cause": "blocked-by-mode",
            "last_appl_error": {
                "ctl_obj": "IED1LD0/CSWI1$CO$Pos$Oper",
                "error": "unknown",
                "or_cat": 2,
                "or_ident": b"op",
                "ctl_num": 7,
                "add_cause": "blocked-by-mode",
            },
        }
    )
    assert not outcome
    assert outcome.add_cause is iec61850.ControlAddCause.BLOCKED_BY_MODE
    assert outcome.last_appl_error == iec61850.LastApplError(
        ctl_obj="IED1LD0/CSWI1$CO$Pos$Oper",
        error="unknown",
        or_cat=2,
        or_ident=b"op",
        ctl_num=7,
        add_cause=iec61850.ControlAddCause.BLOCKED_BY_MODE,
    )
    assert iec61850.ControlOutcome.from_native(
        {"success": True, "add_cause": None, "last_appl_error": None}
    )
