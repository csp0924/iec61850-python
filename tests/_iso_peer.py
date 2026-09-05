"""A loopback peer that answers the association sequence by hand.

The peer completes the COTP handshake of RFC 1006, records the session CONNECT
SPDU the client sends, and answers it with a prepared SPDU. That makes the
bytes the client puts on the wire observable, and lets a test drive the client
down paths a conforming server never produces, such as a session REFUSE.

Nothing here depends on the package under test: the frames are built from the
octet layouts of ISO 8073 (COTP) and ISO 8327-1 (session), so a defect in the
encoder cannot hide itself in the peer as well.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

# RFC 1006 TPKT: version, reserved, then the total length of the frame.
TPKT_VERSION = 0x03
TPKT_HEADER_LEN = 4

# COTP TPDU codes and the fixed part of a CR / CC, which is the code, the two
# reference fields and the protocol class byte.
COTP_CC = 0xD0
COTP_CR_FIXED_LEN = 6

# COTP DT carrying a session SPDU: LI 2, code 0xF0, EOT set.
COTP_DT_HEADER = bytes([0x02, 0xF0, 0x80])

# Source reference the peer answers the CR with.
PEER_SOURCE_REFERENCE = 0x0001

# Protocol class 0, the only class this stack uses.
COTP_CLASS_0 = 0x00


async def _read_tpkt(reader: asyncio.StreamReader) -> bytes:
    """Read one whole TPKT frame and return the TPDU inside it."""
    header = await reader.readexactly(TPKT_HEADER_LEN)
    if header[0] != TPKT_VERSION:
        raise ValueError(f"tpkt version {header[0]:#04x}")
    total = int.from_bytes(header[2:4], "big")
    return await reader.readexactly(total - TPKT_HEADER_LEN)


def _write_tpkt(writer: asyncio.StreamWriter, tpdu: bytes) -> None:
    """Write one TPDU inside a TPKT frame."""
    total = TPKT_HEADER_LEN + len(tpdu)
    writer.write(bytes([TPKT_VERSION, 0x00]) + total.to_bytes(2, "big") + tpdu)


def _connect_confirm(cr: bytes) -> bytes:
    """Build the CC answering `cr`, echoing its variable part.

    A CR is ``LI, code, DST-REF, SRC-REF, class, variable part``. The CC swaps
    the references and keeps the negotiated options as offered.
    """
    li = cr[0]
    caller_reference = cr[4:6]
    variable_part = cr[1 + COTP_CR_FIXED_LEN : 1 + li]
    body = (
        bytes([COTP_CC])
        + caller_reference
        + PEER_SOURCE_REFERENCE.to_bytes(2, "big")
        + bytes([COTP_CLASS_0])
        + variable_part
    )
    return bytes([len(body)]) + body


class SessionPeer:
    """The state one association attempt leaves behind."""

    def __init__(self, addr: str) -> None:
        loop = asyncio.get_running_loop()
        self.addr = addr
        self._connect_request: asyncio.Future[bytes] = loop.create_future()
        self._connect_spdu: asyncio.Future[bytes] = loop.create_future()

    def _record(self, slot: asyncio.Future[bytes], frame: bytes) -> None:
        if not slot.done():
            slot.set_result(frame)

    def record_connect_request(self, tpdu: bytes) -> None:
        self._record(self._connect_request, tpdu)

    def record_connect_spdu(self, spdu: bytes) -> None:
        self._record(self._connect_spdu, spdu)

    async def connect_request(self, timeout_s: float = 5.0) -> bytes:
        """Return the COTP CR TPDU the client sent."""
        return await asyncio.wait_for(asyncio.shield(self._connect_request), timeout_s)

    async def connect_spdu(self, timeout_s: float = 5.0) -> bytes:
        """Return the session CONNECT SPDU the client sent."""
        return await asyncio.wait_for(asyncio.shield(self._connect_spdu), timeout_s)


@asynccontextmanager
async def session_peer(answer: bytes | None = None) -> AsyncIterator[SessionPeer]:
    """Serve one association attempt on an OS-assigned loopback port.

    The peer answers the COTP CR with a CC, records the session CONNECT, and
    then writes `answer` as a raw session SPDU. With `answer` left at ``None``
    it records the CONNECT and closes, which fails the association without
    exercising any particular refusal path.
    """
    peer: SessionPeer | None = None

    async def serve(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            cr = await _read_tpkt(reader)
            assert peer is not None
            peer.record_connect_request(cr)
            _write_tpkt(writer, _connect_confirm(cr))
            await writer.drain()

            dt = await _read_tpkt(reader)
            peer.record_connect_spdu(dt[len(COTP_DT_HEADER) :])

            if answer is not None:
                _write_tpkt(writer, COTP_DT_HEADER + answer)
                await writer.drain()
                # hold the connection open until the client drops it
                await reader.read()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    peer = SessionPeer(f"127.0.0.1:{port}")
    try:
        yield peer
    finally:
        server.close()
        await server.wait_closed()
