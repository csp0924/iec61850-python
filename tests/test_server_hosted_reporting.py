"""End-to-end fire path of a server hosted from Python.

One `update_*` call has to travel the whole way: URCB trigger, engine tick,
InformationReport on the wire, client callback. `test_server.py` only proves
that `update_*` does not raise; the cases here assert that a report actually
reaches a subscribed client.
"""

from __future__ import annotations

import asyncio
import queue

from iec61850 import FC, IedConnection, IedServer, RcbWriteMask

# Minimal SCL: LLN0 plus GGIO1 carrying a Boolean status point, a Float32
# measurand, and an Int32 setpoint. An attribute only reports the triggers it
# declares, so the status and measurand attributes carry dchg / qchg.
SERVER_SCL = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0"/>
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
      <DO name="AnIn1" type="MV_1"/>
      <DO name="SetPt1" type="ING_1"/>
    </LNodeType>
    <DOType id="ENC_1" cdc="ENC">
      <DA name="stVal" fc="ST" bType="Enum" type="BehaviourKind"/>
      <DA name="q" fc="ST" bType="Quality"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN" dchg="true"/>
      <DA name="q" fc="ST" bType="Quality" qchg="true"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <DOType id="MV_1" cdc="MV">
      <DA name="mag" fc="MX" bType="Struct" type="AnalogueValue" dchg="true"/>
      <DA name="q" fc="MX" bType="Quality" qchg="true"/>
      <DA name="t" fc="MX" bType="Timestamp"/>
    </DOType>
    <DOType id="ING_1" cdc="ING">
      <DA name="setVal" fc="SP" bType="INT32"/>
    </DOType>
    <DAType id="AnalogueValue">
      <BDA name="f" bType="FLOAT32"/>
    </DAType>
    <EnumType id="BehaviourKind">
      <EnumVal ord="1">on</EnumVal>
      <EnumVal ord="2">blocked</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""

URCB_MMS = "IED1GenericIO/LLN0$RP$urcb01"
BRCB_MMS = "IED1GenericIO/LLN0$BR$brcb01"
IND1_SERVER = "GenericIO/GGIO1.Ind1.stVal"
ANIN1_SERVER = "GenericIO/GGIO1.AnIn1.mag.f"
IND1_MMS = "IED1GenericIO/GGIO1$ST$Ind1$stVal"
ANIN1_MMS = "IED1GenericIO/GGIO1$MX$AnIn1$mag$f"
BRCB_REF = "IED1GenericIO/LLN0.brcb01"


async def _wait_for_report(
    conn: IedConnection,
    sink: queue.Queue,
    *,
    timeout_s: float = 4.0,
):
    """Poll until one ClientReport lands or `timeout_s` elapses."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        await conn.poll_reports(200)
        try:
            return sink.get_nowait()
        except queue.Empty:
            continue
    return None


async def test_python_hosted_urcb_fires_on_update_bool() -> None:
    """A Boolean change must reach a subscribed client as one report.

    The sequence is reserve and enable the RCB, install the handler, mutate
    the value, then poll. A missed trigger hook, or a data set whose entries
    do not share the model's attribute allocations, leaves `attr_ref_index`
    unable to resolve the changed attribute and this test times out.
    """
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER, ANIN1_SERVER])
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set"],
        buf_tm_ms=10,
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MMS)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(
                rcb, RcbWriteMask.fields("resv", "rpt_ena")
            )

            # Read the block back: the write has to be visible on the wire.
            rcb_after = await conn.get_rcb_values(URCB_MMS)
            assert rcb_after.rpt_ena is True, (
                f"rpt_ena should be True after set_rcb_values, got "
                f"{rcb_after.rpt_ena}"
            )
            assert rcb_after.resv is True, (
                f"resv should be True after set_rcb_values, got {rcb_after.resv}"
            )

            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(
                URCB_MMS, lambda r: sink.put(r), rpt_id=URCB_MMS
            )

            # Mutate after the handler is installed. Server tick loop (1ms)
            # should pick up the trigger and emit an InformationReport.
            s.update_bool(IND1_SERVER, True)

            report = await _wait_for_report(conn, sink)
            assert report is not None, (
                "no URCB report arrived within the window after update_bool"
            )
            assert report.rcb_reference == URCB_MMS
            assert len(report.entries) >= 1
        finally:
            try:
                await conn.uninstall_report_handler(URCB_MMS)
            except Exception:
                pass
            await conn.disconnect()


async def test_python_hosted_urcb_fires_on_update_float32() -> None:
    """Same path driven by a Float32 change rather than a Boolean one."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER, ANIN1_SERVER])
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set"],
        buf_tm_ms=10,
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MMS)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(
                rcb, RcbWriteMask.fields("resv", "rpt_ena")
            )
            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(
                URCB_MMS, lambda r: sink.put(r), rpt_id=URCB_MMS
            )

            s.update_float32(ANIN1_SERVER, 12.5)

            report = await _wait_for_report(conn, sink)
            assert report is not None, "no URCB report arrived after update_float32"
            assert report.rcb_reference == URCB_MMS
        finally:
            try:
                await conn.uninstall_report_handler(URCB_MMS)
            except Exception:
                pass
            await conn.disconnect()


