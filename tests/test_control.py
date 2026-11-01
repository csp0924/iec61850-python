"""End-to-end tests for control select / operate / cancel.

`models/demo.cid` carries one controllable point, `GGIO1.SPCSO1`. Each test
hosts the demonstration IED with that object bound to the control model
under test, which is how a server chooses between the five models of
IEC 61850-7-2 Table 67.
"""

from __future__ import annotations

import asyncio
import threading
import time

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


async def test_a_synchronous_operate_past_the_oper_timeout_ends_with_time_limit_over() -> None:
    """A blocking synchronous operate does not keep the command alive: the
    time limit ends it, and the callback finishes on its own thread later."""
    release = threading.Event()
    finished = threading.Event()

    def configure(server: iec61850.IedServer) -> None:
        def operate(_path: str, _value: object, _action: dict) -> None:
            release.wait(10)
            finished.set()

        server.on_control(
            _demo.SRV_SWITCH,
            ctl_model="direct-enhanced",
            operate=operate,
            oper_timeout_ms=200,
        )

    async with _demo.hosted_demo(
        control_blocks=False, configure=configure, ctl_model="direct-enhanced"
    ) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            started = time.perf_counter()
            outcome = await spc.operate(True)
            elapsed = time.perf_counter() - started
        finally:
            release.set()
            await conn.disconnect()
        assert await asyncio.to_thread(finished.wait, 10)
    assert outcome.success is False
    assert outcome.add_cause is iec61850.ControlAddCause.TIME_LIMIT_OVER
    # The callback was still blocked, so only the time limit can have ended
    # the command; the bound only rules out waiting for the callback.
    assert elapsed < 5


async def test_a_blocked_synchronous_operate_leaves_other_associations_served() -> None:
    """While a synchronous operate blocks, another association reads."""
    release = threading.Event()
    entered = threading.Event()

    def configure(server: iec61850.IedServer) -> None:
        def operate(_path: str, _value: object, _action: dict) -> None:
            entered.set()
            release.wait(10)

        server.on_control(
            _demo.SRV_SWITCH, ctl_model="direct-enhanced", operate=operate
        )

    async with _demo.hosted_demo(
        control_blocks=False, configure=configure, ctl_model="direct-enhanced"
    ) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        other = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            pending = asyncio.ensure_future(spc.operate(True))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                assert await other.read_bool(_demo.IND1, iec61850.FC.ST) is True
                assert not pending.done()
            finally:
                release.set()
            outcome = await pending
            assert outcome.success is True
        finally:
            await other.disconnect()
            await conn.disconnect()


APC_REF = f"{_demo.DOMAIN}/GGIO1.APCSO1"
APC_RECEIVED = [1.5]
SET_MAG_REF = f"{_demo.DOMAIN}/GGIO1.SetMag1.setMag"


def _analogue_scl() -> str:
    """The demonstration CID plus an APC whose ctlVal is an AnalogueValue
    holding `f`, and an ASG whose `setMag` is the same structure under FC=SP."""
    text = _demo.DEMO_CID.read_text(encoding="utf-8")
    replacements = [
        (
            '      <DO name="SPCSO1" type="SPC_T"/>\n',
            '      <DO name="SPCSO1" type="SPC_T"/>\n'
            '      <DO name="APCSO1" type="APC_T"/>\n'
            '      <DO name="SetMag1" type="ASG_T"/>\n',
        ),
        (
            '    <DOType id="SPC_T" cdc="SPC"',
            """    <DOType id="APC_T" cdc="APC">
      <DA name="Oper" fc="CO" bType="Struct" type="APCCmd_T"/>
      <DA name="ctlModel" fc="CF" bType="Enum" type="CtlModelKind"/>
      <DA name="origin" fc="ST" bType="Struct" type="Originator_T"/>
      <DA name="ctlNum" fc="ST" bType="INT8U"/>
      <DA name="mxVal" fc="MX" bType="Struct" type="AnalogValue_T"/>
      <DA name="q" fc="MX" bType="Quality"/>
      <DA name="t" fc="MX" bType="Timestamp"/>
    </DOType>

    <DOType id="ASG_T" cdc="ASG">
      <DA name="setMag" fc="SP" bType="Struct" type="AnalogValue_T"/>
    </DOType>

    <DAType id="APCCmd_T">
      <BDA name="ctlVal" bType="Struct" type="AnalogValue_T"/>
      <BDA name="origin" bType="Struct" type="Originator_T"/>
      <BDA name="ctlNum" bType="INT8U"/>
      <BDA name="T" bType="Timestamp"/>
      <BDA name="Test" bType="BOOLEAN"/>
      <BDA name="Check" bType="Check"/>
    </DAType>

    <DOType id="SPC_T" cdc="SPC\"""",
        ),
        (
            "          <LN prefix=\"\" lnClass=\"GGIO\" inst=\"1\" lnType=\"GGIO_T\">\n",
            "          <LN prefix=\"\" lnClass=\"GGIO\" inst=\"1\" lnType=\"GGIO_T\">\n"
            "            <DOI name=\"APCSO1\">\n"
            "              <DAI name=\"ctlModel\"><Val>direct-with-normal-security</Val></DAI>\n"
            "            </DOI>\n",
        ),
    ]
    for old, new in replacements:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


async def test_apc_operate_sends_the_analogue_value_structure() -> None:
    received: list[object] = []
    server = iec61850.IedServer.from_scl_str(_analogue_scl(), ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")

    def operate(_path: str, value: object, _action: dict) -> None:
        received.append(value)

    server.on_control(
        f"{_demo.LD_INST}/GGIO1.APCSO1", ctl_model="direct-normal", operate=operate
    )
    async with server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            apc = conn.create_control_object(
                APC_REF, iec61850.ControlModel.DIRECT_NORMAL
            )
            outcome = await apc.operate(
                {"type": "structure", "value": [{"type": "float32", "value": 1.5}]}
            )
            assert outcome.success is True
            for _ in range(150):
                if received:
                    break
                await asyncio.sleep(0.02)
        finally:
            await conn.disconnect()
    assert received == [APC_RECEIVED]


async def test_structure_attribute_round_trips_through_write() -> None:
    server = iec61850.IedServer.from_scl_str(_analogue_scl(), ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    async with server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            await conn.write(SET_MAG_REF, iec61850.FC.SP, {"type": "structure", "value": [2.5]})
            assert await conn.read_float(f"{SET_MAG_REF}.f", iec61850.FC.SP) == 2.5
            await conn.write(
                SET_MAG_REF,
                iec61850.FC.SP,
                {"type": "structure", "value": ({"type": "float32", "value": -4.0},)},
            )
            assert await conn.read_float(f"{SET_MAG_REF}.f", iec61850.FC.SP) == -4.0
            with pytest.raises(ValueError, match="a list or tuple of components"):
                await conn.write(SET_MAG_REF, iec61850.FC.SP, {"type": "structure", "value": 2.5})
            with pytest.raises(ValueError, match=r"\['value'\]\[0\]"):
                await conn.write(
                    SET_MAG_REF, iec61850.FC.SP, {"type": "structure", "value": [None]}
                )
        finally:
            await conn.disconnect()


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
