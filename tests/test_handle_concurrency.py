"""Client handles stay usable while a request they issued is in flight.

Each case holds a request back until the test has used the handle, so a
getter or setter that waited for the request would stall the event loop the
held request needs to finish. The order of events decides the outcome; the
cases do not depend on how long anything takes, and the watchdog only turns
such a deadlock into a failure.
"""

from __future__ import annotations

import asyncio
import faulthandler
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest

import _demo
import iec61850
from iec61850 import FC, IedConnection, IedServer, RcbWriteMask

from test_server_hosted_reporting import IND1_SERVER, SERVER_SCL, URCB_MMS


@pytest.fixture(autouse=True)
def _abort_on_deadlock() -> Iterator[None]:
    """A handle that blocks the loop while holding the GIL deadlocks the
    process, where no Python-level timeout can run; dump the stacks and exit
    instead of hanging."""
    faulthandler.dump_traceback_later(120, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


class _HeldForwarder:
    """A loopback TCP forwarder that holds client bytes back once armed.

    While armed, the first chunk the client sends sets `seen` and is
    forwarded only after `release` is set. It runs on the test's event loop,
    so it forwards nothing while that loop is blocked.
    """

    def __init__(self, target: str) -> None:
        self._host, port = target.rsplit(":", 1)
        self._port = int(port)
        self.armed = False
        self.seen = asyncio.Event()
        self.release = asyncio.Event()
        self.addr = ""

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        held: bool,
    ) -> None:
        try:
            while data := await reader.read(65536):
                if held and self.armed:
                    self.seen.set()
                    await self.release.wait()
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _accept(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        server_reader, server_writer = await asyncio.open_connection(
            self._host, self._port
        )
        await asyncio.gather(
            self._pump(client_reader, server_writer, held=True),
            self._pump(server_reader, client_writer, held=False),
        )

    @asynccontextmanager
    async def running(self) -> AsyncIterator[_HeldForwarder]:
        listener = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        host, port = listener.sockets[0].getsockname()[:2]
        self.addr = f"{host}:{port}"
        try:
            yield self
        finally:
            self.release.set()
            listener.close()


async def test_control_handle_is_usable_while_an_operate_is_in_flight() -> None:
    """Reading `ctl_model` and calling `set_test` while an operate waits for
    its CommandTermination does not block the loop that runs the server's
    `async` operate callback."""
    entered = asyncio.Event()
    handle_used = asyncio.Event()

    def configure(server: IedServer) -> None:
        async def operate(_path: str, value: object, _action: dict) -> None:
            entered.set()
            await handle_used.wait()
            server.update_bool(f"{_demo.SRV_SWITCH}.stVal", bool(value))

        server.on_control(
            _demo.SRV_SWITCH, ctl_model="direct-enhanced", operate=operate
        )

    async with _demo.hosted_demo(
        control_blocks=False, configure=configure, ctl_model="direct-enhanced"
    ) as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            operate = asyncio.create_task(spc.operate(True))
            await entered.wait()

            assert spc.ctl_model is iec61850.ControlModel.DIRECT_ENHANCED
            assert spc.object_reference == _demo.SWITCH
            spc.set_test(False)
            spc.set_origin(iec61850.OriginValue(or_cat=3, or_ident=b"hmi"))
            handle_used.set()

            outcome = await operate
            assert outcome.success is True
            assert await conn.read_bool(f"{_demo.SWITCH}.stVal", FC.ST) is True
        finally:
            await conn.disconnect()


async def test_settings_recorded_during_an_operate_apply_to_the_next_command() -> None:
    """A setter called while a command is in flight takes effect on the next
    command: the server sees the new `Test` flag and originator."""
    seen: list[dict] = []
    entered = asyncio.Event()
    handle_used = asyncio.Event()

    def configure(server: IedServer) -> None:
        async def operate(_path: str, _value: object, action: dict) -> None:
            seen.append(action)
            if len(seen) == 1:
                entered.set()
                await handle_used.wait()

        server.on_control(
            _demo.SRV_SWITCH, ctl_model="direct-enhanced", operate=operate
        )

    async with _demo.hosted_demo(
        control_blocks=False, configure=configure, ctl_model="direct-enhanced"
    ) as server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(
                _demo.SWITCH, iec61850.ControlModel.DIRECT_ENHANCED
            )
            first = asyncio.create_task(spc.operate(True))
            await entered.wait()
            spc.set_test(True)
            spc.set_origin(iec61850.OriginValue(or_cat=3, or_ident=b"hmi"))
            handle_used.set()
            assert (await first).success is True

            assert (await spc.operate(False)).success is True
            assert seen[0]["test"] is False
            assert seen[1]["test"] is True
            assert seen[1]["origin"] == {"or_cat": 3, "or_ident": b"hmi"}
        finally:
            await conn.disconnect()


async def test_rcb_handle_is_usable_while_set_rcb_values_is_in_flight() -> None:
    """The handle's attributes read and write while its `set_rcb_values`
    request is held on the way to the server, and the request carries the
    values the handle had when it was issued."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", [IND1_SERVER])
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed"],
        opt_flds=["seq_num", "reason"],
        buf_tm_ms=10,
    )
    async with s:
        async with _HeldForwarder(s.bound_addr).running() as forwarder:
            conn = await IedConnection.connect(forwarder.addr)
            try:
                rcb = await conn.get_rcb_values(URCB_MMS)
                rcb.resv = True
                rcb.rpt_ena = True
                rcb.buf_time_ms = 20
                forwarder.armed = True
                pushed = asyncio.create_task(
                    conn.set_rcb_values(
                        rcb, RcbWriteMask.fields("resv", "rpt_ena", "buf_time")
                    )
                )
                await forwarder.seen.wait()

                assert rcb.rpt_ena is True
                assert rcb.object_reference == URCB_MMS
                rcb.buf_time_ms = 30
                assert rcb.buf_time_ms == 30
                forwarder.armed = False
                forwarder.release.set()

                await pushed
                after = await conn.get_rcb_values(URCB_MMS)
                assert after.rpt_ena is True
                assert after.resv is True
                assert after.buf_time_ms == 20
            finally:
                await conn.disconnect()
