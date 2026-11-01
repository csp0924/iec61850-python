"""End-to-end tests for URCB reporting: read/write the RCB and install a handler.

Drives the whole chain — Python facade -> PyO3 -> iec61850-client ->
MMS Read/Write plus InformationReport -> the server's URCB engine — against
the demonstration IED. `urcbMeas` reports `dsMeas`, the two measurands the
test drives with `update_float32`.
"""

from __future__ import annotations

import asyncio
import queue

import pytest

import _demo
import iec61850

RCB_REF = _demo.URCB_REF
UNKNOWN_RCB = f"{_demo.DOMAIN}/LLN0$RP$nope"


async def test_get_rcb_values_reads_defaults(demo_server: str) -> None:
    """Initial RCB state exposes object_reference, data set, and trgOps."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        rcb = await conn.get_rcb_values(RCB_REF)
        assert rcb.object_reference == RCB_REF
        assert rcb.is_buffered is False
        assert rcb.dataset == f"{_demo.DOMAIN}/{_demo.DS_MEAS}"
        assert rcb.trigger_options.data_change is True
        assert rcb.rpt_ena is False
    finally:
        await conn.disconnect()


async def test_get_rcb_values_unknown_raises(demo_server: str) -> None:
    """Reading a non-existent RCB surfaces IedDataAccessError."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError):
            await conn.get_rcb_values(UNKNOWN_RCB)
    finally:
        await conn.disconnect()


async def test_rcb_write_mask_constants() -> None:
    """Sanity-check the public bit constants."""
    mask = iec61850.RcbWriteMask.fields("rpt_ena", "trigger_options", "opt_flds")
    assert mask.bits == 0x0002 | 0x0100 | 0x0020


async def test_install_then_uninstall_handler_roundtrip(demo_server: str) -> None:
    """Both handler calls are local and need no MMS round-trip."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        received: list[iec61850.ClientReport] = []

        def on_report(rpt: iec61850.ClientReport) -> None:
            received.append(rpt)

        await conn.install_report_handler(RCB_REF, on_report)
        await conn.uninstall_report_handler(RCB_REF)
        assert received == []  # nothing fired between install and uninstall
    finally:
        await conn.disconnect()


async def test_uninstall_unknown_raises(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(ValueError):
            await conn.uninstall_report_handler(UNKNOWN_RCB)
    finally:
        await conn.disconnect()


async def test_urcb_data_change_dispatch_to_callback() -> None:
    """Enable the URCB, register a callback, then move a reported measurand.

    Verifies the whole chain delivers a ClientReport carrying at least one
    entry inside the wait window.
    """
    async with _demo.hosted_demo() as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            # IEC 61850 Edition 2.1 rejects a write to an unreserved URCB from
            # another association, so reserve first and enable in the same
            # sequence; the client orders Resv before RptEna.
            rcb = await conn.get_rcb_values(RCB_REF)
            rcb.resv = True
            rcb.rpt_ena = True
            await conn.set_rcb_values(
                rcb, iec61850.RcbWriteMask.fields("resv", "rpt_ena")
            )

            sink: queue.Queue[iec61850.ClientReport] = queue.Queue()
            await conn.install_report_handler(
                RCB_REF, lambda r: sink.put(r), rpt_id=RCB_REF
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 4.0
            report: iec61850.ClientReport | None = None
            power = 230.5
            while loop.time() < deadline:
                power += 1.0
                server.update_float32(_demo.SRV_POWER, power)
                await conn.poll_reports(200)
                try:
                    report = sink.get_nowait()
                    break
                except queue.Empty:
                    continue

            assert report is not None, "no URCB report arrived within the window"
            assert report.rpt_id == RCB_REF
            assert report.rcb_reference == RCB_REF
            assert any(entry.ref is not None for entry in report.entries)
        finally:
            try:
                await conn.uninstall_report_handler(RCB_REF)
            except iec61850.IedError:
                pass
            await conn.disconnect()


async def test_spawn_report_dispatcher_runs_and_stops(demo_server: str) -> None:
    """The background dispatcher starts and stops cleanly."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        dispatcher = conn.spawn_report_dispatcher(interval_ms=200)
        await asyncio.sleep(0.5)
        await dispatcher.aclose()
    finally:
        await conn.disconnect()


async def test_report_dispatcher_keeps_the_error_that_ended_it() -> None:
    """Losing the connection ends the dispatcher: the error is kept and
    `on_error` is called with it once."""
    errors: list[BaseException] = []
    ended = asyncio.Event()

    def on_error(exc: BaseException) -> None:
        errors.append(exc)
        ended.set()

    async with _demo.hosted_demo() as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        dispatcher = conn.spawn_report_dispatcher(interval_ms=50, on_error=on_error)
        assert not dispatcher.done
        assert dispatcher.exception() is None
    try:
        await asyncio.wait_for(ended.wait(), timeout=30)
        await asyncio.sleep(0)
        assert dispatcher.done
        assert isinstance(dispatcher.exception(), iec61850.IedConnectionError)
        assert errors == [dispatcher.exception()]
    finally:
        await dispatcher.aclose()
        try:
            await conn.abort()
        except iec61850.IedError:
            pass


async def test_a_gi_mask_writes_the_gi_the_handle_holds(demo_server: str) -> None:
    """A mask naming GI writes the handle's GI: left False it writes False,
    which a disabled URCB accepts; set True it requests an interrogation,
    which a disabled URCB refuses with temporarily-unavailable."""
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        rcb = await conn.get_rcb_values(RCB_REF)
        assert rcb.rpt_ena is False
        rcb.gi = False
        await conn.set_rcb_values(rcb, iec61850.RcbWriteMask.fields("gi"))
        rcb.gi = True
        with pytest.raises(iec61850.IedDataAccessError) as info:
            await conn.set_rcb_values(rcb, iec61850.RcbWriteMask.fields("gi"))
        assert info.value.code == 2  # temporarily-unavailable
    finally:
        await conn.disconnect()
