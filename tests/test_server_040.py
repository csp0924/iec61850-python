"""Server API end to end: each case starts a server hosted by this package and
drives it from the in-package client over TCP."""

from __future__ import annotations

import asyncio
import queue

import pytest

import _demo
from iec61850 import (
    FC,
    AcsiClass,
    ControlModel,
    DataSetMember,
    IedConnection,
    IedConnectionError,
    IedDataAccessError,
    IedError,
    IedServer,
    IedServerError,
    RcbWriteMask,
    SclError,
)

URCB_MEAS = f"{_demo.DOMAIN}/LLN0$RP$urcbMeas"
BRCB_MEAS = f"{_demo.DOMAIN}/LLN0$BR$brcbMeas"


def _demo_server(*, indexed: bool = False) -> IedServer:
    """The demonstration IED on a free loopback port, its RCBs optionally
    indexed."""
    text = _demo.DEMO_CID.read_text(encoding="utf-8")
    if indexed:
        text = text.replace('indexed="false"', 'indexed="true"')
    server = IedServer.from_scl_str(text, ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    return server


# ── register_from_model ─────────────────────────────────────────────────────


async def test_model_control_blocks_are_not_served_without_register_from_model() -> None:
    server = _demo_server()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(URCB_MEAS)
            assert server.connection_count == 1
        finally:
            await conn.disconnect()


async def test_register_from_model_while_running_returns_the_summary() -> None:
    server = _demo_server()
    async with server:
        summary = server.register_from_model()
        assert summary == {
            "data_sets": 2,
            "urcbs": 1,
            "brcbs": 1,
            "gocbs": 1,
            "lcbs": 0,
            "sgcbs": 0,
            "control_objects": 1,
        }
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MEAS)
            assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
            brcb = await conn.get_rcb_values(BRCB_MEAS)
            assert brcb.is_buffered
        finally:
            await conn.disconnect()


async def test_register_from_model_names_indexed_instances_with_two_digits() -> None:
    server = _demo_server(indexed=True)
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["urcbs"] == 2
        assert summary["brcbs"] == 2
        conn = await IedConnection.connect(server.bound_addr)
        try:
            for name in ("urcbMeas01", "urcbMeas02"):
                rcb = await conn.get_rcb_values(f"{_demo.DOMAIN}/LLN0$RP${name}")
                assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(URCB_MEAS)
        finally:
            await conn.disconnect()


async def test_register_from_model_twice_raises_with_the_reference() -> None:
    server = _demo_server()
    async with server:
        assert server.register_from_model() is not None
        with pytest.raises(IedServerError) as info:
            server.register_from_model()
        assert info.value.reference is not None
        assert info.value.reference.startswith(f"{_demo.DOMAIN}/")


async def test_register_from_model_queued_before_start_runs_at_start() -> None:
    server = _demo_server()
    assert server.register_from_model() is None
    with pytest.raises(RuntimeError, match="already queued"):
        server.register_from_model()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MEAS)
            assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
        finally:
            await conn.disconnect()


async def test_queued_register_from_model_conflicting_with_add_dataset_fails_start() -> None:
    server = _demo_server()
    server.add_dataset(_demo.DS_MEAS, [_demo.SRV_POWER])
    server.register_from_model()
    with pytest.raises(IedServerError) as info:
        await server.start()
    assert info.value.reference == f"{_demo.DOMAIN}/{_demo.DS_MEAS}"
    assert not server.is_running


async def test_register_from_model_after_explicit_urcb_raises_while_running() -> None:
    server = _demo_server()
    _demo.wire_control_blocks(server)
    async with server:
        with pytest.raises(IedServerError) as info:
            server.register_from_model()
        assert info.value.reference is not None
        conn = await IedConnection.connect(server.bound_addr)
        try:
            # Nothing of the model was registered: the BRCB stays absent.
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(BRCB_MEAS)
            assert (await conn.get_rcb_values(URCB_MEAS)).dataset is not None
        finally:
            await conn.disconnect()


async def test_register_from_model_keeps_the_on_control_handlers() -> None:
    server = _demo_server()
    operated: list[object] = []

    def operate(_path: str, value: object, _action: dict) -> None:
        operated.append(value)
        server.update_bool(f"{_demo.SRV_SWITCH}.stVal", bool(value))

    server.on_control(_demo.SRV_SWITCH, ctl_model="direct-normal", operate=operate)
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["control_objects"] == 0
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(_demo.SWITCH, ControlModel.DIRECT_NORMAL)
            outcome = await spc.operate(True)
            assert outcome.success is True
            assert operated == [True]
        finally:
            await conn.disconnect()


