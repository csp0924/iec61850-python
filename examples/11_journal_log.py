"""Pulling journal entries back from a Log Control Block.

Unlike reports, journals are pull-based — they sit in a ring buffer on the
IED until a client asks for them. Useful for historian backfill and
sequence-of-events applications. The example also exercises the overflow
path so you can see the oldest entries getting evicted.

`models/demo.cid` declares no LCB, so the recipe registers one at runtime
over the CID's status data set.

    python 11_journal_log.py

Expected stdout:

    8 entries  more=False
      12:53:20.000  DemoIEDLD0/GGIO1$ST$Ind1$stVal  <-  True
      12:53:20.100  DemoIEDLD0/GGIO1$ST$Ind1$stVal  <-  False
      12:53:20.200  DemoIEDLD0/GGIO1$ST$Ind1$stVal  <-  True
      ...(5 more)

    after overflow: 10 entries  earliest=12:53:20.300  latest=12:53:21.200
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from itertools import cycle

import iec61850
from _shared import DEMO, DS_STATUS, spawn_demo

LCB_NAME = "lcb01"
BASE_MS = 1_715_000_000_000  # stable fixture timestamp
LOGGED_PATH = f"{DEMO.domain}/GGIO1$ST$Ind1$stVal"


def configure_log_control(server: iec61850.IedServer) -> None:
    server.register_log_control(
        f"{DEMO.ld}/LLN0.{LCB_NAME}",
        dataset=DS_STATUS,
        log_ref=f"{DEMO.domain}/LLN0$GeneralLog",
        trg_ops=["data_changed"],
        storage_capacity=10,
    )


def push_events(server: iec61850.IedServer, count: int, *, t0: int) -> None:
    """Append `count` synthetic events with stable timestamps."""
    values = cycle((True, False))
    for offset, value in zip(range(count), values, strict=False):
        server.log_value(
            f"{DEMO.ld}/LLN0.{LCB_NAME}",
            data_ref=LOGGED_PATH,
            value=value,
            time_ms=t0 + offset * 100,
            reason_code=0x02,
        )


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[
        :-3
    ]


async def main() -> None:
    async with spawn_demo(configure=configure_log_control) as server:
        push_events(server, 8, t0=BASE_MS)

        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            lcb_ref = DEMO.lcb(LCB_NAME)
            entries, more = await conn.query_journal_by_time(
                lcb_ref, BASE_MS, BASE_MS + 1_000
            )
            print(f"{len(entries)} entries  more={more}")
            for entry in entries[:3]:
                v = entry.variables[0]
                print(f"  {fmt_ts(entry.time_ms)}  {v.data_ref}  <-  {v.value}")
            if len(entries) > 3:
                print(f"  ...({len(entries) - 3} more)")

            # Overflow: push another 5 so the oldest fall off the ring.
            push_events(server, 5, t0=BASE_MS + 800)
            entries, _ = await conn.query_journal_by_time(
                lcb_ref, BASE_MS, BASE_MS + 1_000_000
            )
            print(
                f"\nafter overflow: {len(entries)} entries  "
                f"earliest={fmt_ts(entries[0].time_ms)}  "
                f"latest={fmt_ts(entries[-1].time_ms)}"
            )
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
