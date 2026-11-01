"""BIT STRING values: tagged dicts from `on_read` and `write`, the typed
bit-string read and write on the client, and the bit-string updates on the
server.

The model carries a double-point status, whose `stVal` is the two-bit
`Dbpos`, and a `Dbpos` under FC=SP so a remote Write is accepted. `Dbpos`
packs its two bits into the top of one octet: the on state, `10`, is the octet
`0x80` with six unused trailing bits.
"""

from __future__ import annotations

import asyncio
import queue
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from iec61850 import (
    FC,
    ClientReport,
    IedConnection,
    IedDataAccessError,
    IedServer,
    Quality,
    QualityDetail,
    RcbWriteMask,
    Validity,
)

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
    pos_da: dict[str, Any] = {
        "name": "stVal",
        "fc": "ST",
        "type": "CodedEnum",
        "trg_ops": ["data_changed"],
    }
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
                                    {
                                        "name": "q",
                                        "fc": "ST",
                                        "type": "Quality",
                                        "trg_ops": ["quality_changed"],
                                    },
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


async def test_tagged_bit_string_clears_the_unused_bits() -> None:
    tagged = {"type": "bit_string", "padding": 6, "data": b"\xff"}
    async with _session({SRV_POS: lambda _p: tagged}) as (_, conn):
        await conn.write(SETPOINT, FC.SP, tagged)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\xc0", 6)
        assert await conn.read_bit_string(POS, FC.ST) == (b"\xc0", 6)


@pytest.mark.parametrize(
    "tagged",
    [
        {"type": "bit_string", "padding": 0, "data": b""},
        {"type": "bit_string", "padding": 5, "data": b"\xe0"},
        {"type": "bit_string", "padding": 3, "data": b"\x80\x00"},
    ],
    ids=["empty", "three-bit", "thirteen-bit"],
)
async def test_bit_string_of_the_wrong_length_is_refused(
    tagged: dict[str, Any],
) -> None:
    """A Dbpos is exactly two bits, on a Write and from on_read alike."""
    async with _session({SRV_POS: lambda _p: tagged}) as (_, conn):
        await conn.write(SETPOINT, FC.SP, DBPOS_ON)
        with pytest.raises(IedDataAccessError):
            await conn.write(SETPOINT, FC.SP, tagged)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\x80", 6)
        with pytest.raises(IedDataAccessError):
            await conn.read_bit_string(POS, FC.ST)


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


async def test_remote_write_leaves_the_unused_bits_of_a_bit_string_clear() -> None:
    seen: list[object] = []

    def writer(_path: str, value: object) -> bool:
        seen.append(value)
        return True

    async with _session() as (server, conn):
        server.on_write(SRV_SETPOINT, writer)
        await conn.write_bit_string(SETPOINT, FC.SP, b"\xff", 6)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\xc0", 6)
        assert await conn.read(SETPOINT, FC.SP) == b"\xc0"
    assert seen == [b"\xc0"]


async def test_write_bit_string_sends_unused_bits_as_zero() -> None:
    async with _session() as (_, conn):
        await conn.write_bit_string(SETPOINT, FC.SP, b"\xff", 6)
        assert await conn.read_bit_string(SETPOINT, FC.SP) == (b"\xc0", 6)


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


# ── server-side bit-string updates ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "data"), [(0, b"\x00"), (1, b"\x40"), (2, b"\x80"), (3, b"\xc0")]
)
async def test_update_bit_string_from_int_dbpos(value: int, data: bytes) -> None:
    async with _session() as (server, conn):
        server.update_bit_string_from_int(SRV_POS, value)
        assert await conn.read_bit_string(POS, FC.ST) == (data, 6)


async def test_update_bit_string_from_int_quality() -> None:
    # Bit 1 of the thirteen, validity "invalid" (01), carries 2**11.
    async with _session() as (server, conn):
        server.update_bit_string_from_int(SRV_POS_Q, 1 << 11)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x40\x00", 3)
        quality = await conn.read_quality(POS_Q, FC.ST)
        assert quality.validity is Validity.INVALID