async def test_on_control_after_register_from_model_replaces_the_entry() -> None:
    server = _demo_server()
    operated: list[object] = []
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["control_objects"] == 1
        server.on_control(
            _demo.SRV_SWITCH,
            ctl_model="direct-normal",
            operate=lambda _p, v, _a: operated.append(v),
        )
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(_demo.SWITCH, ControlModel.DIRECT_NORMAL)
            outcome = await spc.operate(True)
            assert outcome.success is True
            assert operated == [True]
        finally:
            await conn.disconnect()


async def test_register_from_model_after_stop_raises() -> None:
    server = _demo_server()
    async with server:
        pass
    with pytest.raises(RuntimeError, match="stopped"):
        server.register_from_model()


# ── update_array ────────────────────────────────────────────────────────────

ARRAY_SCL = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
            <DataSet name="dsArr">
              <FCDA ldInst="GenericIO" prefix="" lnClass="GGIO" lnInst="1"
                    doName="Arr" daName="vals" fc="MX"/>
            </DataSet>
            <ReportControl name="urcbArr" rptID="arr" datSet="dsArr"
                           buffered="false" indexed="false" bufTime="0"
                           intgPd="0" confRev="1">
              <TrgOps dchg="true"/>
              <OptFields seqNum="true" dataSet="true" reasonCode="true"/>
              <RptEnabled max="1"/>
            </ReportControl>
          </LN0>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1"/>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="ENC_1"/>
    </LNodeType>
    <LNodeType id="GGIO_1" lnClass="GGIO">
      <DO name="Ind1" type="SPS_1"/>
      <DO name="Arr" type="ARR_1"/>
    </LNodeType>
    <DOType id="ENC_1" cdc="ENC">
      <DA name="stVal" fc="ST" bType="Enum" type="BehaviourKind"/>
    </DOType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN" dchg="true"/>
    </DOType>
    <DOType id="ARR_1" cdc="MV">
      <DA name="vals" fc="MX" bType="INT32" count="3" dchg="true"/>
      <DA name="mags" fc="MX" bType="Struct" type="AnalogueValue" count="2" dchg="true"/>
      <DA name="flags" fc="MX" bType="BOOLEAN" count="2"/>
    </DOType>
    <DAType id="AnalogueValue">
      <BDA name="f" bType="FLOAT32"/>
    </DAType>
    <EnumType id="BehaviourKind">
      <EnumVal ord="1">on</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""

ARR_VALS = "GenericIO/GGIO1.Arr.vals"
ARR_MAGS = "GenericIO/GGIO1.Arr.mags"
ARR_FLAGS = "GenericIO/GGIO1.Arr.flags"
URCB_ARR = "IED1GenericIO/LLN0$RP$urcbArr"


def _array_server() -> IedServer:
    server = IedServer.from_scl_str(ARRAY_SCL, ied_name="IED1")
    server.bind("127.0.0.1:0")
    return server


async def _next_report(conn: IedConnection, sink: queue.Queue, timeout_s: float):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        await conn.poll_reports(100)
        try:
            return sink.get_nowait()
        except queue.Empty:
            continue
    return None


async def test_update_array_writes_every_element() -> None:
    server = _array_server()
    async with server:
        server.update_array(ARR_VALS, [1, -2, 3])
        server.update_array(ARR_MAGS, [[1.5], {"f": 2.5}])
        server.update_array(ARR_FLAGS, (True, {"type": "bool", "value": False}))
        conn = await IedConnection.connect(server.bound_addr)
        try:
            assert await conn.read("IED1GenericIO/GGIO1.Arr.vals", FC.MX) == [1, -2, 3]
            assert await conn.read("IED1GenericIO/GGIO1.Arr.mags", FC.MX) == [[1.5], [2.5]]
            assert await conn.read("IED1GenericIO/GGIO1.Arr.flags", FC.MX) == [True, False]
        finally:
            await conn.disconnect()


