"""Server API end to end: each case starts a server hosted by this package and
drives it from the in-package client over TCP."""

from __future__ import annotations

import asyncio
import queue

import pytest

import _demo
from iec61850 import (
    FC,
    ControlModel,
    IedConnection,
    IedDataAccessError,
    IedServer,
    IedServerError,
    RcbWriteMask,
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
