"""BIT STRING values: tagged dicts from `on_read` and `write`, and the typed
bit-string read and write on the client.

The model carries a double-point status, whose `stVal` is the two-bit
`Dbpos`, and a `Dbpos` under FC=SP so a remote Write is accepted. `Dbpos`
packs its two bits into the top of one octet: the on state, `10`, is the octet
`0x80` with six unused trailing bits.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from iec61850 import FC, IedConnection, IedDataAccessError, IedServer, Validity

IED = "BitIED"
LD = "Bay"
DOMAIN = f"{IED}{LD}"

SRV_POS = f"{LD}/XCBR1.Pos.stVal"
SRV_POS_Q = f"{LD}/XCBR1.Pos.q"
SRV_SETPOINT = f"{LD}/XCBR1.PosSet.setVal"
SRV_COUNT = f"{LD}/XCBR1.OpCnt.stVal"

POS = f"{DOMAIN}/XCBR1.Pos.stVal"
POS_Q = f"{DOMAIN}/XCBR1.Pos.q"
SETPOINT = f"{DOMAIN}/XCBR1.PosSet.setVal"
COUNT = f"{DOMAIN}/XCBR1.OpCnt.stVal"

DBPOS_ON = {"type": "bit_string", "padding": 6, "data": b"\x80"}


def _spec(pos_value: dict[str, Any] | None = None) -> dict[str, Any]:
    pos_da: dict[str, Any] = {"name": "stVal", "fc": "ST", "type": "CodedEnum"}
    if pos_value is not None:
        pos_da["value"] = pos_value
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
                                "name": "Pos",
                                "das": [
                                    pos_da,
                                    {"name": "q", "fc": "ST", "type": "Quality"},
                                ],
                            },
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
async def _session(
    on_read: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[IedServer, IedConnection]]:
    """Host the model, register `on_read` callbacks, and connect a client."""
    server = IedServer.from_model_spec(spec or _spec())
    server.bind("127.0.0.1:0")
    for path, callback in (on_read or {}).items():
        server.on_read(path, callback)
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            yield server, conn
        finally:
            await conn.disconnect()


# ── on_read ─────────────────────────────────────────────────────────────────


async def test_on_read_tagged_bit_string_reaches_the_client() -> None:
    async with _session({SRV_POS: lambda _p: DBPOS_ON}) as (_, conn):
        assert await conn.read_bit_string(POS, FC.ST) == (b"\x80", 6)
        assert await conn.read(POS, FC.ST) == b"\x80"


async def test_on_read_tagged_quality_keeps_its_padding() -> None:
    # Quality is thirteen bits: validity "invalid" (01) and the overflow bit.
    tagged = {"type": "bit_string", "padding": 3, "data": b"\x60\x00"}
    async with _session({SRV_POS_Q: lambda _p: tagged}) as (_, conn):
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x60\x00", 3)
        quality = await conn.read_quality(POS_Q, FC.ST)
        assert quality.validity is Validity.INVALID
        assert quality.detail.overflow is True


async def test_on_read_accepts_other_tagged_values() -> None:
    tagged = {"type": "int", "value": 7}
    async with _session({SRV_COUNT: lambda _p: tagged}) as (_, conn):
        assert await conn.read_int32(COUNT, FC.ST) == 7


@pytest.mark.parametrize(
    ("tagged", "message"),
    [
        ({"padding": 6, "data": b"\x80"}, "is missing the 'type' key"),
        (
            {"type": "bit_string", "data": b"\x80"},
            "of type 'bit_string' is missing the 'padding' key",
        ),
        ({"type": "bit_string", "padding": 6}, "is missing the 'data' key"),
        (
            {"type": "bit_string", "padding": 8, "data": b"\x80"},
            r"\['padding'\] must be an int in 0..=7",
        ),
        (
            {"type": "bit_string", "padding": 6, "data": "80"},
            r"\['data'\] must be bytes",
        ),
        ({"type": "bits", "data": b"\x80"}, "has type 'bits', which is not supported"),
    ],
)
async def test_on_read_malformed_tagged_value_fails_the_read(
    capsys: pytest.CaptureFixture[str], tagged: dict[str, Any], message: str
) -> None:
    """The client sees a failed read; the reason is printed on the server."""
    async with _session({SRV_POS: lambda _p: tagged}) as (_, conn):
        with pytest.raises(IedDataAccessError):
            await conn.read_bit_string(POS, FC.ST)
    err = capsys.readouterr().err
    assert f"on_read value for '{SRV_POS}'" in err
    assert re.search(message, err), err


# ── write with a tagged value ───────────────────────────────────────────────


async def test_write_tagged_bit_string_round_trip() -> None:
    async with _session() as (_, conn):
        await conn.write(SETPOINT, FC.SP, DBPOS_ON)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\x80", 6)


@pytest.mark.parametrize(
    ("tagged", "message"),
    [
        ({"padding": 6, "data": b"\x80"}, "write value is missing the 'type' key"),
        (
            {"type": "bit_string", "padding": -1, "data": b"\x80"},
            r"write value\['padding'\] must be an int in 0..=7",
        ),
        (
            {"type": "bit_string", "padding": 3, "data": b""},
            r"write value\['padding'\] must be 0 when the data is empty",
        ),
        (
            {"type": "default"},
            "write value of type 'default' needs the attribute type",
        ),
        ({"type": "int", "value": "1"}, r"write value\['value'\] must be an i64"),
    ],
)
async def test_write_malformed_tagged_value_raises(
    tagged: dict[str, Any], message: str
) -> None:
    async with _session() as (_, conn):
        with pytest.raises(ValueError, match=message):
            await conn.write(SETPOINT, FC.SP, tagged)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\x00", 6)


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


# ── from_model_spec shares the tagged-value rules ──────────────────────────


async def test_from_model_spec_tagged_bit_string_sets_the_initial_value() -> None:
    async with _session(spec=_spec(DBPOS_ON)) as (_, conn):
        assert await conn.read_bit_string(POS, FC.ST) == (b"\x80", 6)


def test_from_model_spec_rejects_padding_past_seven() -> None:
    bad = {"type": "bit_string", "padding": 9, "data": b"\x80"}
    with pytest.raises(
        ValueError,
        match=r"from_model_spec: \S+\.value\['padding'\] must be an int in 0\.\.=7",
    ):
        IedServer.from_model_spec(_spec(bad))