async def test_update_bit_string_quality_bytes() -> None:
    async with _session() as (server, conn):
        server.update_bit_string(SRV_POS_Q, b"\x60\x00", 3)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x60\x00", 3)
        quality = await conn.read_quality(POS_Q, FC.ST)
        assert quality.validity is Validity.INVALID
        assert quality.detail.overflow is True


QUALITIES = [
    Quality(),
    Quality(validity=Validity.INVALID),
    Quality(validity=Validity.RESERVED),
    Quality(validity=Validity.QUESTIONABLE, detail=QualityDetail(old_data=True)),
    Quality(detail=QualityDetail(overflow=True, inaccurate=True)),
    Quality(source_substituted=True),
    Quality(test=True),
    Quality(operator_blocked=True),
    Quality(
        validity=Validity.QUESTIONABLE,
        detail=QualityDetail(
            overflow=True,
            out_of_range=True,
            bad_reference=True,
            oscillatory=True,
            failure=True,
            old_data=True,
            inconsistent=True,
            inaccurate=True,
        ),
        source_substituted=True,
        test=True,
        operator_blocked=True,
    ),
]


@pytest.mark.parametrize("quality", QUALITIES)
async def test_update_quality_reads_back_through_read_quality(quality: Quality) -> None:
    async with _session() as (server, conn):
        server.update_quality(SRV_POS_Q, quality)
        assert await conn.read_quality(POS_Q, FC.ST) == quality
        server.update_quality(SRV_POS_Q, Quality())
        server.update_quality(SRV_POS_Q, quality.to_bits())
        assert await conn.read_quality(POS_Q, FC.ST) == quality


async def test_update_quality_takes_the_read_quality_bit_order() -> None:
    """0x0002 is validity invalid and 0x0800 test, as `read_quality` reads
    them; on the wire the first bit of the string is bit 0."""
    async with _session() as (server, conn):
        server.update_quality(SRV_POS_Q, 0x0002)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x40\x00", 3)
        assert await conn.read_quality(POS_Q, FC.ST) == Quality(validity=Validity.INVALID)
        server.update_quality(SRV_POS_Q, 0x0800)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x00\x10", 3)
        assert await conn.read_quality(POS_Q, FC.ST) == Quality(test=True)
        with pytest.raises(ValueError, match="quality bits"):
            server.update_quality(SRV_POS_Q, 1 << 13)
        with pytest.raises(IedDataAccessError):
            server.update_quality(SRV_POS, Quality())


@pytest.mark.parametrize(
    ("short_data", "short_padding"),
    [(b"\x80", 7), (b"\x40", 6)],
    ids=["one-bit", "two-bit"],
)
async def test_quality_written_short_takes_thirteen_bits_again(
    short_data: bytes, short_padding: int
) -> None:
    """A Quality holds one to thirteen bits whatever length it holds now."""
    async with _session() as (server, conn):
        server.update_bit_string(SRV_POS_Q, short_data, short_padding)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (short_data, short_padding)
        server.update_bit_string_from_int(SRV_POS_Q, 1 << 11)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x40\x00", 3)

        server.update_bit_string(SRV_POS_Q, short_data, short_padding)
        server.update_bit_string(SRV_POS_Q, b"\x60\x00", 3)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x60\x00", 3)

        with pytest.raises(IedDataAccessError):
            server.update_bit_string(SRV_POS_Q, b"\x60\x00", 2)
        with pytest.raises(IedDataAccessError):
            server.update_bit_string_from_int(SRV_POS_Q, 1 << 13)
        assert await conn.read_bit_string(POS_Q, FC.ST) == (b"\x60\x00", 3)