async def test_update_array_sends_one_report_for_the_whole_array() -> None:
    server = _array_server()
    server.register_from_model()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_ARR)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(rcb, RcbWriteMask.fields("resv", "rpt_ena"))
            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(URCB_ARR, sink.put, rpt_id="arr")

            server.update_array(ARR_VALS, [4, 5, 6])

            report = await _next_report(conn, sink, 4.0)
            assert report is not None, "no report after update_array"
            assert [e.value for e in report.entries] == [[4, 5, 6]]
            assert await _next_report(conn, sink, 0.5) is None
        finally:
            await conn.disconnect()


async def test_update_array_refuses_a_value_that_does_not_fit() -> None:
    server = _array_server()
    async with server:
        server.update_array(ARR_VALS, [7, 8, 9])
        with pytest.raises(ValueError, match="must hold 3 elements, got 2"):
            server.update_array(ARR_VALS, [1, 2])
        with pytest.raises(ValueError, match=r"values\[1\] must be an i64"):
            server.update_array(ARR_VALS, [1, "x", 3])
        with pytest.raises(ValueError, match=r"values\[0\] must be a bool"):
            server.update_array(ARR_FLAGS, [1, True])
        with pytest.raises(ValueError, match="missing the member 'f'"):
            server.update_array(ARR_MAGS, [{"g": 1.0}, {"f": 2.0}])
        with pytest.raises(ValueError, match="is not an array attribute"):
            server.update_array("GenericIO/GGIO1.Ind1.stVal", [True])
        with pytest.raises(KeyError):
            server.update_array("GenericIO/GGIO1.Nope.vals", [1])
        conn = await IedConnection.connect(server.bound_addr)
        try:
            assert await conn.read("IED1GenericIO/GGIO1.Arr.vals", FC.MX) == [7, 8, 9]
        finally:
            await conn.disconnect()


def test_update_array_before_start_raises() -> None:
    server = _array_server()
    with pytest.raises(RuntimeError, match="not running"):
        server.update_array(ARR_VALS, [1, 2, 3])


# ── on_read / on_write context ──────────────────────────────────────────────


async def test_on_read_with_context_receives_the_request() -> None:
    server = _demo_server()
    seen: list[dict] = []

    def handler(path: str, ctx: dict) -> None:
        assert path == _demo.SRV_IND1
        seen.append(ctx)
        # No server lock is held here, so an update goes through.
        server.update_bool(_demo.SRV_IND2, True)

    server.on_read(_demo.SRV_IND1, handler, with_context=True)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.read_bool(_demo.IND1, FC.ST)
            await conn.read(f"{_demo.DOMAIN}/GGIO1.Ind1", FC.ST)
            assert await conn.read_bool(_demo.IND2, FC.ST) is True
        finally:
            await conn.disconnect()
    assert len(seen) == 2
    leaf, parent = seen
    assert leaf["path"] == "GGIO1$ST$Ind1$stVal"
    assert leaf["fc"] == "ST"
    assert leaf["requested"] == "GGIO1$ST$Ind1$stVal"
    assert leaf["conn_id"] > 0
    assert parent["path"] == "GGIO1$ST$Ind1$stVal"
    assert parent["requested"] == "GGIO1$ST$Ind1"
    assert parent["conn_id"] == leaf["conn_id"]


async def test_on_read_without_context_keeps_the_single_argument() -> None:
    server = _demo_server()
    calls: list[str] = []

    def handler(path: str) -> bool:
        calls.append(path)
        return True

    server.on_read(_demo.SRV_IND1, handler)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            assert await conn.read_bool(_demo.IND1, FC.ST) is True
        finally:
            await conn.disconnect()
    assert calls == [_demo.SRV_IND1]


async def test_on_write_with_context_receives_the_request() -> None:
    server = IedServer.from_model_spec(_demo.writable_spec())
    server.bind("127.0.0.1:0")
    seen: list[tuple[object, dict]] = []
    setpoint = f"{_demo.WRITABLE_LD}/GGIO1.SetPt1.setVal"

    def handler(path: str, value: object, ctx: dict) -> bool:
        assert path == setpoint
        seen.append((value, ctx))
        server.update_string(f"{_demo.WRITABLE_LD}/GGIO1.Label.setSrc", "written")
        return True

    server.on_write(setpoint, handler, with_context=True)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 42)
            assert await conn.read_int32(_demo.WRITABLE_SETPOINT, FC.SP) == 42
            assert await conn.read_string(_demo.WRITABLE_LABEL, FC.SP) == "written"
        finally:
            await conn.disconnect()
    assert len(seen) == 1
    value, ctx = seen[0]
    assert value == 42
    assert ctx["path"] == "GGIO1$SP$SetPt1$setVal"
    assert ctx["fc"] == "SP"
    assert ctx["requested"] == "GGIO1$SP$SetPt1$setVal"
    assert ctx["conn_id"] > 0


