"""End-to-end tests for the inclusion bitstring and the segmentation of reports.

A report too large for the PDU size the association negotiated is split by
the server into segments. Connecting with a small `local_max_pdu_size` to a
server whose data set has many members makes a General Interrogation report
arrive in several segments.
"""

from __future__ import annotations

import asyncio
import queue

import _demo
import iec61850

MEMBERS = 24
SMALL_PDU = 400
IED = "IED1"
LD = "GenericIO"
URCB_MMS = f"{IED}{LD}/LLN0$RP$urcb01"


def _scl(members: int) -> str:
    dos = "\n".join(f'      <DO name="Ind{i}" type="SPS_1"/>' for i in range(1, members + 1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="{IED}" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="{LD}">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0"/>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1"/>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="SPS_1"/>
    </LNodeType>
    <LNodeType id="GGIO_1" lnClass="GGIO">
{dos}
    </LNodeType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN" dchg="true"/>
    </DOType>
  </DataTypeTemplates>
</SCL>
"""


def _many_member_server() -> iec61850.IedServer:
    server = iec61850.IedServer.from_scl_str(_scl(MEMBERS), ied_name=IED)
    server.bind("127.0.0.1:0")
    server.add_dataset(
        "LLN0$dsMany", [f"{LD}/GGIO1.Ind{i}.stVal" for i in range(1, MEMBERS + 1)]
    )
    server.register_urcb(
        f"{LD}/LLN0.urcb01",
        dataset="LLN0$dsMany",
        rpt_id=URCB_MMS,
        trg_ops=["data_changed", "gi"],
        opt_flds=["seq_num", "reason", "data_set", "data_reference"],
        buf_tm_ms=10,
    )
    return server


async def _enable(conn: iec61850.IedConnection, rcb_ref: str) -> None:
    rcb = await conn.get_rcb_values(rcb_ref)
    rcb.resv = True
    rcb.rpt_ena = True
    await conn.set_rcb_values(rcb, iec61850.RcbWriteMask.fields("resv", "rpt_ena"))


async def _drain(
    conn: iec61850.IedConnection,
    sink: queue.Queue[iec61850.ClientReport],
    *,
    until_last_segment: bool,
    timeout_s: float = 4.0,
) -> list[iec61850.ClientReport]:
    """Poll reports until one arrives, or until a final segment arrives."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    got: list[iec61850.ClientReport] = []
    while loop.time() < deadline:
        await conn.poll_reports(100)
        while not sink.empty():
            got.append(sink.get_nowait())
        if got and (not until_last_segment or not got[-1].more_segments_follow):
            break
    return got


async def test_data_change_report_marks_only_changed_member() -> None:
    async with _demo.hosted_demo() as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            await _enable(conn, _demo.URCB_REF)
            sink: queue.Queue[iec61850.ClientReport] = queue.Queue()
            await conn.install_report_handler(
                _demo.URCB_REF, sink.put, rpt_id=_demo.URCB_REF
            )
            server.update_float32(_demo.SRV_POWER, 412.5)
            reports = await _drain(conn, sink, until_last_segment=False)
        finally:
            await conn.disconnect()
    assert reports, "no report arrived within the window"
    report = reports[0]
    # dsMeas holds TotW then Hz; only TotW changed.
    assert report.included == (True, False)
    assert report.entries[0].value == 412.5
    assert report.entries[0].reason is not None
    assert report.entries[0].reason.data_change is True
    assert report.entries[1].reason is not None
    assert report.entries[1].reason.data_change is False
    assert report.sub_seq_num is None
    assert report.more_segments_follow is False


async def test_gi_report_fits_one_pdu_unsegmented() -> None:
    server = _many_member_server()
    async with server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            await _enable(conn, URCB_MMS)
            sink: queue.Queue[iec61850.ClientReport] = queue.Queue()
            await conn.install_report_handler(URCB_MMS, sink.put, rpt_id=URCB_MMS)
            await conn.trigger_gi(URCB_MMS)
            reports = await _drain(conn, sink, until_last_segment=True)
        finally:
            await conn.disconnect()
    assert len(reports) == 1
    report = reports[0]
    assert report.included == (True,) * MEMBERS
    assert report.sub_seq_num is None
    assert report.more_segments_follow is False


async def test_gi_report_arrives_in_segments_at_small_pdu() -> None:
    server = _many_member_server()
    async with server:
        conn = await iec61850.IedConnection.connect(
            server.bound_addr, local_max_pdu_size=SMALL_PDU
        )
        try:
            await _enable(conn, URCB_MMS)
            sink: queue.Queue[iec61850.ClientReport] = queue.Queue()
            await conn.install_report_handler(URCB_MMS, sink.put, rpt_id=URCB_MMS)
            await conn.trigger_gi(URCB_MMS)
            segments = await _drain(conn, sink, until_last_segment=True)
        finally:
            await conn.disconnect()
    assert len(segments) > 1
    assert [s.sub_seq_num for s in segments] == list(range(len(segments)))
    assert [s.more_segments_follow for s in segments] == [True] * (
        len(segments) - 1
    ) + [False]
    assert len({s.sequence_number for s in segments}) == 1
    covered = [False] * MEMBERS
    for segment in segments:
        assert len(segment.included) == MEMBERS
        assert any(segment.included)
        for i, flag in enumerate(segment.included):
            if flag:
                assert not covered[i], f"member {i} in two segments"
                covered[i] = True
                assert segment.entries[i].reason is not None
                assert segment.entries[i].reason.general_interrogation is True
    assert all(covered)


def test_client_report_from_native_dict_reads_segmentation() -> None:
    report = iec61850.ClientReport.from_native_dict(
        {
            "rcb_reference": "LD/LLN0$RP$r",
            "rpt_id": "r",
            "values": [True, None, False],
            "reasons": [0x10, 0, 0x10],
            "data_references": [None, None, None],
            "included": [True, False, True],
            "sub_seq_num": 2,
            "more_segments_follow": True,
        }
    )
    assert report.included == (True, False, True)
    assert report.sub_seq_num == 2
    assert report.more_segments_follow is True


def test_client_report_from_native_dict_defaults_without_segmentation() -> None:
    report = iec61850.ClientReport.from_native_dict(
        {
            "rcb_reference": "LD/LLN0$RP$r",
            "rpt_id": "r",
            "values": [],
            "reasons": [],
            "data_references": [],
        }
    )
    assert report.included == ()
    assert report.sub_seq_num is None
    assert report.more_segments_follow is False
