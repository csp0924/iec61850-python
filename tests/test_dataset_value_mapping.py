"""Facade-level mapping of a data set read result, without a server.

Under ``strict=False`` a failing entry reaches Python as the native marker
dict, and the facade is what turns it into a `DataAccessFailure`. The
demonstration server refuses a CreateDataSet naming a member it does not
carry, so a data set whose read fails per entry cannot be built over the wire;
a stand-in for the native connection is what makes the conversion observable.
"""

from __future__ import annotations

from typing import Any

import iec61850

DS_REF = "IED1LD0/LLN0.dsStatus"

# What the native layer puts in a failing position under `strict=False`.
FAILURE_MARKER: dict[str, Any] = {"error": "ObjectNonExistent", "code": 10}


class FakeNativeConnection:
    """Stands in for ``_native.IedConnection`` in the data set read only.

    Records the arguments it was handed and answers the fixed payload.
    """

    def __init__(self, payload: list[Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, bool]] = []

    async def get_data_set_values(self, reference: str, strict: bool) -> list[Any]:
        self.calls.append((reference, strict))
        return list(self._payload)


def connection(payload: list[Any]) -> iec61850.IedConnection:
    """An `IedConnection` whose native half is the stand-in.

    `IedConnection.__init__` refuses direct construction, so the instance is
    allocated and its one slot filled directly.
    """
    conn = object.__new__(iec61850.IedConnection)
    conn._native_conn = FakeNativeConnection(payload)  # type: ignore[assignment]
    return conn


async def test_a_failing_entry_surfaces_as_a_dataclass() -> None:
    """The marker becomes `DataAccessFailure`; the other values stand."""
    conn = connection([1.0, FAILURE_MARKER, True])
    values = await conn.get_data_set_values(DS_REF, strict=False)

    assert len(values) == 3
    assert values[0] == 1.0
    assert values[2] is True
    failure = values[1]
    assert isinstance(failure, iec61850.DataAccessFailure)
    assert failure.code == 10
    assert failure.error == "ObjectNonExistent"


async def test_an_all_success_result_is_passed_through() -> None:
    """Nothing but a marker is rewritten, whichever mode is asked for."""
    conn = connection([1.0, True, "on"])
    assert await conn.get_data_set_values(DS_REF, strict=False) == [1.0, True, "on"]
    assert await conn.get_data_set_values(DS_REF) == [1.0, True, "on"]


async def test_reference_and_strictness_reach_the_native_call() -> None:
    """Strictness is decided below the facade, which never raises on its own."""
    conn = connection([1.0])
    await conn.get_data_set_values(DS_REF, strict=False)
    await conn.get_data_set_values(DS_REF)
    assert conn._native_conn.calls == [  # type: ignore[attr-defined]
        (DS_REF, False),
        (DS_REF, True),
    ]
