"""Loopback peers that answer the association sequence by hand.

`session_peer` completes the COTP handshake of RFC 1006, records the session
CONNECT SPDU the client sends, and answers it with a prepared SPDU. That makes
the bytes the client puts on the wire observable, and lets a test drive the
client down paths a conforming server never produces, such as a session
REFUSE. `associated_peer` completes or refuses the association at the ACSE or
MMS level and then answers each request by script, with an MMS PDU, a dropped
connection, or nothing.

Nothing here depends on the package under test: the frames are built from the
octet layouts of ISO 8073 (COTP), ISO 8327-1 (session), ISO 8823-1
(presentation), ISO 8650-1 (ACSE) and ISO 9506-2 (MMS), so a defect in the
encoder cannot hide itself in the peer as well.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable

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


# ---------------------------------------------------------------------------
# A peer that completes the association and then answers requests by script.
#
# The accept is built from ISO 8327-1 (session AC), ISO 8823-1 (presentation
# CPA), ISO 8650-1 (ACSE AARE) and ISO 9506-2 (MMS Initiate) octet layouts,
# with the BER encoding of X.690.
# ---------------------------------------------------------------------------

# Session SPDU identifiers and parameter codes (ISO 8327-1).
SPDU_ACCEPT = 0x0E
SPDU_DATA = bytes([0x01, 0x00, 0x01, 0x00])  # GIVE-TOKENS then DATA TRANSFER
PGI_CONNECT_ACCEPT_ITEM = 0x05
PI_PROTOCOL_OPTIONS = 0x13
PI_VERSION_NUMBER = 0x16
PI_SESSION_REQUIREMENT = 0x14
PGI_USER_DATA = 0xC1
SESSION_VERSION_2 = 0x02
SESSION_REQUIREMENT_DUPLEX = bytes([0x00, 0x02])

# Presentation context identifiers the client proposes for ACSE and MMS.
ACSE_CONTEXT = 1
MMS_CONTEXT = 3

# Object identifiers, BER content octets.
OID_BER_TRANSFER_SYNTAX = bytes([0x51, 0x01])  # 2.1.1
OID_MMS_APPLICATION_CONTEXT = bytes([0x28, 0xCA, 0x22, 0x02, 0x03])  # 1.0.9506.2.3

# AARE result values (ISO 8650-1 Associate-result).
AARE_ACCEPTED = 0
AARE_REJECTED_PERMANENT = 1

# MMS PDU tags (ISO 9506-2 MMSpdu CHOICE).
MMS_CONFIRMED_REQUEST = 0xA0
MMS_CONFIRMED_RESPONSE = 0xA1
MMS_REJECT = 0xA4
MMS_CONCLUDE_REQUEST = bytes([0x8B, 0x00])

# Answers a responder can give instead of MMS bytes.
NO_ANSWER = None
RESET = "reset"
CLOSE = "close"


def tlv(tag: int | bytes, value: bytes) -> bytes:
    """Encode one BER TLV with a definite length."""
    tag_octets = bytes([tag]) if isinstance(tag, int) else tag
    n = len(value)
    if n < 0x80:
        length = bytes([n])
    elif n <= 0xFF:
        length = bytes([0x81, n])
    else:
        length = bytes([0x82]) + n.to_bytes(2, "big")
    return tag_octets + length + value


def read_tlv(buf: bytes, pos: int) -> tuple[int, bytes, int]:
    """Decode the TLV at `pos`; return its first tag octet, value and end."""
    tag = buf[pos]
    pos += 1
    if tag & 0x1F == 0x1F:
        while buf[pos] & 0x80:
            pos += 1
        pos += 1
    first = buf[pos]
    pos += 1
    if first < 0x80:
        length = first
    else:
        count = first & 0x7F
        length = int.from_bytes(buf[pos : pos + count], "big")
        pos += count
    return tag, buf[pos : pos + length], pos + length


def presentation_data(context: int, payload: bytes) -> bytes:
    """Fully-encoded user data carrying one PDV of `context` (ISO 8823-1)."""
    pdv = tlv(0x02, bytes([context])) + tlv(0xA0, payload)
    return tlv(0x61, tlv(0x30, pdv))


def initiate_response(*, max_calling: int = 5) -> bytes:
    """An Initiate-ResponsePDU granting every service and parameter CBB."""
    detail = (
        tlv(0x80, b"\x01")
        + tlv(0x81, bytes([0x05, 0xF1, 0x00]))
        + tlv(0x82, bytes([0x03]) + b"\xff" * 10 + b"\xf8")
    )
    return tlv(
        0xA9,
        tlv(0x80, (32000).to_bytes(2, "big"))
        + tlv(0x81, bytes([max_calling]))
        + tlv(0x82, b"\x05")
        + tlv(0x83, b"\x0a")
        + tlv(0xA4, detail),
    )


def initiate_error(code: int) -> bytes:
    """An Initiate-ErrorPDU whose ServiceError is of the initiate class."""
    return tlv(0xAA, tlv(0xA0, tlv(0x88, bytes([code]))))


def aare(result: int, mms: bytes | None) -> bytes:
    """An AARE carrying `result` and, when given, `mms` as user information."""
    body = (
        tlv(0xA1, tlv(0x06, OID_MMS_APPLICATION_CONTEXT))
        + tlv(0xA2, tlv(0x02, bytes([result])))
        + tlv(0xA3, tlv(0xA1, tlv(0x02, b"\x00")))
    )
    if mms is not None:
        external = tlv(0x02, bytes([MMS_CONTEXT])) + tlv(0xA0, mms)
        body += tlv(0xBE, tlv(0x28, external))
    return tlv(0x61, body)


def session_accept(acse: bytes) -> bytes:
    """A session AC whose user data is a CPA PPDU carrying `acse`."""
    result_entry = tlv(0x30, tlv(0x80, b"\x00") + tlv(0x81, OID_BER_TRANSFER_SYNTAX))
    normal_mode = (
        tlv(0x83, b"\x00\x00\x00\x01")
        + tlv(0xA5, result_entry + result_entry)
        + presentation_data(ACSE_CONTEXT, acse)
    )
    cpa = tlv(0x31, tlv(0xA0, tlv(0x80, b"\x01")) + tlv(0xA2, normal_mode))
    accept_item = bytes([PI_PROTOCOL_OPTIONS, 1, 0x00, PI_VERSION_NUMBER, 1, SESSION_VERSION_2])
    params = (
        bytes([PGI_CONNECT_ACCEPT_ITEM, len(accept_item)])
        + accept_item
        + bytes([PI_SESSION_REQUIREMENT, 2])
        + SESSION_REQUIREMENT_DUPLEX
    )
    if len(cpa) < 0xFF:
        params += bytes([PGI_USER_DATA, len(cpa)]) + cpa
    else:
        params += bytes([PGI_USER_DATA, 0xFF]) + len(cpa).to_bytes(2, "big") + cpa
    if len(params) < 0xFF:
        return bytes([SPDU_ACCEPT, len(params)]) + params
    return bytes([SPDU_ACCEPT, 0xFF]) + len(params).to_bytes(2, "big") + params


def mms_of(spdu: bytes) -> bytes:
    """Return the MMS PDU inside a session DATA SPDU and its PDV."""
    _, sequence, _ = read_tlv(spdu, len(SPDU_DATA))
    _, pdv, _ = read_tlv(sequence, 0)
    pos = 0
    while pos < len(pdv):
        tag, value, pos = read_tlv(pdv, pos)
        if tag == 0xA0:
            return value
    raise ValueError("pdv without single-ASN1-type data")


def invoke_id_of(request: bytes) -> bytes:
    """Return the content octets of the invokeID of a Confirmed-RequestPDU."""
    tag, body, _ = read_tlv(request, 0)
    if tag != MMS_CONFIRMED_REQUEST:
        raise ValueError(f"not a confirmed request: {tag:#04x}")
    tag, invoke_id, _ = read_tlv(body, 0)
    return invoke_id


def confirmed_response(invoke_id: bytes, service: bytes) -> bytes:
    """A Confirmed-ResponsePDU answering `invoke_id` with `service`."""
    return tlv(MMS_CONFIRMED_RESPONSE, tlv(0x02, invoke_id) + service)


def reject(invoke_id: bytes, reason_tag: int, code: int) -> bytes:
    """A RejectPDU naming `invoke_id`, its rejectReason given by CHOICE tag
    and value."""
    return tlv(MMS_REJECT, tlv(0x80, invoke_id) + tlv(0x80 | reason_tag, bytes([code])))


Responder = Callable[[bytes], "bytes | str | None"]


@asynccontextmanager
async def associated_peer(
    responder: Responder | None = None,
    *,
    acse: bytes | None = None,
) -> AsyncIterator[str]:
    """Serve associations on an OS-assigned loopback port; yield its address.

    The peer answers the COTP CR with a CC and the session CONNECT with an AC
    carrying `acse`, by default an accepting AARE with an Initiate-Response.
    Each Confirmed-RequestPDU that follows is handed to `responder`, whose
    answer is the MMS PDU to send, ``NO_ANSWER``, ``RESET`` to drop the
    connection with a TCP RST, or ``CLOSE`` to close it in order.

    The sockets are driven directly rather than through stream transports, so
    a reset is a close with a zero linger and never preceded by a FIN.
    """
    loop = asyncio.get_running_loop()
    accept = session_accept(
        acse if acse is not None else aare(AARE_ACCEPTED, initiate_response())
    )

    async def recv_exact(sock: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = await loop.sock_recv(sock, n - len(data))
            if not chunk:
                raise asyncio.IncompleteReadError(data, n)
            data += chunk
        return data

    async def recv_tpdu(sock: socket.socket) -> bytes:
        header = await recv_exact(sock, TPKT_HEADER_LEN)
        total = int.from_bytes(header[2:4], "big")
        return await recv_exact(sock, total - TPKT_HEADER_LEN)

    async def send_tpdu(sock: socket.socket, tpdu: bytes) -> None:
        total = TPKT_HEADER_LEN + len(tpdu)
        await loop.sock_sendall(
            sock, bytes([TPKT_VERSION, 0x00]) + total.to_bytes(2, "big") + tpdu
        )

    async def serve(sock: socket.socket) -> None:
        try:
            cr = await recv_tpdu(sock)
            await send_tpdu(sock, _connect_confirm(cr))
            await recv_tpdu(sock)
            await send_tpdu(sock, COTP_DT_HEADER + accept)
            while True:
                dt = await recv_tpdu(sock)
                request = mms_of(dt[len(COTP_DT_HEADER) :])
                answer = responder(request) if responder is not None else NO_ANSWER
                if answer == RESET:
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                    return
                if answer == CLOSE:
                    return
                if isinstance(answer, bytes):
                    spdu = SPDU_DATA + presentation_data(MMS_CONTEXT, answer)
                    await send_tpdu(sock, COTP_DT_HEADER + spdu)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            sock.close()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    sessions: set[asyncio.Task[None]] = set()

    async def accept_loop() -> None:
        while True:
            sock, _ = await loop.sock_accept(listener)
            sock.setblocking(False)
            task = asyncio.create_task(serve(sock))
            sessions.add(task)
            task.add_done_callback(sessions.discard)

    acceptor = asyncio.create_task(accept_loop())
    try:
        yield f"127.0.0.1:{port}"
    finally:
        acceptor.cancel()
        for task in list(sessions):
            task.cancel()
        await asyncio.gather(acceptor, *sessions, return_exceptions=True)
        listener.close()