async def test_on_write_without_context_keeps_two_arguments() -> None:
    server = IedServer.from_model_spec(_demo.writable_spec())
    server.bind("127.0.0.1:0")
    calls: list[tuple[str, object]] = []
    setpoint = f"{_demo.WRITABLE_LD}/GGIO1.SetPt1.setVal"
    server.on_write(setpoint, lambda p, v: calls.append((p, v)) or True)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 7)
        finally:
            await conn.disconnect()
    assert calls == [(setpoint, 7)]


# ── set_edition ─────────────────────────────────────────────────────────────

GOCB_GO_ID = f"{_demo.DOMAIN}/LLN0.gcbStatus.GoID"


async def _write_go_id(edition: str | None) -> tuple[bool, str]:
    """Write the GoID of the model's GoCB under `edition`; return whether the
    write was accepted and the GoID read back."""
    server = _demo_server()
    if edition is not None:
        server.set_edition(edition)
    server.register_from_model()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            try:
                await conn.write_visible_string(GOCB_GO_ID, "GO", "renamed")
                accepted = True
            except IedDataAccessError:
                accepted = False
            return accepted, await conn.read_string(GOCB_GO_ID, "GO")
        finally:
            await conn.disconnect()


async def test_go_id_is_writable_under_the_default_edition() -> None:
    assert await _write_go_id(None) == (True, "renamed")


async def test_go_id_is_writable_under_ed1_and_ed2() -> None:
    assert await _write_go_id("ed1") == (True, "renamed")
    assert await _write_go_id("ed2") == (True, "renamed")


async def test_go_id_is_read_only_under_ed2_1() -> None:
    accepted, go_id = await _write_go_id("ed2.1")
    assert accepted is False
    assert go_id != "renamed"


async def test_set_edition_refuses_an_unknown_edition_and_a_started_server() -> None:
    server = _demo_server()
    with pytest.raises(ValueError, match="not an edition"):
        server.set_edition("ed3")
    async with server:
        with pytest.raises(RuntimeError, match="set_edition: cannot reconfigure"):
            server.set_edition("ed2")


# ── setting group values ────────────────────────────────────────────────────

SG_SCL = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
            <SettingControl numOfSGs="3" actSG="1"/>
          </LN0>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1">
            <DOI name="SetPt1">
              <DAI name="setVal">
                <Val sGroup="1">10</Val>
                <Val sGroup="2">20</Val>
                <Val sGroup="3">30</Val>
              </DAI>
            </DOI>
          </LN>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="ENC_1"/>
    </LNodeType>
    <LNodeType id="GGIO_1" lnClass="GGIO">
      <DO name="Ind1" type="SPS_1"/>
      <DO name="SetPt1" type="ING_1"/>
    </LNodeType>
    <DOType id="ENC_1" cdc="ENC">
      <DA name="stVal" fc="ST" bType="Enum" type="BehaviourKind"/>
    </DOType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN"/>
    </DOType>
    <DOType id="ING_1" cdc="ING">
      <DA name="setVal" fc="SG" bType="INT32"/>
    </DOType>
    <EnumType id="BehaviourKind">
      <EnumVal ord="1">on</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""

SG_SETTING = "GenericIO/GGIO1.SetPt1.setVal"
SG_SETTING_REF = "IED1GenericIO/GGIO1.SetPt1.setVal"


def _sg_server() -> IedServer:
    server = IedServer.from_scl_str(SG_SCL, ied_name="IED1")
    server.bind("127.0.0.1:0")
    return server


async def test_get_setting_group_value_reads_each_group() -> None:
    server = _sg_server()
    async with server:
        assert [server.get_setting_group_value(SG_SETTING, g) for g in (1, 2, 3)] == [
            10,
            20,
            30,
        ]


async def test_set_setting_group_value_of_the_active_group_reaches_clients() -> None:
    server = _sg_server()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            assert await conn.read_int32(SG_SETTING_REF, FC.SG) == 10
            server.set_setting_group_value(SG_SETTING, 1, 11)
            assert server.get_setting_group_value(SG_SETTING, 1) == 11
            assert await conn.read_int32(SG_SETTING_REF, FC.SG) == 11
        finally:
            await conn.disconnect()


