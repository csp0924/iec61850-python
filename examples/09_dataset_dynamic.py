"""Creating and deleting a data set on the fly.

Custom snapshots without re-engineering the SCL. The context manager keeps
ownership obvious: the data set only exists for the duration of the `with`.

    python 09_dataset_dynamic.py

Expected stdout:

    created DemoIEDLD0/LLN0.ds_snapshot with 3 members
    duplicate create rejected: IedServiceError
    data set gone - second delete returned False
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import iec61850
from _shared import DEMO, spawn_demo


@asynccontextmanager
async def temporary_dataset(
    conn: iec61850.IedConnection,
    ref: str,
    members: list[iec61850.DataSetMember],
) -> AsyncIterator[str]:
    await conn.create_data_set(ref, members)
    try:
        yield ref
    finally:
        await conn.delete_data_set(ref)


async def main() -> None:
    async with spawn_demo() as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            members = [
                iec61850.DataSetMember(object_ref=DEMO.ind1, fc=iec61850.FC.ST),
                iec61850.DataSetMember(object_ref=DEMO.ind2, fc=iec61850.FC.ST),
                iec61850.DataSetMember(object_ref=DEMO.power, fc=iec61850.FC.MX),
            ]
            ref = f"{DEMO.domain}/LLN0.ds_snapshot"

            async with temporary_dataset(conn, ref, members) as live:
                print(f"created {live} with {len(members)} members")

                try:
                    await conn.create_data_set(live, members)
                except iec61850.IedServiceError as exc:
                    print(f"duplicate create rejected: {type(exc).__name__}")

            # Outside the `with`, the data set has been removed.
            deleted_again = await conn.delete_data_set(ref)
            assert deleted_again is False
            print("data set gone - second delete returned False")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
