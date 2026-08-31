"""Discovering what an IED exposes, without prior knowledge of its SCL.

Useful when you connect to an unfamiliar IED and need to enumerate logical
devices, logical nodes, and their data objects before deciding what to read.
Point it at any IEC 61850 server; against the cookbook IED start
`03_host_server_from_scl.py` first.

    python 02_browse_data_model.py 127.0.0.1:PORT

Expected stdout:

    1 logical device(s): DemoIEDLD0

    -- DemoIEDLD0 --------------------------------
      GGIO1   SPCSO1, Ind1, Ind2, Ind3, Ind4
        SPCSO1: Cancel, Oper, SBO, SBOw, ctlModel, ctlNum, origin, q, stVal, t
      LLN0    NamPlt, Beh, Health, Mod
        NamPlt: configRev, d, ldNs, swRev, vendor
      LPHD1   PhyNam, PhyHealth, Proxy
        PhyNam: hwRev, model, serNum, swRev, vendor
      MMXU1   Hz, PhV, TotW
        Hz: mag, q, t
"""

from __future__ import annotations

import asyncio
import sys

import iec61850


async def describe_logical_node(
    conn: iec61850.IedConnection, ln_ref: str
) -> tuple[list[str], dict[str, list[str]]]:
    """Return the DO names of `ln_ref` and the members of its first DO.

    `get_logical_node_directory` takes an ACSI class, not a functional
    constraint: `DATA_OBJECT` lists data objects, `DATASET` / `REPORT` /
    `BUFFERED_REPORT` / `LOG` / `GOOSE` list the control blocks of that kind.
    """
    dos = await conn.get_logical_node_directory(ln_ref, iec61850.AcsiClass.DATA_OBJECT)
    members: dict[str, list[str]] = {}
    if dos:
        first = dos[0]
        members[first] = await conn.get_data_directory(f"{ln_ref}.{first}")
    return dos, members


async def main(address: str) -> None:
    conn = await iec61850.IedConnection.connect(address)
    try:
        lds = await conn.get_server_directory()
        print(f"{len(lds)} logical device(s): {', '.join(lds)}\n")

        for ld in lds:
            print(f"-- {ld} --------------------------------")
            for ln in sorted(await conn.get_logical_device_directory(ld)):
                dos, members = await describe_logical_node(conn, f"{ld}/{ln}")
                print(f"  {ln:7s} {', '.join(dos)}")
                for do, sub in members.items():
                    print(f"    {do}: {', '.join(sorted(sub))}")
            print()
    finally:
        await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:102"))