async def test_set_setting_group_value_of_an_inactive_group_waits_for_activation() -> None:
    server = _sg_server()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            server.set_setting_group_value(SG_SETTING, 2, {"type": "int", "value": 22})
            assert await conn.read_int32(SG_SETTING_REF, FC.SG) == 10
            server.force_active_setting_group("GenericIO", 2)
            assert await conn.read_int32(SG_SETTING_REF, FC.SG) == 22
        finally:
            await conn.disconnect()


async def test_setting_group_value_refusals() -> None:
    server = _sg_server()
    with pytest.raises(RuntimeError, match="not running"):
        server.get_setting_group_value(SG_SETTING, 1)
    async with server:
        for group in (0, 4):
            with pytest.raises(ValueError, match=r"outside 1\.\.=3"):
                server.get_setting_group_value(SG_SETTING, group)
            with pytest.raises(ValueError, match=r"outside 1\.\.=3"):
                server.set_setting_group_value(SG_SETTING, group, 1)
        with pytest.raises(KeyError, match="not an FC=SG or FC=SE setting"):
            server.get_setting_group_value("GenericIO/GGIO1.Ind1.stVal", 1)
        with pytest.raises(KeyError, match="not an FC=SG or FC=SE setting"):
            server.set_setting_group_value("GenericIO/GGIO1.Ind1.stVal", 1, True)
        with pytest.raises(KeyError, match="not found"):
            server.get_setting_group_value("GenericIO/GGIO1.Nope.setVal", 1)
        with pytest.raises(ValueError, match="must be an i64"):
            server.set_setting_group_value(SG_SETTING, 1, "eleven")
        with pytest.raises(IedDataAccessError, match="TypeInconsistent"):
            server.set_setting_group_value(SG_SETTING, 1, {"type": "float32", "value": 1.0})
        assert server.get_setting_group_value(SG_SETTING, 1) == 10


async def test_setting_group_value_of_an_ld_without_sgcb_raises() -> None:
    server = _array_server()
    async with server:
        with pytest.raises(KeyError, match="has no SGCB"):
            server.get_setting_group_value("GenericIO/GGIO1.Ind1.stVal", 1)


SG_EDIT_SG = "IED1GenericIO/LLN0.SGCB.EditSG"
SG_CNF_EDIT = "IED1GenericIO/LLN0.SGCB.CnfEdit"


async def _edit_and_confirm(
    on_confirm: object,
) -> tuple[IedDataAccessError | None, object, dict]:
    """Stage 222 in group 2 and confirm it with `on_confirm` installed.

    Returns the error the confirmation raised, if any, the value group 2
    holds afterwards, and the SGCB snapshot.
    """
    text = SG_SCL.replace('fc="SG" bType="INT32"', 'fc="SE" bType="INT32"')
    server = IedServer.from_scl_str(text, ied_name="IED1")
    server.bind("127.0.0.1:0")
    server.register_setting_group_handler("GenericIO", on_confirm=on_confirm)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.write(SG_EDIT_SG, FC.SP, {"type": "uint", "value": 2})
            await conn.write(SG_SETTING_REF, FC.SE, 222)
            error = None
            try:
                await conn.write(SG_CNF_EDIT, FC.SP, True)
            except IedDataAccessError as exc:
                error = exc
            return (
                error,
                server.get_setting_group_value(SG_SETTING, 2),
                server.get_setting_group_info("GenericIO"),
            )
        finally:
            await conn.disconnect()


@pytest.mark.parametrize("answer", [None, True])
async def test_on_confirm_accepting_commits_the_edit(answer: object) -> None:
    error, value, info = await _edit_and_confirm(lambda _sg, _conn: answer)
    assert error is None
    assert value == 222
    assert info["edit_sg"] == 0


@pytest.mark.parametrize("answer", [False, 1])
async def test_on_confirm_refusing_keeps_the_group(answer: object) -> None:
    error, value, info = await _edit_and_confirm(lambda _sg, _conn: answer)
    assert error is not None
    assert error.code == 3
    assert value == 20
    assert info["edit_sg"] == 2


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_on_confirm_raising_refuses_the_edit() -> None:
    def on_confirm(_sg: int, _conn: int) -> None:
        raise RuntimeError("storage unavailable")

    error, value, _info = await _edit_and_confirm(on_confirm)
    assert error is not None
    assert error.code == 3
    assert value == 20


