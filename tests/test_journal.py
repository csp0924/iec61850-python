"""End-to-end tests for the Log Service journal query surface.

The `journal_server` fixture registers an LCB on the demonstration IED and
seeds it with `JOURNAL_COUNT` entries spaced `JOURNAL_STEP_MS` apart, each
carrying a single Boolean variable with reason code 0x02 (data change).
"""

from __future__ import annotations

import pytest

import _demo
import iec61850

BASE_TIME_MS = _demo.JOURNAL_BASE_MS
TIME_STEP_MS = _demo.JOURNAL_STEP_MS
ENTRY_COUNT = _demo.JOURNAL_COUNT
LAST_TIME_MS = _demo.JOURNAL_LAST_MS


async def test_query_by_time_returns_all_seeded_entries(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        entries, more = await conn.query_journal_by_time(
            _demo.LOG_REF, BASE_TIME_MS, LAST_TIME_MS
        )
        assert len(entries) == ENTRY_COUNT
        assert more is False
        first, last = entries[0], entries[-1]
        assert first.time_ms == BASE_TIME_MS
        assert last.time_ms == LAST_TIME_MS
        # entry_id is the 8-byte wire identifier; the in-memory backend
        # assigns monotonically, so the last entry sorts after the first.
        assert len(first.entry_id) == 8
        assert first.entry_id < last.entry_id
        assert all(
            entries[i].time_ms <= entries[i + 1].time_ms
            for i in range(len(entries) - 1)
        )
    finally:
        await conn.disconnect()


async def test_query_by_time_entry_has_expected_variable(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        entries, _ = await conn.query_journal_by_time(
            _demo.LOG_REF, BASE_TIME_MS, BASE_TIME_MS
        )
        assert len(entries) == 1
        entry = entries[0]
        assert len(entry.variables) == 1
        variable = entry.variables[0]
        assert variable.data_ref == _demo.JOURNAL_DATA_REF
        assert variable.value is True  # entry 0 was seeded with `i % 2 == 0`
        assert variable.reason_code == 0x02
    finally:
        await conn.disconnect()


async def test_query_by_time_subrange(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        start = BASE_TIME_MS + 5 * TIME_STEP_MS
        end = BASE_TIME_MS + 9 * TIME_STEP_MS
        entries, _ = await conn.query_journal_by_time(_demo.LOG_REF, start, end)
        assert [e.time_ms for e in entries] == [
            start + i * TIME_STEP_MS for i in range(5)
        ]
    finally:
        await conn.disconnect()


async def test_query_after_entry_resumes_from_seen_entry(journal_server: str) -> None:
    """First page, resume from the last entry's (time, id), expect no overlap."""
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        page1, _ = await conn.query_journal_by_time(
            _demo.LOG_REF, BASE_TIME_MS, BASE_TIME_MS + 4 * TIME_STEP_MS
        )
        assert len(page1) == 5
        cursor = page1[-1]
        page2, _ = await conn.query_journal_after_entry(
            _demo.LOG_REF, cursor.time_ms, cursor.entry_id
        )
        # `after` is strict — the cursor entry itself must not appear again.
        assert all(e.entry_id != cursor.entry_id for e in page2)
        assert all(e.time_ms >= cursor.time_ms for e in page2)
        assert len(page2) == ENTRY_COUNT - 5
    finally:
        await conn.disconnect()


async def test_query_after_entry_rejects_bad_id_length(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        with pytest.raises(ValueError, match="entry_id must be exactly 8 bytes"):
            await conn.query_journal_after_entry(_demo.LOG_REF, 0, b"\x00" * 4)
    finally:
        await conn.disconnect()


async def test_query_journal_rejects_bad_log_ref(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        with pytest.raises(ValueError):
            await conn.query_journal_by_time("no_slash_here", 0, 1)
    finally:
        await conn.disconnect()


# A PDU size at which one ReadJournal response holds only a few entries, so
# the server pages the journal with moreFollows.
SMALL_PDU = 300

SEEDED_TIMES = [BASE_TIME_MS + i * TIME_STEP_MS for i in range(ENTRY_COUNT)]


async def _first_seeded_id(conn: iec61850.IedConnection) -> bytes:
    """EntryID of the oldest seeded entry.

    Seeding the server also logs the live value change of `seed()`, so the
    journal holds one entry outside the seeded time range.
    """
    entries, _ = await conn.query_journal(
        _demo.LOG_REF, iec61850.JournalQuery.by_time(BASE_TIME_MS, BASE_TIME_MS)
    )
    return entries[0].entry_id


async def test_query_journal_by_time_form_matches_legacy(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        query = iec61850.JournalQuery.by_time(BASE_TIME_MS, LAST_TIME_MS)
        entries, more = await conn.query_journal(_demo.LOG_REF, query)
        legacy, _ = await conn.query_journal_by_time(
            _demo.LOG_REF, BASE_TIME_MS, LAST_TIME_MS
        )
    finally:
        await conn.disconnect()
    assert more is False
    assert entries == legacy
    assert [e.time_ms for e in entries] == SEEDED_TIMES


async def test_query_journal_max_entries_limits_one_request(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        first = await _first_seeded_id(conn)
        query = iec61850.JournalQuery(start_entry=first, max_entries=5)
        entries, more = await conn.query_journal(_demo.LOG_REF, query)
    finally:
        await conn.disconnect()
    assert entries[0].entry_id == first
    assert [e.time_ms for e in entries] == SEEDED_TIMES[:5]
    assert more is False


async def test_query_journal_pages_at_small_pdu(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(
        journal_server, local_max_pdu_size=SMALL_PDU
    )
    try:
        query = iec61850.JournalQuery.by_time(BASE_TIME_MS, LAST_TIME_MS)
        page, more = await conn.query_journal(_demo.LOG_REF, query)
        assert more is True
        assert 0 < len(page) < ENTRY_COUNT
        last = page[-1]
        resume = iec61850.JournalQuery(
            end_time_ms=LAST_TIME_MS, after_entry=(last.time_ms, last.entry_id)
        )
        page2, _ = await conn.query_journal(_demo.LOG_REF, resume)
    finally:
        await conn.disconnect()
    assert page2[0].time_ms == last.time_ms + TIME_STEP_MS
    assert {e.entry_id for e in page}.isdisjoint(e.entry_id for e in page2)


async def test_query_journal_all_collects_every_page(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(
        journal_server, local_max_pdu_size=SMALL_PDU
    )
    try:
        first = await _first_seeded_id(conn)
        entries = await conn.query_journal_all(
            _demo.LOG_REF, iec61850.JournalQuery(start_entry=first)
        )
    finally:
        await conn.disconnect()
    assert [e.time_ms for e in entries] == SEEDED_TIMES
    assert len({e.entry_id for e in entries}) == ENTRY_COUNT


async def test_query_journal_all_max_entries_bounds_total(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(
        journal_server, local_max_pdu_size=SMALL_PDU
    )
    try:
        first = await _first_seeded_id(conn)
        query = iec61850.JournalQuery(start_entry=first, max_entries=7)
        entries = await conn.query_journal_all(_demo.LOG_REF, query)
    finally:
        await conn.disconnect()
    assert [e.time_ms for e in entries] == SEEDED_TIMES[:7]


async def test_query_journal_all_end_time_truncates(journal_server: str) -> None:
    end = BASE_TIME_MS + 9 * TIME_STEP_MS
    conn = await iec61850.IedConnection.connect(
        journal_server, local_max_pdu_size=SMALL_PDU
    )
    try:
        query = iec61850.JournalQuery.by_time(BASE_TIME_MS, end)
        entries = await conn.query_journal_all(_demo.LOG_REF, query)
    finally:
        await conn.disconnect()
    assert [e.time_ms for e in entries] == SEEDED_TIMES[:10]
    assert all(e.time_ms <= end for e in entries)


async def test_query_journal_all_starting_after_skips_cursor(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        first = await _first_seeded_id(conn)
        head, _ = await conn.query_journal(
            _demo.LOG_REF, iec61850.JournalQuery(start_entry=first, max_entries=3)
        )
        cursor = head[-1]
        rest = await conn.query_journal_all(
            _demo.LOG_REF,
            iec61850.JournalQuery.starting_after(cursor.time_ms, cursor.entry_id),
        )
    finally:
        await conn.disconnect()
    assert [e.time_ms for e in rest] == SEEDED_TIMES[3:]
    assert all(e.entry_id != cursor.entry_id for e in rest)


async def test_query_journal_variables_filter(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        kept = await conn.query_journal_all(
            _demo.LOG_REF,
            iec61850.JournalQuery(
                start_time_ms=BASE_TIME_MS,
                end_time_ms=LAST_TIME_MS,
                variables=(_demo.JOURNAL_DATA_REF,),
            ),
        )
        dropped = await conn.query_journal_all(
            _demo.LOG_REF,
            iec61850.JournalQuery(
                start_time_ms=BASE_TIME_MS,
                end_time_ms=LAST_TIME_MS,
                variables=(f"{_demo.DOMAIN}/GGIO1$ST$Ind2$stVal",),
            ),
        )
    finally:
        await conn.disconnect()
    assert [e.time_ms for e in kept] == SEEDED_TIMES
    assert dropped == []


async def test_query_journal_unknown_log_raises(journal_server: str) -> None:
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        with pytest.raises(iec61850.IedServiceError) as info:
            await conn.query_journal_all(
                f"{_demo.DOMAIN}/LLN0$NoSuchLog", iec61850.JournalQuery()
            )
    finally:
        await conn.disconnect()
    assert info.value.reason == "service-error"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"start_time_ms": 0, "start_entry": b"\x00" * 8}, "mutually exclusive"),
        ({"end_time_ms": 0, "max_entries": 1}, "mutually exclusive"),
        ({"start_entry": b"\x00" * 4}, "exactly 8 bytes"),
        ({"after_entry": (0, b"\x00")}, "exactly 8 bytes"),
        ({"max_entries": -1}, "non-negative"),
        ({"variables": "not-a-sequence"}, "sequence"),
    ],
)
def test_journal_query_rejects_invalid_selection(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        iec61850.JournalQuery(**kwargs)


def test_journal_query_to_native_round_trips_fields() -> None:
    query = iec61850.JournalQuery(
        start_entry=b"\x00" * 7 + b"\x05",
        max_entries=4,
        variables=("a", "b"),
        after_entry=(10, b"\x00" * 7 + b"\x02"),
    )
    assert query.to_native() == {
        "start_time_ms": None,
        "start_entry": b"\x00" * 7 + b"\x05",
        "end_time_ms": None,
        "max_entries": 4,
        "variables": ["a", "b"],
        "after_entry": (10, b"\x00" * 7 + b"\x02"),
    }


async def test_logical_node_directory_lists_log_control_blocks_and_logs_apart(
    journal_server: str,
) -> None:
    """`AcsiClass.LOG_CONTROL` lists the LCBs of a logical node and
    `AcsiClass.LOG` its journals."""
    conn = await iec61850.IedConnection.connect(journal_server)
    try:
        lln0 = f"{_demo.DOMAIN}/LLN0"
        lcbs = await conn.get_logical_node_directory(lln0, iec61850.AcsiClass.LOG_CONTROL)
        logs = await conn.get_logical_node_directory(lln0, iec61850.AcsiClass.LOG)
        assert "evlog" in lcbs
        assert "GeneralLog" in logs
        assert "evlog" not in logs
        assert "GeneralLog" not in lcbs
    finally:
        await conn.disconnect()
