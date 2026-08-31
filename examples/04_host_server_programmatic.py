"""Building a server model in Python — no SCL file involved.

Handy for test fixtures and fuzzers. `IedServer.from_model_spec` accepts a
nested dict mirroring IEC 61850-6 (LDs -> LNs -> DOs -> DAs); the helpers in
this module compose that dict piece by piece so the intent reads top-down.

The model declares a setpoint under FC=SP, which is one of the three
constraints a server accepts a remote Write on by default, so this recipe can
also show the write path end to end. `models/demo.cid` has no writable
attribute, which is why the write demonstration lives here rather than in
`01_typed_io.py`.

    python 04_host_server_programmatic.py

Expected stdout:

    ProgIED listening on 127.0.0.1:54321
    client wrote Bay/GGIO1.SetPt1.setVal <- 42
    setpoint read back: 42
    breaker after update: closed
"""

from __future__ import annotations

import asyncio
from typing import Any

import iec61850

IED = "ProgIED"
LD = "Bay"
DOMAIN = f"{IED}{LD}"


def da(name: str, fc: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "fc": fc, "type": kind, **extra}


def status_do(name: str) -> dict[str, Any]:
    """A Boolean-valued DO with quality and timestamp under FC=ST."""
    return {
        "name": name,
        "das": [
            da("stVal", "ST", "Boolean", trg_ops=["data_changed"]),
            da("q", "ST", "Quality"),
            da("t", "ST", "Timestamp"),
        ],
    }


def measurement_do(name: str) -> dict[str, Any]:
    """A Float32 measurement DO under FC=MX (CDC MV)."""
    return {
        "name": name,
        "constructed_das": [
            {
                "name": "mag",
                "fc": "MX",
                "children": [da("f", "MX", "Float32", trg_ops=["data_changed"])],
            },
        ],
        "das": [da("q", "MX", "Quality"), da("t", "MX", "Timestamp")],
    }


def setpoint_do(name: str) -> dict[str, Any]:
    """An Int32 setpoint under FC=SP (CDC ING), writable by a remote client."""
    return {"name": name, "das": [da("setVal", "SP", "Int32")]}


def build_spec() -> dict[str, Any]:
    return {
        "ied_name": IED,
        "lds": [
            {
                "inst": LD,
                "lns": [
                    {
                        "lln0": True,
                        "dos": [
                            {
                                "name": "Mod",
                                "das": [
                                    da(
                                        "stVal",
                                        "ST",
                                        "Enumerated",
                                        value={"type": "int", "value": 1},
                                    ),
                                    da("q", "ST", "Quality"),
                                    da("t", "ST", "Timestamp"),
                                ],
                            },
                        ],
                    },
                    {
                        "class": "GGIO",
                        "inst": "1",
                        "dos": [
                            status_do("Ind1"),
                            setpoint_do("SetPt1"),
                        ],
                    },
                    {
                        "class": "MMXU",
                        "inst": "1",
                        "dos": [measurement_do("TotW"), measurement_do("Hz")],
                    },
                ],
            },
        ],
    }


async def main() -> None:
    server = iec61850.IedServer.from_model_spec(build_spec())
    server.bind("127.0.0.1:0")

    def echo_setpoint(path: str, value: Any) -> bool:
        print(f"client wrote {path} <- {value!r}")
        return True  # accept and let the server update its cache

    server.on_write(f"{LD}/GGIO1.SetPt1.setVal", echo_setpoint)

    async with server:
        print(f"{IED} listening on {server.bound_addr}")
        server.update_bool(f"{LD}/GGIO1.Ind1.stVal", False)
        server.update_float32(f"{LD}/MMXU1.TotW.mag.f", 200.0)

        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            await conn.write_int32(
                f"{DOMAIN}/GGIO1.SetPt1.setVal", iec61850.FC.SP, 42
            )
            readback = await conn.read_int32(
                f"{DOMAIN}/GGIO1.SetPt1.setVal", iec61850.FC.SP
            )
            print(f"setpoint read back: {readback}")

            server.update_bool(f"{LD}/GGIO1.Ind1.stVal", True)
            closed = await conn.read_bool(
                f"{DOMAIN}/GGIO1.Ind1.stVal", iec61850.FC.ST
            )
            print(f"breaker after update: {'closed' if closed else 'open'}")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