# ── device model per connection ─────────────────────────────────────────────


async def test_each_connection_reads_the_model_of_its_own_server(
    demo_server: str, writable_server: str
) -> None:
    def ld_names(model: dict) -> list[str]:
        return [ld["name"] for ld in model["logical_devices"]]

    first = await IedConnection.connect(demo_server)
    try:
        assert ld_names(await first.get_device_model()) == [_demo.DOMAIN]
    finally:
        await first.disconnect()
    second = await IedConnection.connect(writable_server)
    try:
        assert ld_names(await second.get_device_model()) == [_demo.WRITABLE_DOMAIN]
    finally:
        await second.disconnect()


# ── write access policy ─────────────────────────────────────────────────────

CFG_REF = f"{_demo.WRITABLE_DOMAIN}/GGIO1.Cfg.setVal"


def _policy_server() -> IedServer:
    """The writable model plus an Int32 under FC=CF."""
    spec = _demo.writable_spec()
    spec["lds"][0]["lns"][1]["dos"].append(
        {"name": "Cfg", "das": [{"name": "setVal", "fc": "CF", "type": "Int32"}]}
    )
    server = IedServer.from_model_spec(spec)
    server.bind("127.0.0.1:0")
    return server


def test_write_access_defaults_and_refusals() -> None:
    server = _policy_server()
    assert server.write_access_allowed("SP") is True
    assert server.write_access_allowed("SV") is True
    assert server.write_access_allowed("SE") is True
    assert server.write_access_allowed("CF") is False
    assert server.write_access_allowed("ST") is False
    with pytest.raises(ValueError, match="invalid FC token"):
        server.write_access_allowed("XX")
    with pytest.raises(ValueError, match="never writable"):
        server.set_write_access_policy("ST", True)
    with pytest.raises(ValueError, match="invalid FC token"):
        server.set_write_access_policy("XX", True)


async def test_write_access_policy_set_before_start_applies() -> None:
    server = _policy_server()
    server.set_write_access_policy("CF", True)
    assert server.write_access_allowed("CF") is True
    async with server:
        assert server.write_access_allowed("CF") is True
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.write_int32(CFG_REF, FC.CF, 5)
            assert await conn.read_int32(CFG_REF, FC.CF) == 5
        finally:
            await conn.disconnect()


async def test_write_access_policy_refusing_sp_blocks_client_writes() -> None:
    server = _policy_server()
    server.set_write_access_policy("SP", False)
    async with server:
        assert server.write_access_allowed("SP") is False
        assert server.write_access_allowed("SV") is True
        conn = await IedConnection.connect(server.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 2)
            with pytest.raises(IedDataAccessError):
                await conn.write_int32(CFG_REF, FC.CF, 5)
            assert await conn.read_int32(_demo.WRITABLE_SETPOINT, FC.SP) == 0
        finally:
            await conn.disconnect()
    with pytest.raises(RuntimeError, match="stopped"):
        server.set_write_access_policy("SP", True)


async def test_write_access_policy_changes_apply_while_running() -> None:
    """Closing and reopening a constraint on a running server governs the
    writes that arrive afterwards, on an association opened before."""
    server = _policy_server()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 1)
            server.set_write_access_policy("SP", False)
            assert server.write_access_allowed("SP") is False
            with pytest.raises(IedDataAccessError) as info:
                await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 2)
            assert info.value.code == 3  # object-access-denied
            assert await conn.read_int32(_demo.WRITABLE_SETPOINT, FC.SP) == 1
            server.set_write_access_policy("SP", True)
            await conn.write_int32(_demo.WRITABLE_SETPOINT, FC.SP, 3)
            assert await conn.read_int32(_demo.WRITABLE_SETPOINT, FC.SP) == 3

            with pytest.raises(IedDataAccessError):
                await conn.write_int32(CFG_REF, FC.CF, 5)
            server.set_write_access_policy("CF", True)
            await conn.write_int32(CFG_REF, FC.CF, 5)
            assert await conn.read_int32(CFG_REF, FC.CF) == 5
        finally:
            await conn.disconnect()


# ── abort_all_connections / dynamic_dataset_count ───────────────────────────


