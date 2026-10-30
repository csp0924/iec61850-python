"""BIT STRING values through the typed bit-string read and write on the client.

The model carries a `Dbpos` under FC=SP so a remote Write is accepted. `Dbpos`
packs its two bits into the top of one octet: the on state, `10`, is the octet
`0x80` with six unused trailing bits.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from iec61850 import FC, IedConnection, IedDataAccessError, IedServer

IED = "BitIED"
LD = "Bay"
DOMAIN = f"{IED}{LD}"

SETPOINT = f"{DOMAIN}/XCBR1.PosSet.setVal"
COUNT = f"{DOMAIN}/XCBR1.OpCnt.stVal"


def _spec() -> dict[str, Any]:
    return {
        "ied_name": IED,
        "lds": [
            {
                "inst": LD,
                "lns": [
                    {"lln0": True, "dos": []},
                    {
                        "class": "XCBR",
                        "inst": "1",
                        "dos": [
                            {
                                "name": "PosSet",
                                "das": [
                                    {"name": "setVal", "fc": "SP", "type": "CodedEnum"},
                                ],
                            },
                            {
                                "name": "OpCnt",
                                "das": [
                                    {"name": "stVal", "fc": "ST", "type": "Int32"},
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }


@asynccontextmanager
async def _session() -> AsyncIterator[tuple[IedServer, IedConnection]]:
    """Host the model and connect a client."""
    server = IedServer.from_model_spec(_spec())
    server.bind("127.0.0.1:0")
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            yield server, conn
        finally:
            await conn.disconnect()


# ── typed bit-string read and write ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "padding"), [(b"\x00", 6), (b"\x40", 6), (b"\x80", 6), (b"\xc0", 6)]
)
async def test_write_bit_string_round_trip(data: bytes, padding: int) -> None:
    async with _session() as (_, conn):
        await conn.write_bit_string(SETPOINT, FC.SP, data, padding)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (data, padding)


@pytest.mark.parametrize(
    ("data", "padding", "message"),
    [
        (b"\x80", 8, "padding must be an int in 0..=7"),
        (b"\x80", -1, "padding must be an int in 0..=7"),
        (b"\x80", 256, "padding must be an int in 0..=7"),
        (b"", 1, "padding must be 0 when the data is empty"),
    ],
)
async def test_write_bit_string_invalid_padding_sends_nothing(
    data: bytes, padding: int, message: str
) -> None:
    async with _session() as (_, conn):
        await conn.write_bit_string(SETPOINT, FC.SP, b"\x40", 6)
        with pytest.raises(ValueError, match=message):
            await conn.write_bit_string(SETPOINT, FC.SP, data, padding)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\x40", 6)


async def test_read_bit_string_of_another_type_raises() -> None:
    async with _session() as (_, conn):
        with pytest.raises(IedDataAccessError, match="expected BitString, got Integer"):
            await conn.read_bit_string(COUNT, FC.ST)

