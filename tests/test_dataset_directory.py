"""End-to-end tests for GetDataSetDirectory.

Exercises the whole chain: Python facade -> PyO3 -> iec61850-client -> MMS
GetNamedVariableListAttributes -> the server's data set registry.

The expected member lists are written out here rather than derived from the
server response, so the assertions carry an expectation independent of the
code under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _demo
import iec61850

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every prose source that hands a caller an `AcsiClass` member by name. A
# member that no longer exists makes the snippet raise `AttributeError`.
DOC_SOURCES = {
    "README.md": REPO_ROOT / "README.md",
    "_facade.py": REPO_ROOT / "python" / "iec61850" / "_facade.py",
    "examples/02_browse_data_model.py": REPO_ROOT
    / "examples"
    / "02_browse_data_model.py",
}

ACSI_MEMBER = re.compile("AcsiClass[.]([A-Za-z_][A-Za-z0-9_]*)")

DS_STATUS = f"{_demo.DOMAIN}/{_demo.DS_STATUS}"
DS_MEAS = f"{_demo.DOMAIN}/{_demo.DS_MEAS}"
DS_CLONE = f"{_demo.DOMAIN}/LLN0.ds_clone"
DS_DIR_DYN = f"{_demo.DOMAIN}/LLN0.ds_dir_dyn"
DS_ABSENT = f"{_demo.DOMAIN}/LLN0.ds_absent"

# `dsStatus` as `models/demo.cid` declares it: four FCDA on GGIO1 under ST.
DS_STATUS_MEMBERS = [
    iec61850.DataSetMember(
        object_ref=f"{_demo.DOMAIN}/GGIO1.Ind1.stVal", fc=iec61850.FC.ST
    ),
    iec61850.DataSetMember(
        object_ref=f"{_demo.DOMAIN}/GGIO1.Ind2.stVal", fc=iec61850.FC.ST
    ),
    iec61850.DataSetMember(
        object_ref=f"{_demo.DOMAIN}/GGIO1.Ind3.stVal", fc=iec61850.FC.ST
    ),
    iec61850.DataSetMember(
        object_ref=f"{_demo.DOMAIN}/GGIO1.Ind4.stVal", fc=iec61850.FC.ST
    ),
]

# `dsMeas` declares three FCDA in the CID; `_demo.wire_control_blocks` binds
# the two of them the client-side Read service resolves.
DS_MEAS_MEMBERS = [
    iec61850.DataSetMember(object_ref=_demo.POWER, fc=iec61850.FC.MX),
    iec61850.DataSetMember(object_ref=_demo.FREQ, fc=iec61850.FC.MX),
]


async def test_static_data_set_directory_matches_the_cid(demo_server: str) -> None:
    """`dsStatus` reports exactly the four FCDA the CID declares."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        directory = await conn.get_data_set_directory(DS_STATUS)
        assert directory.members == DS_STATUS_MEMBERS
        assert directory.deletable is False
    finally:
        await conn.disconnect()


async def test_measurand_data_set_directory_matches_its_binding(
    demo_server: str,
) -> None:
    """`dsMeas` reports the members bound at start, in binding order."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        directory = await conn.get_data_set_directory(DS_MEAS)
        assert directory.members == DS_MEAS_MEMBERS
        assert directory.deletable is False
    finally:
        await conn.disconnect()


async def test_dynamic_data_set_directory_round_trip(demo_server: str) -> None:
    """A created data set reports its own members and is deletable."""
    members = [
        iec61850.DataSetMember(object_ref=_demo.IND2, fc=iec61850.FC.ST),
        iec61850.DataSetMember(object_ref=_demo.POWER, fc=iec61850.FC.MX),
        iec61850.DataSetMember(object_ref=_demo.IND1, fc=iec61850.FC.ST),
    ]
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        await conn.create_data_set(DS_DIR_DYN, members)
        try:
            directory = await conn.get_data_set_directory(DS_DIR_DYN)
            assert directory.members == members
            assert directory.deletable is True
        finally:
            assert await conn.delete_data_set(DS_DIR_DYN) is True

        with pytest.raises(iec61850.IedServiceError):
            await conn.get_data_set_directory(DS_DIR_DYN)
    finally:
        await conn.disconnect()


async def test_directory_of_unknown_data_set_raises(demo_server: str) -> None:
    """A name the server never held is refused, like a deleted one."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedServiceError):
            await conn.get_data_set_directory(DS_ABSENT)
    finally:
        await conn.disconnect()


async def test_directory_members_clone_the_data_set(demo_server: str) -> None:
    """The member list feeds straight back into `create_data_set`."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        source = await conn.get_data_set_directory(DS_STATUS)
        await conn.create_data_set(DS_CLONE, source.members)
        try:
            clone = await conn.get_data_set_directory(DS_CLONE)
            assert clone.members == source.members
            assert clone.deletable is True
            assert await conn.get_data_set_values(DS_CLONE) == await (
                conn.get_data_set_values(DS_STATUS)
            )
        finally:
            assert await conn.delete_data_set(DS_CLONE) is True
    finally:
        await conn.disconnect()


async def test_logical_node_directory_lists_the_data_set_names(
    demo_server: str,
) -> None:
    """The call the docs point at for names, run against a live server.

    Listing names is GetLogicalNodeDirectory, a different service from the
    member list this module otherwise covers.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        names = await conn.get_logical_node_directory(
            f"{_demo.DOMAIN}/LLN0", iec61850.AcsiClass.DATASET
        )
        assert "dsMeas" in names
        assert "dsStatus" in names
    finally:
        await conn.disconnect()


@pytest.mark.parametrize("origin", sorted(DOC_SOURCES))
def test_documented_acsi_class_members_exist(origin: str) -> None:
    """Every `AcsiClass.<name>` a doc hands a caller must resolve.

    A member renamed in `_enums.py` leaves the snippets behind, and a reader
    copying one gets `AttributeError` rather than a wrong result.
    """
    text = DOC_SOURCES[origin].read_text(encoding="utf-8")
    named = sorted(set(ACSI_MEMBER.findall(text)))
    assert named, f"{origin} names no AcsiClass member"
    missing = [name for name in named if not hasattr(iec61850.AcsiClass, name)]
    assert not missing, f"{origin} names absent AcsiClass members: {missing}"


async def test_get_data_set_values_strict_and_lenient_agree(demo_server: str) -> None:
    """Every member of `dsStatus` resolves, so both modes return the values.

    This is the all-success path only, and says nothing about how a failing
    entry is reported: `test_dataset_value_mapping.py` covers the conversion
    of a failure marker, and `read_multiple` covers one end to end.
    """
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        strict = await conn.get_data_set_values(DS_STATUS)
        lenient = await conn.get_data_set_values(DS_STATUS, strict=False)
        assert lenient == strict
        assert len(strict) == len(DS_STATUS_MEMBERS)
        assert not any(isinstance(v, iec61850.DataAccessFailure) for v in lenient)
    finally:
        await conn.disconnect()
