"""Asking a data set what it holds, then fetching it in one round trip.

`get_data_set_directory` answers the member list of a data set — reference,
Functional Constraint, and whether the server lets it be deleted. Feeding that
list into `read_multiple` fetches every member with a single MMS Read, so a
poll costs one round trip instead of one per point. `read_multiple` is not
limited to data set members: any set of references can be batched.

    python 14_dataset_directory_batch_read.py

Expected stdout:

    dsMeas: 3 members, deletable=False
      MX DemoIEDLD0/MMXU1.TotW.mag.f
      MX DemoIEDLD0/MMXU1.Hz.mag.f
      MX DemoIEDLD0/MMXU1.PhV.phsA.cVal.mag.f
    batch read of all 3 members in one request:
      DemoIEDLD0/MMXU1.TotW.mag.f = 230.5
      DemoIEDLD0/MMXU1.Hz.mag.f = 50.0
      DemoIEDLD0/MMXU1.PhV.phsA.cVal.mag.f = 11000.0
    one absent target marks only its own slot:
      DemoIEDLD0/GGIO1.Ind1.stVal = True
      DemoIEDLD0/GGIO1.NoSuchDO.stVal unavailable: ObjectNonExistent
      DemoIEDLD0/MMXU1.Hz.mag.f = 50.0
    strict read_multiple raised IedDataAccessError
    ds_clone: 4 members, deletable=True
    clone deleted
"""

from __future__ import annotations

import asyncio

import iec61850
from _shared import DEMO, DS_MEAS, DS_STATUS, spawn_demo

# A data object the demonstration model does not carry, so the server answers
# an access failure for it while the other targets of the same Read succeed.
ABSENT = f"{DEMO.domain}/GGIO1.NoSuchDO.stVal"


def show(targets: list[tuple[str, iec61850.FC]], values: list[object]) -> None:
    """Print one line per target, values and failure markers alike."""
    for (reference, _fc), value in zip(targets, values, strict=True):
        if isinstance(value, iec61850.DataAccessFailure):
            print(f"  {reference} unavailable: {value.error}")
        else:
            print(f"  {reference} = {value}")


async def main() -> None:
    async with spawn_demo() as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            ds_meas = f"{DEMO.domain}/{DS_MEAS}"
            directory = await conn.get_data_set_directory(ds_meas)
            print(
                f"dsMeas: {len(directory.members)} members, "
                f"deletable={directory.deletable}"
            )
            for member in directory.members:
                print(f"  {member.fc.value} {member.object_ref}")

            targets = [(m.object_ref, m.fc) for m in directory.members]
            print(f"batch read of all {len(targets)} members in one request:")
            show(targets, await conn.read_multiple(targets))

            # `strict=False` keeps the successful values and marks the rest.
            mixed = [
                (DEMO.ind1, iec61850.FC.ST),
                (ABSENT, iec61850.FC.ST),
                (DEMO.freq, iec61850.FC.MX),
            ]
            print("one absent target marks only its own slot:")
            show(mixed, await conn.read_multiple(mixed, strict=False))

            # The default refuses the whole batch instead.
            try:
                await conn.read_multiple(mixed)
            except iec61850.IedDataAccessError as exc:
                print(f"strict read_multiple raised {type(exc).__name__}")

            # A member list is in the form `create_data_set` takes, so a
            # directory read clones a set of leaf attributes. `deletable`
            # tells the two kinds apart: the SCL-declared source is false,
            # the clone created here is true.
            source = await conn.get_data_set_directory(f"{DEMO.domain}/{DS_STATUS}")
            clone_ref = f"{DEMO.domain}/LLN0.ds_clone"
            await conn.create_data_set(clone_ref, source.members)
            clone = await conn.get_data_set_directory(clone_ref)
            print(
                f"ds_clone: {len(clone.members)} members, "
                f"deletable={clone.deletable}"
            )
            assert clone.members == source.members
            assert await conn.delete_data_set(clone_ref) is True
            print("clone deleted")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