async def test_update_bit_string_stores_unused_bits_as_zero() -> None:
    async with _session() as (server, conn):
        server.update_bit_string(SRV_POS, b"\xff", 6)
        assert await conn.read_bit_string(POS, FC.ST) == (b"\xc0", 6)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (lambda s: s.update_bit_string(SRV_POS, b"\xe0", 5), "type mismatch"),
        (lambda s: s.update_bit_string(SRV_POS, b"\x80\x00", 0), "type mismatch"),
        (lambda s: s.update_bit_string(SRV_COUNT, b"\x80", 6), "type mismatch"),
        (lambda s: s.update_bit_string_from_int(SRV_POS, 4), "type mismatch"),
        (lambda s: s.update_bit_string_from_int(SRV_COUNT, 1), "type mismatch"),
    ],
    ids=["three-bit-dbpos", "sixteen-bit-dbpos", "int32", "dbpos-4", "int32-int"],
)
async def test_update_bit_string_mismatch_leaves_the_value(
    update: Any, message: str
) -> None:
    async with _session() as (server, conn):
        server.update_bit_string_from_int(SRV_POS, 1)
        server.update_int32(SRV_COUNT, 9)
        with pytest.raises(IedDataAccessError, match=message):
            update(server)
        assert await conn.read_bit_string(POS, FC.ST) == (b"\x40", 6)
        assert await conn.read_int32(COUNT, FC.ST) == 9


@pytest.mark.parametrize(
    ("data", "padding", "message"),
    [
        (b"\x80", 8, "update_bit_string: padding must be an int in 0..=7"),
        (b"\x80", -1, "update_bit_string: padding must be an int in 0..=7"),
        (b"", 2, "update_bit_string: padding must be 0 when the data is empty"),
    ],
)
async def test_update_bit_string_invalid_padding_raises(
    data: bytes, padding: int, message: str
) -> None:
    async with _session() as (server, conn):
        server.update_bit_string_from_int(SRV_POS, 1)
        with pytest.raises(ValueError, match=message):
            server.update_bit_string(SRV_POS, data, padding)
        assert await conn.read_bit_string(POS, FC.ST) == (b"\x40", 6)


def test_update_bit_string_before_start_raises() -> None:
    server = IedServer.from_model_spec(_spec())
    with pytest.raises(RuntimeError, match="update_bit_string: server is not running"):
        server.update_bit_string(SRV_POS, b"\x80", 6)
    with pytest.raises(KeyError, match="update_bit_string_from_int"):
        server.update_bit_string_from_int(f"{LD}/XCBR1.Pos.nope", 2)


URCB = f"{DOMAIN}/LLN0$RP$urcbPos"
MMS_POS = f"{DOMAIN}/XCBR1$ST$Pos$stVal"
MMS_POS_Q = f"{DOMAIN}/XCBR1$ST$Pos$q"


async def _next_report(
    conn: IedConnection, sink: queue.Queue[ClientReport]
) -> ClientReport:
    """Poll until one report arrives; fail after four seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 4.0
    while loop.time() < deadline:
        await conn.poll_reports(200)
        try:
            return sink.get_nowait()
        except queue.Empty:
            continue
    raise AssertionError("no report arrived")


async def test_bit_string_updates_trigger_reports() -> None:
    """`stVal` reports a data change and `q` a quality change."""
    server = IedServer.from_model_spec(_spec())
    server.bind("127.0.0.1:0")
    server.add_dataset("XCBR1$dsPos", [SRV_POS, SRV_POS_Q])
    server.register_urcb(
        f"{LD}/LLN0.urcbPos",
        dataset="XCBR1$dsPos",
        trg_ops=["data_changed", "quality_changed"],
        opt_flds=["seq_num", "reason", "data_set", "data_reference"],
        buf_tm_ms=10,
    )
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(rcb, RcbWriteMask.fields("resv", "rpt_ena"))
            sink: queue.Queue[ClientReport] = queue.Queue()
            await conn.install_report_handler(URCB, sink.put, rpt_id=URCB)

            server.update_bit_string_from_int(SRV_POS, 2)
            report = await _next_report(conn, sink)
            [entry] = [e for e in report.entries if e.ref == MMS_POS]
            assert entry.value == b"\x80"
            assert entry.reason is not None
            assert entry.reason.data_change and not entry.reason.quality_change

            server.update_bit_string(SRV_POS_Q, b"\x60\x00", 3)
            report = await _next_report(conn, sink)
            [entry] = [e for e in report.entries if e.ref == MMS_POS_Q]
            assert entry.value == b"\x60\x00"
            assert entry.reason is not None
            assert entry.reason.quality_change and not entry.reason.data_change
        finally:
            await conn.disconnect()
