"""Subscribing to the unbuffered report control block declared in the CID.

The flow is the same against any IEC 61850 server: read the RCB, set
`resv` + `rpt_ena` in one round-trip (Edition 2.1 rejects enable without
reservation), then install a callback. `poll_reports` drives dispatch
from the current task; for fire-and-forget delivery, see the buffered
example which uses a background `ReportDispatcher`.

When you run the in-process demo (no `remote` argument), a second task pokes
the measurands so `urcbMeas` has something to fire on. Against a real IED
just omit that — the IED is already driving its own data.

    python 07_reporting_unbuffered.py            # hosts its own IED
    python 07_reporting_unbuffered.py host:port  # against a running IED

Expected stdout:

    local DemoIED on 127.0.0.1:54321
    subscribed to DemoIEDLD0/LLN0$RP$urcbMeas; collecting reports for 3s
      seq=0 entries=2
        DemoIEDLD0/MMXU1$MX$TotW$mag$f  <-  231.5  (dchg)
        DemoIEDLD0/MMXU1$MX$Hz$mag$f  <-  49.97999954223633  (dchg)
"""

from __future__ import annotations

import asyncio
import sys

import iec61850
from _shared import DEMO, URCB_MEAS, spawn_demo


def reason_of(entry: iec61850.ReportEntry) -> str:
    """Compact rendering of the inclusion bitmap of one report entry."""
    flags = [
        name
        for name, on in (
            ("dchg", entry.reason.data_change),
            ("qchg", entry.reason.quality_change),
            ("dupd", entry.reason.data_update),
            ("intg", entry.reason.integrity),
            ("gi", entry.reason.general_interrogation),
        )
        if on
    ]
    return f"({','.join(flags) or 'none'})"


async def consume_reports(address: str, *, deadline_s: float = 5.0) -> None:
    conn = await iec61850.IedConnection.connect(address)
    try:
        rcb_ref = DEMO.urcb(URCB_MEAS)
        rcb = await conn.get_rcb_values(rcb_ref)
        rcb.resv = True
        rcb.rpt_ena = True
        await conn.set_rcb_values(rcb, iec61850.RcbWriteMask.fields("resv", "rpt_ena"))

        inbox: asyncio.Queue[iec61850.ClientReport] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        await conn.install_report_handler(
            rcb_ref,
            lambda r: loop.call_soon_threadsafe(inbox.put_nowait, r),
            rpt_id=rcb_ref,
        )
        print(f"subscribed to {rcb_ref}; collecting reports for {deadline_s:.0f}s")

        async def pump() -> None:
            while True:
                await conn.poll_reports(200)

        pump_task = asyncio.create_task(pump())
        try:
            end = loop.time() + deadline_s
            while loop.time() < end:
                try:
                    report = await asyncio.wait_for(
                        inbox.get(), timeout=end - loop.time()
                    )
                except asyncio.TimeoutError:
                    break
                included = [e for e in report.entries if e.ref is not None]
                print(f"  seq={report.sequence_number} entries={len(included)}")
                for entry in included:
                    print(f"    {entry.ref}  <-  {entry.value!r}  {reason_of(entry)}")
        finally:
            pump_task.cancel()
    finally:
        await conn.disconnect()


async def drive_model(server: iec61850.IedServer, *, period_s: float = 0.4) -> None:
    """Walk the measurands every `period_s` so the URCB has data to fire on."""
    power = 230.0
    freq = 50.0
    while True:
        await asyncio.sleep(period_s)
        power += 1.5
        freq = 50.0 if freq != 50.0 else 49.98
        server.update_float32(DEMO.srv_power, power)
        server.update_float32(DEMO.srv_freq, freq)


async def main(remote: str | None) -> None:
    if remote:
        await consume_reports(remote)
        return
    async with spawn_demo() as server:
        print(f"local DemoIED on {server.bound_addr}")
        driver = asyncio.create_task(drive_model(server))
        try:
            await consume_reports(server.bound_addr, deadline_s=3.0)
        finally:
            driver.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
