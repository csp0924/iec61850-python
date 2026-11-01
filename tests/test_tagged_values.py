"""Every tag of the tagged value dict, through `from_model_spec` and `write`.

The server answers a Write whose value has a different MMS type than the
attribute with ObjectValueInvalid, so a tag converted to the wrong MMS type
fails the write instead of reading back as the same Python value.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from iec61850 import FC, IedConnection, IedServer, TimeQuality

IED = "TagIED"
LD = "Bay"
DOMAIN = f"{IED}{LD}"

UTC = bytes([0x65, 0x53, 0xF1, 0x00, 0x80, 0x00, 0x00, 0x0A])
ENTRY_TIME = bytes([0x01, 0x02, 0x03, 0x04, 0x37, 0x8A])

# (attribute name, attribute type, tagged value, value read back)
CASES: list[tuple[str, str, dict[str, Any], Any]] = [
    ("b", "Boolean", {"type": "bool", "value": True}, True),
    ("i", "Int32", {"type": "int", "value": -5}, -5),
    ("u", "Int32U", {"type": "uint", "value": 7}, 7),
    ("f", "Float32", {"type": "float32", "value": 1.5}, 1.5),
    ("d", "Float64", {"type": "float64", "value": 2.25}, 2.25),
    ("vs", "VisibleString", {"type": "visible_string", "value": "abc"}, "abc"),
    ("ms", "UnicodeString255", {"type": "mms_string", "value": "50 Ω"}, "50 Ω"),
    ("os", "OctetString", {"type": "octet_string", "value": b"\x01\x02"}, b"\x01\x02"),
    ("ut", "Timestamp", {"type": "utc_time", "value": UTC}, UTC),
    ("bt", "EntryTime", {"type": "binary_time", "value": ENTRY_TIME}, ENTRY_TIME),
    (
        "bs",
        "CodedEnum",
        {"type": "bit_string", "padding": 6, "data": b"\x80"},
        b"\x80",
    ),
]
IDS = [case[2]["type"] for case in CASES]


def _spec(values: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    das = []
    for name, da_type, _, _ in CASES:
        da: dict[str, Any] = {"name": name, "fc": "SP", "type": da_type}
        if values and name in values:
            da["value"] = values[name]
        das.append(da)
    return {
        "ied_name": IED,
        "lds": [
            {
                "inst": LD,
                "lns": [
                    {"lln0": True, "dos": []},
                    {
                        "class": "GGIO",
                        "inst": "1",
                        "dos": [{"name": "Val", "das": das}],
                    },
                ],
            },
        ],
    }


@asynccontextmanager
async def _session(spec: dict[str, Any]) -> AsyncIterator[IedConnection]:
    server = IedServer.from_model_spec(spec)
    server.bind("127.0.0.1:0")
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            yield conn
        finally:
            await conn.disconnect()


def _ref(name: str) -> str:
    return f"{DOMAIN}/GGIO1.Val.{name}"


@pytest.mark.parametrize(("name", "da_type", "tagged", "expected"), CASES, ids=IDS)
async def test_from_model_spec_tagged_initial_value(
    name: str, da_type: str, tagged: dict[str, Any], expected: Any
) -> None:
    async with _session(_spec({name: tagged})) as conn:
        assert await conn.read(_ref(name), FC.SP) == expected


@pytest.mark.parametrize(("name", "da_type", "tagged", "expected"), CASES, ids=IDS)
async def test_write_tagged_value_has_the_attribute_type(
    name: str, da_type: str, tagged: dict[str, Any], expected: Any
) -> None:
    async with _session(_spec()) as conn:
        await conn.write(_ref(name), FC.SP, tagged)
        assert await conn.read(_ref(name), FC.SP) == expected


async def test_from_model_spec_default_is_the_zero_of_the_type() -> None:
    default = {"type": "default"}
    spec = _spec({"i": default, "vs": default, "bs": default})
    async with _session(spec) as conn:
        assert await conn.read(_ref("i"), FC.SP) == 0
        assert await conn.read(_ref("vs"), FC.SP) == ""
        assert await conn.read_bit_string(_ref("bs"), FC.SP) == (b"\x00", 6)


@pytest.mark.parametrize(
    ("name", "tagged", "message"),
    [
        (
            "ut",
            {"type": "utc_time", "value": UTC[:7]},
            r"\['value'\] for utc_time must be exactly 8 bytes \(got 7\)",
        ),
        (
            "bt",
            {"type": "binary_time", "value": ENTRY_TIME[:5]},
            r"\['value'\] for binary_time must be 4 or 6 bytes \(got 5\)",
        ),
    ],
    ids=["utc_time", "binary_time"],
)
async def test_write_time_of_the_wrong_length_raises(
    name: str, tagged: dict[str, Any], message: str
) -> None:
    async with _session(_spec()) as conn:
        with pytest.raises(ValueError, match=message):
            await conn.write(_ref(name), FC.SP, tagged)


@pytest.mark.parametrize(
    "quality",
    [
        TimeQuality(),
        TimeQuality(leap_second_known=True, time_accuracy=10),
        TimeQuality(clock_failure=True, clock_not_synchronized=True, time_accuracy=31),
    ],
)
async def test_read_timestamp_with_quality_returns_the_time_quality(
    quality: TimeQuality,
) -> None:
    """A UtcTime the server holds reads back with its TimeQuality octet."""
    stamp = UTC[:7] + bytes([quality.to_octet()])
    async with _session(_spec({"ut": {"type": "utc_time", "value": stamp}})) as conn:
        when, read_quality = await conn.read_timestamp_with_quality(_ref("ut"), FC.SP)
        assert read_quality == quality
        assert when == datetime.fromtimestamp(0x6553F100, tz=timezone.utc) + timedelta(
            microseconds=500_000
        )
        assert await conn.read_timestamp(_ref("ut"), FC.SP) == when