async def _until(predicate, timeout_s: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_abort_all_connections_closes_every_association() -> None:
    server = _demo_server()
    async with server:
        assert server.abort_all_connections() == 0
        first = await IedConnection.connect(server.bound_addr)
        second = await IedConnection.connect(server.bound_addr)
        assert server.connection_count == 2
        assert server.abort_all_connections() == 2
        assert await _until(lambda: server.connection_count == 0)
        with pytest.raises(IedConnectionError) as lost:
            await first.read_bool(_demo.IND1, FC.ST)
        assert lost.value.reason == "connection-lost"
        assert first.is_connected is False
        with pytest.raises(IedConnectionError, match="not connected") as after:
            await first.read_bool(_demo.IND1, FC.ST)
        assert after.value.reason is None
        # The server keeps accepting new associations.
        third = await IedConnection.connect(server.bound_addr)
        try:
            await third.read_bool(_demo.IND1, FC.ST)
        finally:
            await third.disconnect()
        for conn in (first, second):
            try:
                await conn.abort()
            except IedError:
                pass


async def test_abort_connection_closes_only_the_named_association() -> None:
    server = _demo_server()
    readers: list[int] = []

    def handler(_path: str, ctx: dict) -> None:
        readers.append(ctx["conn_id"])

    server.on_read(_demo.SRV_IND1, handler, with_context=True)
    async with server:
        kept = await IedConnection.connect(server.bound_addr)
        kicked = await IedConnection.connect(server.bound_addr)
        try:
            await kept.read_bool(_demo.IND1, FC.ST)
            await kicked.read_bool(_demo.IND1, FC.ST)
            kept_id, kicked_id = readers
            assert kept_id != kicked_id

            assert server.abort_connection(kicked_id) is True
            assert await _until(lambda: server.connection_count == 1)
            with pytest.raises(IedConnectionError) as lost:
                await kicked.read_bool(_demo.IND1, FC.ST)
            assert lost.value.reason == "connection-lost"
            assert kicked.is_connected is False

            assert await kept.read_bool(_demo.IND1, FC.ST) is not None
            assert kept.is_connected is True
            assert readers[-1] == kept_id
            # The association is gone, so the same id now names nothing.
            assert server.abort_connection(kicked_id) is False
        finally:
            await kept.disconnect()
            await kicked.abort()


async def test_abort_connection_ignores_an_unknown_id() -> None:
    server = _demo_server()
    with pytest.raises(RuntimeError, match="not running"):
        server.abort_connection(1)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            assert server.abort_connection(2**63) is False
            assert await conn.read_bool(_demo.IND1, FC.ST) is not None
            assert server.connection_count == 1
        finally:
            await conn.disconnect()


async def test_dynamic_dataset_count_follows_client_data_sets() -> None:
    server = _demo_server()
    _demo.wire_control_blocks(server)
    ds_ref = f"{_demo.DOMAIN}/LLN0.ds_dyn"
    async with server:
        assert server.dynamic_dataset_count() == 0
        conn = await IedConnection.connect(server.bound_addr)
        try:
            await conn.create_data_set(
                ds_ref, [DataSetMember(object_ref=_demo.IND1, fc=FC.ST)]
            )
            assert server.dynamic_dataset_count() == 1
            assert await conn.delete_data_set(ds_ref) is True
            assert server.dynamic_dataset_count() == 0
        finally:
            await conn.disconnect()


def test_abort_and_dynamic_count_require_a_running_server() -> None:
    server = _demo_server()
    with pytest.raises(RuntimeError, match="not running"):
        server.abort_all_connections()
    with pytest.raises(RuntimeError, match="not running"):
        server.dynamic_dataset_count()


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(
            lambda cid: cid.replace(
                'datSet="dsStatus"\n                        confRev="1" type="GOOSE"',
                'datSet="dsMissing"\n                        confRev="1" type="GOOSE"',
            ),
            id="GOOSE",
        ),
        pytest.param(
            lambda cid: cid.replace(
                "          </LN0>",
                '            <LogControl name="lcbMissing" datSet="dsMissing"'
                ' logName="GeneralLog"/>\n          </LN0>',
                1,
            ),
            id="log",
        ),
    ],
)
def test_scl_naming_a_missing_data_set_is_refused(edit) -> None:
    """A GOOSE or log control block whose datSet names no data set of its
    logical node is refused when the SCL is read."""
    cid = _demo.DEMO_CID.read_text(encoding="utf-8")
    text = edit(cid)
    assert text != cid
    with pytest.raises(SclError) as info:
        IedServer.from_scl_str(text, ied_name=_demo.IED_NAME)
    assert info.value.kind == "UnresolvedDataSetReference"


