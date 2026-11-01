"""Buffered report control block with a background dispatcher.

`Iec61850Client(report_dispatcher_interval_ms=...)` pumps `poll_reports` on a
background task, so the application sees only the callback. Buffered RCBs
queue events server-side while the client is offline and replay them on
reconnect using `EntryID`, giving at-least-once delivery up to the buffer
size.

The first report after a BRCB goes from disabled to enabled carries
`BufOvfl` set, whichever `EntryID` the client resynchronises from; after that
it is set only when the buffer dropped entries that were never sent.

When you run the in-process demo (no `remote` argument), a second task pokes
the measurands so `brcbMeas` has something to fire on.

    python 08_reporting_buffered.py            # hosts its own IED
    python 08_reporting_buffered.py host:port  # against a running IED

Expected stdout (entry IDs vary):

    local DemoIED on 127.0.0.1:54321, data set LLN0$dsMeas
    subscribing to DemoIEDLD0/LLN0$BR$brcbMeas over data set DemoIEDLD0/LLN0$dsMeas
      seq=0 members=3 entry_id=000001a194fc5401  BUFFER-OVERFLOW
      seq=1 members=1 entry_id=000001a194fc5599
      seq=2 members=1 entry_id=000001a194fc572d
      seq=3 members=1 entry_id=000001a194fc58c2
      seq=4 members=1 entry_id=000001a194fc5a55

    total reports: 5
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
            f"  seq={report.sequence_number} members={sum(report.included)} "
            f"entry_id={report.entry_id.hex() if report.entry_id else None}{flag}"
        )

    async with iec61850.Iec61850Client(config) as client:
        ref = DEMO.brcb(BRCB_MEAS)
        rcb = await client.connection.get_rcb_values(ref)
        print(f"subscribing to {ref} over data set {rcb.dataset}")
        await client.connection.install_report_handler(ref, on_report, rpt_id=ref)
        rcb.rpt_ena = True
        await client.connection.set_rcb_values(
            rcb, iec61850.RcbWriteMask.fields("rpt_ena")
        )
        await asyncio.sleep(idle_s)

    print(f"\ntotal reports: {len(seen)}")


async def drive_model(server: iec61850.IedServer, *, period_s: float = 0.4) -> None:
    """Walk the active power every `period_s` so the BRCB has data to fire on."""
    power = 230.0
    while True:
        await asyncio.sleep(period_s)
        power += 1.5
        server.update_float32(DEMO.srv_power, power)


async def main(remote: str | None) -> None:
    if remote:
        await consume(remote)
        return
    async with spawn_demo() as server:
        print(f"local DemoIED on {server.bound_addr}, data set {DS_MEAS}")
        driver = asyncio.create_task(drive_model(server))
        try:
            await consume(server.bound_addr)
        finally:
            driver.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
