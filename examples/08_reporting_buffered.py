"""Buffered report control block with a background dispatcher.

`Iec61850Client(report_dispatcher_interval_ms=...)` pumps `poll_reports` on a
background task, so the application sees only the callback. Buffered RCBs
queue events server-side while the client is offline and replay them on
reconnect using `EntryID`, giving at-least-once delivery up to the buffer
size.

Run with an IED address (`python 08_reporting_buffered.py host:port`) for the
client half. Without arguments the script hosts the cookbook IED and shows
the server-side registration only: this server does not yet answer
GetBRCBValues, so the subscription step needs an IED that does.

    python 08_reporting_buffered.py            # server-side registration
    python 08_reporting_buffered.py host:port  # subscribe to a running IED

Expected stdout:

    local DemoIED on 127.0.0.1:54321
    BRCB registered at DemoIEDLD0/LLN0$BR$brcbMeas over data set LLN0$dsMeas
    point a client at this address, or pass one to subscribe.
"""

from __future__ import annotations

import asyncio
import sys

import iec61850
from _shared import BRCB_MEAS, DEMO, DS_MEAS, spawn_demo


async def consume(address: str, *, idle_s: float = 2.0) -> None:
    host, _, port = address.partition(":")
    config = iec61850.Iec61850ClientConfig(
        address=host,
        port=int(port),
        report_dispatcher_interval_ms=100,
    )
    seen: list[iec61850.ClientReport] = []

    def on_report(report: iec61850.ClientReport) -> None:
        seen.append(report)
        flag = "  BUFFER-OVERFLOW" if report.buffer_overflow else ""
        print(
            f"  seq={report.sequence_number} entries={len(report.entries)} "
            f"entry_id={report.entry_id!r}{flag}"
        )

    async with iec61850.Iec61850Client(config) as client:
        ref = DEMO.brcb(BRCB_MEAS)
        rcb = await client.connection.get_rcb_values(ref)
        rcb.rpt_ena = True
        await client.connection.set_rcb_values(
            rcb, iec61850.RcbWriteMask.fields("rpt_ena")
        )
        await client.connection.install_report_handler(ref, on_report, rpt_id=ref)
        await asyncio.sleep(idle_s)

    print(f"\ntotal reports: {len(seen)}")


async def main(remote: str | None) -> None:
    if remote:
        await consume(remote)
        return
    async with spawn_demo() as server:
        print(f"local DemoIED on {server.bound_addr}")
        print(f"BRCB registered at {DEMO.brcb(BRCB_MEAS)} over data set {DS_MEAS}")
        print("point a client at this address, or pass one to subscribe.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
