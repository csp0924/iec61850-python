"""Facade-level mapping of a data set directory, without a server.

`get_data_set_directory` turns the native member dicts into `DataSetMember`.
The demonstration model carries no array-typed FCDA and the server refuses a
dynamic data set member that names an array element, so `array_index` and
`component` cannot be produced over the wire; a stand-in for the native
connection is what makes that half of the mapping observable.
"""

from __future__ import annotations

from typing import Any

import iec61850

DS_REF = "IED1LD0/LLN0.dsArr"

MEMBER_DICTS: list[dict[str, Any]] = [
    {
        "object_ref": "IED1LD0/GGIO1.Ind1",
        "fc": "ST",
        "array_index": 2,
        "component": "stVal",
    },
    {
        "object_ref": "IED1LD0/MMXU1.TotW.mag.f",
        "fc": "MX",
        "array_index": None,
        "component": None,
    },
    {
        "object_ref": "IED1LD0/GGIO1.Ind3",
        "fc": "ST",
        "array_index": 0,
        "component": None,
    },
    {
        "object_ref": "IED1LD0/MMXU1.PhV",
        "fc": "MX",
        "array_index": 1,
        "component": "cVal.mag.f",
    },
]

EXPECTED_MEMBERS = [
    iec61850.DataSetMember("IED1LD0/GGIO1.Ind1", iec61850.FC.ST, 2, "stVal"),
    iec61850.DataSetMember("IED1LD0/MMXU1.TotW.mag.f", iec61850.FC.MX, None, None),
    iec61850.DataSetMember("IED1LD0/GGIO1.Ind3", iec61850.FC.ST, 0, None),
    iec61850.DataSetMember("IED1LD0/MMXU1.PhV", iec61850.FC.MX, 1, "cVal.mag.f"),
]


class FakeNativeConnection:
    """Stands in for ``_native.IedConnection`` in the directory call only.

    Records the reference it was handed and answers the fixed payload.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[str] = []

    async def get_data_set_directory(self, reference: str) -> dict[str, Any]:
        self.calls.append(reference)
        return self._payload


def connection(payload: dict[str, Any]) -> iec61850.IedConnection:
    """An `IedConnection` whose native half is the stand-in.

    `IedConnection.__init__` refuses direct construction, so the instance is
    allocated and its one slot filled directly.
    """
    conn = object.__new__(iec61850.IedConnection)
    conn._native_conn = FakeNativeConnection(payload)  # type: ignore[assignment]
    return conn


async def test_every_member_field_is_carried_across() -> None:
    """Reference, FC, array index and component all reach the dataclass."""
    conn = connection({"deletable": True, "members": MEMBER_DICTS})
    directory = await conn.get_data_set_directory(DS_REF)
    assert directory.members == EXPECTED_MEMBERS


async def test_member_order_is_preserved() -> None:
    """The list keeps the order the server sent, member for member."""
    conn = connection(
        {"deletable": False, "members": list(reversed(MEMBER_DICTS))}
    )
    directory = await conn.get_data_set_directory(DS_REF)
    assert directory.members == list(reversed(EXPECTED_MEMBERS))


async def test_index_zero_is_not_confused_with_no_index() -> None:
    """`array_index=0` addresses the first element; `None` addresses none."""
    conn = connection({"deletable": False, "members": MEMBER_DICTS})
    directory = await conn.get_data_set_directory(DS_REF)
    assert directory.members[2].array_index == 0
    assert directory.members[1].array_index is None


async def test_deletable_flag_is_carried_across() -> None:
    for flag in (True, False):
        conn = connection({"deletable": flag, "members": MEMBER_DICTS})
        directory = await conn.get_data_set_directory(DS_REF)
        assert directory.deletable is flag


async def test_reference_reaches_the_native_call_unchanged() -> None:
    conn = connection({"deletable": False, "members": MEMBER_DICTS})
    directory = await conn.get_data_set_directory(DS_REF)
    assert conn._native_conn.calls == [DS_REF]  # type: ignore[attr-defined]
    assert len(directory.members) == len(MEMBER_DICTS)