async def test_report_entry_of_an_omitted_member_carries_no_reason() -> None:
    """A member a report leaves out carries no inclusion reason.

    The first report carries `Ind1`, the second only `AnIn1`; in the second the
    `Ind1` entry keeps its previous value but must not look as if it had
    triggered the report.
    """
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER, ANIN1_SERVER])
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed"],
        opt_flds=["seq_num", "reason", "data_set", "data_reference"],
        buf_tm_ms=10,
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MMS)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(
                rcb, RcbWriteMask.fields("resv", "rpt_ena")
            )
            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(
                URCB_MMS, lambda r: sink.put(r), rpt_id=URCB_MMS
            )

            s.update_bool(IND1_SERVER, True)
            first = await _wait_for_report(conn, sink)
            assert first is not None, "no report arrived after update_bool"
            [ind1] = [e for e in first.entries if e.ref == IND1_MMS]
            assert ind1.reason is not None and ind1.reason.data_change

            s.update_float32(ANIN1_SERVER, 12.5)
            second = await _wait_for_report(conn, sink)
            assert second is not None, "no report arrived after update_float32"
            [ind1] = [e for e in second.entries if e.ref == IND1_MMS]
            [anin1] = [e for e in second.entries if e.ref == ANIN1_MMS]
            assert anin1.reason is not None and anin1.reason.data_change
            assert ind1.reason is not None
            assert not any(
                (
                    ind1.reason.data_change,
                    ind1.reason.quality_change,
                    ind1.reason.data_update,
                    ind1.reason.integrity,
                    ind1.reason.general_interrogation,
                )
            )
        finally:
            try:
                await conn.uninstall_report_handler(URCB_MMS)
            except Exception:
                pass
            await conn.disconnect()


async def test_brcb_enabled_by_attribute_writes_delivers_a_report() -> None:
    """A BRCB enabled one attribute at a time reports to the enabling client.

    `RptEna` is written on its own, as a client that does not use
    `set_rcb_values` does; the report goes to the association that wrote it.
    No RptID is passed, so the server defaults it to the MMS path.
    """
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER, ANIN1_SERVER])
    s.register_brcb(
        "GenericIO/LLN0.brcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set"],
        buf_tm_ms=10,
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write_bool(BRCB_REF + ".RptEna", FC.BR, True)
            assert await conn.read_bool(BRCB_REF + ".RptEna", FC.BR) is True

            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(
                BRCB_MMS, lambda r: sink.put(r), rpt_id=BRCB_MMS
            )

            s.update_bool(IND1_SERVER, True)

            report = await _wait_for_report(conn, sink)
            assert report is not None, "no BRCB report arrived after update_bool"
            assert report.rcb_reference == BRCB_MMS
        finally:
            try:
                await conn.uninstall_report_handler(BRCB_MMS)
            except Exception:
                pass
            await conn.disconnect()


async def test_urcb_sends_an_integrity_report_every_intg_pd() -> None:
    """An enabled RCB with integrity in its TrgOps reports without any change."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER, ANIN1_SERVER])
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["integrity"],
        opt_flds=["seq_num", "reason", "data_set", "data_reference"],
        buf_tm_ms=10,
        intg_pd_ms=200,
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MMS)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(
                rcb, RcbWriteMask.fields("resv", "rpt_ena")
            )
            sink: queue.Queue = queue.Queue()
            await conn.install_report_handler(
                URCB_MMS, lambda r: sink.put(r), rpt_id=URCB_MMS
            )

            reports = []
            for _ in range(2):
                report = await _wait_for_report(conn, sink, timeout_s=6.0)
                assert report is not None, "no integrity report arrived"
                reports.append(report)

            for report in reports:
                assert report.rcb_reference == URCB_MMS
                assert {e.ref for e in report.entries} == {IND1_MMS, ANIN1_MMS}
                assert all(
                    e.reason is not None and e.reason.integrity
                    for e in report.entries
                )
            assert reports[1].sequence_number == reports[0].sequence_number + 1
        finally:
            try:
                await conn.uninstall_report_handler(URCB_MMS)
            except Exception:
                pass
            await conn.disconnect()