def _spec_with_lcb(dataset_ref: str) -> dict:
    """A model whose LLN0 declares one data set and an LCB on `dataset_ref`."""
    return {
        "ied_name": "LgIED",
        "lds": [
            {
                "inst": "LD0",
                "lns": [
                    {
                        "lln0": True,
                        "dos": [
                            {
                                "name": "Mod",
                                "das": [
                                    {"name": "stVal", "fc": "ST", "type": "Int32"},
                                ],
                            },
                        ],
                        "datasets": [
                            {
                                "name": "ds1",
                                "entries": [
                                    {"ln_name": "LLN0", "fc": "ST", "do_path": ["Mod"]},
                                ],
                            },
                        ],
                        "rcbs": [{"name": "rpt", "dataset_ref": "LLN0$ds1"}],
                        "lcbs": [
                            {
                                "name": "lcb1",
                                "dataset_ref": dataset_ref,
                                "log_ref": "LgIEDLD0/LLN0$GeneralLog",
                            },
                        ],
                    },
                ],
            },
        ],
    }


async def test_register_from_model_with_a_missing_data_set_registers_nothing() -> None:
    """A model built in code whose LCB names a data set the server does not
    hold fails the whole call before anything is registered; the same model
    naming an existing data set registers."""
    broken = IedServer.from_model_spec(_spec_with_lcb("LLN0$dsMissing"))
    broken.bind("127.0.0.1:0")
    async with broken:
        with pytest.raises(IedServerError, match="log control block"):
            broken.register_from_model()
        conn = await IedConnection.connect(broken.bound_addr)
        try:
            assert (
                await conn.get_logical_node_directory("LgIEDLD0/LLN0", AcsiClass.REPORT)
                == []
            )
        finally:
            await conn.disconnect()

    sound = IedServer.from_model_spec(_spec_with_lcb("LLN0$ds1"))
    sound.bind("127.0.0.1:0")
    async with sound:
        summary = sound.register_from_model()
        assert summary is not None and summary["lcbs"] == 1


# ── SCL Communication addressing ────────────────────────────────────────────


def test_communication_addressing_reads_from_the_model() -> None:
    server = _demo_server()
    [ap] = server.connected_access_point("AP1")
    assert ap["ap_name"] == "AP1"
    assert ap["address"]["ip"] == "127.0.0.1"
    assert ap["address"]["parameters"]["MMS-Port"] == "102"
    assert ap["address"]["parameters"]["OSI-PSEL"] == "00000001"
    assert server.connected_access_point("NoSuchAP") == []

    gse = server.gse_address("LD0", "gcbStatus")
    assert gse is not None
    assert gse["mac_address"] == b"\x01\x0c\xcd\x01\x00\x01"
    assert gse["app_id"] == 0x1000
    assert gse["vlan_id"] == 0
    assert gse["vlan_priority"] == 4
    assert gse["min_time_ms"] == 10
    assert gse["max_time_ms"] == 1000
    assert gse["parameters"]["APPID"] == "1000"
    assert server.gse_address("LD0", "noSuchGoCB") is None
    assert server.smv_address("LD0", "noSuchSvCB") is None


def test_a_connected_ap_of_an_ied_the_file_lacks_is_skipped() -> None:
    """A <ConnectedAP> naming an IED the file does not declare does not stop
    the SCL from loading; the loaded IED keeps its own addressing."""
    cid = _demo.DEMO_CID.read_text(encoding="utf-8")
    extra = (
        '      <ConnectedAP iedName="NoSuchIED" apName="AP1">\n'
        '        <Address><P type="IP">10.0.0.9</P></Address>\n'
        "      </ConnectedAP>\n"
    )
    anchor = "    </SubNetwork>"
    assert cid.count(anchor) == 1
    server = IedServer.from_scl_str(
        cid.replace(anchor, extra + anchor), ied_name=_demo.IED_NAME
    )
    [ap] = server.connected_access_point("AP1")
    assert ap["address"]["ip"] == "127.0.0.1"
