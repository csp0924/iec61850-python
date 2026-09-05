"""End-to-end tests for the batched read.

Exercises the whole chain: Python facade -> PyO3 -> iec61850-client -> one MMS
Read carrying several variables -> the server's read handler.

`ABSENT` names a data object the demo model does not carry, which is what
makes the server answer one AccessResult failure among the successes.
"""

from __future__ import annotations

import pytest

import _demo
import iec61850

ABSENT = f"{_demo.DOMAIN}/GGIO1.NoSuchDO.stVal"

TARGETS = [
    (_demo.IND1, iec61850.FC.ST),
    (_demo.IND2, iec61850.FC.ST),
    (_demo.POWER, iec61850.FC.MX),
]
VALUES = [True, False, 230.5]

# Wire code of the MMS DataAccessError `object-non-existent` (ISO 9506-2).
OBJECT_NON_EXISTENT_CODE = 10


async def test_read_multiple_returns_values_in_request_order(
    demo_server: str,
) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.read_multiple(TARGETS) == VALUES
    finally:
        await conn.disconnect()


async def test_read_multiple_follows_a_reversed_request(demo_server: str) -> None:
    """Reversing the targets reverses the results, one for one."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        assert await conn.read_multiple(list(reversed(TARGETS))) == list(
            reversed(VALUES)
        )
    finally:
        await conn.disconnect()


async def test_read_multiple_strict_raises_naming_the_failing_index(
    demo_server: str,
) -> None:
    """One absent target fails the whole call and names its position."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError) as excinfo:
            await conn.read_multiple([*TARGETS, (ABSENT, iec61850.FC.ST)])
        message = str(excinfo.value)
        assert "entry 3" in message
        assert ABSENT in message
    finally:
        await conn.disconnect()


async def test_read_multiple_lenient_marks_only_the_failing_entry(
    demo_server: str,
) -> None:
    """The successful values stand; the absent one becomes a failure marker."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        results = await conn.read_multiple(
            [*TARGETS, (ABSENT, iec61850.FC.ST)], strict=False
        )
        assert results[:3] == VALUES
        failure = results[3]
        assert isinstance(failure, iec61850.DataAccessFailure)
        assert failure.error == "ObjectNonExistent"
        assert failure.code == OBJECT_NON_EXISTENT_CODE
    finally:
        await conn.disconnect()


async def test_read_multiple_lenient_keeps_the_failing_position(
    demo_server: str,
) -> None:
    """A failure in the middle shifts nothing around it."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        results = await conn.read_multiple(
            [TARGETS[0], (ABSENT, iec61850.FC.ST), TARGETS[2]], strict=False
        )
        assert results[0] == VALUES[0]
        assert isinstance(results[1], iec61850.DataAccessFailure)
        assert results[2] == VALUES[2]
    finally:
        await conn.disconnect()


async def test_read_multiple_rejects_an_empty_request(demo_server: str) -> None:
    """Nothing to read is a caller error, not an empty round trip."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.read_multiple([])
    finally:
        await conn.disconnect()


async def test_read_multiple_rejects_an_unresolvable_reference(
    demo_server: str,
) -> None:
    """A malformed reference refuses the whole request before it is sent."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.read_multiple([("no-domain-separator", iec61850.FC.ST)])
    finally:
        await conn.disconnect()


async def test_read_multiple_agrees_with_single_reads(demo_server: str) -> None:
    """One batched round trip yields what the individual reads yield."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        singles = [await conn.read(ref, fc) for ref, fc in TARGETS]
        assert await conn.read_multiple(TARGETS) == singles
    finally:
        await conn.disconnect()
