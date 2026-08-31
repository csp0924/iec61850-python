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
