"""End-to-end tests for dynamic data set create and delete.

Exercises the whole chain: Python facade -> PyO3 -> iec61850-client ->
MMS DefineNamedVariableList / DeleteNamedVariableList -> the server's dynamic
data set registry.
"""

from __future__ import annotations

import pytest

import _demo
import iec61850

DS_DYN = f"{_demo.DOMAIN}/LLN0.ds_dyn1"
DS_DUP = f"{_demo.DOMAIN}/LLN0.ds_dup"
DS_BAD = f"{_demo.DOMAIN}/LLN0.ds_bad"
DS_GHOST = f"{_demo.DOMAIN}/LLN0.ds_ghost"


def _members() -> list[iec61850.DataSetMember]:
    return [
        iec61850.DataSetMember(object_ref=_demo.IND1, fc=iec61850.FC.ST),
        iec61850.DataSetMember(object_ref=_demo.POWER, fc=iec61850.FC.MX),
    ]


async def test_create_data_set_round_trip(demo_server: str) -> None:
    """Create, delete, and confirm the second delete reports nothing to do."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        await conn.create_data_set(DS_DYN, _members())

        deleted = await conn.delete_data_set(DS_DYN)
        assert deleted is True

        deleted_again = await conn.delete_data_set(DS_DYN)
        assert deleted_again is False
    finally:
        await conn.disconnect()


async def test_create_duplicate_data_set_raises(demo_server: str) -> None:
    """The server answers ServiceError(Definition) when the name is taken."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        await conn.create_data_set(DS_DUP, _members())
        try:
            with pytest.raises(iec61850.IedServiceError):
                await conn.create_data_set(DS_DUP, _members())
        finally:
            await conn.delete_data_set(DS_DUP)
    finally:
        await conn.disconnect()


async def test_create_with_unknown_member_raises(demo_server: str) -> None:
    """A member the server does not carry bubbles back as ServiceError."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        bogus = [
            iec61850.DataSetMember(
                object_ref=f"{_demo.DOMAIN}/GGIO1.NoSuchDO.stVal",
                fc=iec61850.FC.ST,
            ),
        ]
        with pytest.raises(iec61850.IedServiceError):
            await conn.create_data_set(DS_BAD, bogus)
    finally:
        await conn.disconnect()


async def test_delete_nonexistent_returns_false(demo_server: str) -> None:
    """Deleting a name the server does not have is not an error."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        deleted = await conn.delete_data_set(DS_GHOST)
        assert deleted is False
    finally:
        await conn.disconnect()


async def test_component_without_array_index_rejected_locally(
    demo_server: str,
) -> None:
    """``component`` requires ``array_index``.

    The facade composes the alternate-access reference and rejects the
    invalid combination before issuing any MMS call.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        bad = [
            iec61850.DataSetMember(
                object_ref=f"{_demo.DOMAIN}/GGIO1.Ind1",
                fc=iec61850.FC.ST,
                component="stVal",
            ),
        ]
        with pytest.raises(ValueError, match="component requires array_index"):
            await conn.create_data_set(DS_BAD, bad)
    finally:
        await conn.disconnect()


async def test_array_index_member_rejected_by_server(demo_server: str) -> None:
    """The facade composes an alternate-access reference, but the underlying
    MMS DefineNamedVariableList path does not accept array-indexed members;
    the rejection surfaces as ``ValueError`` from the lower layer.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        members = [
            iec61850.DataSetMember(
                object_ref=_demo.IND1,
                fc=iec61850.FC.ST,
                array_index=0,
            ),
        ]
        with pytest.raises(ValueError):
            await conn.create_data_set(f"{_demo.DOMAIN}/LLN0.ds_arr", members)
    finally:
        await conn.disconnect()


async def test_read_static_data_set_values(demo_server: str) -> None:
    """`dsStatus` is declared in the CID and bound at start; read it back."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        values = await conn.get_data_set_values(f"{_demo.DOMAIN}/{_demo.DS_STATUS}")
        assert len(values) == 4
        assert values[0] is True
    finally:
        await conn.disconnect()
