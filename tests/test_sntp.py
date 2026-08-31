"""SNTP client tests.

Spawns a tiny in-process Python SNTP server in a daemon thread (so we don't
depend on a Rust binary), then exercises ``iec61850.query_sntp`` against it.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time

import pytest

import iec61850

NTP_UNIX_OFFSET_S = 2_208_988_800


def _unix_to_ntp_words(unix_seconds: float) -> tuple[int, int]:
    ntp_int = int(unix_seconds) + NTP_UNIX_OFFSET_S
    frac = int((unix_seconds - int(unix_seconds)) * (1 << 32)) & 0xFFFFFFFF
    return ntp_int & 0xFFFFFFFF, frac


class _PySntpServer:
    """30-line SNTP server: decode client request, build mode=4 reply."""

    def __init__(self, *, stratum: int = 1, reference_id: bytes = b"LOCL") -> None:
        self.stratum = stratum
        self.reference_id = reference_id
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.5)
        self.addr = self.sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.sock.close()
        self._thread.join(timeout=1.0)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self.sock.recvfrom(48)
            except (TimeoutError, OSError):
                continue
            if len(data) < 48:
                continue
            t2 = time.time()
            client_xmit = data[40:48]  # echo as originate
            t3 = time.time()
            ts2_int, ts2_frac = _unix_to_ntp_words(t2)
            ts3_int, ts3_frac = _unix_to_ntp_words(t3)
            header = (0 << 6) | (4 << 3) | 4  # LI=0, VN=4, Mode=4 server
            reply = struct.pack(
                ">BBbbII4s",
                header,
                self.stratum,
                6,  # poll (signed)
                -20,  # precision (signed)
                0,  # root delay
                0,  # root dispersion
                self.reference_id,
            )
            reply += struct.pack(">II", 0, 0)  # reference ts: leave 0
            reply += client_xmit  # originate ts echo
            reply += struct.pack(">II", ts2_int, ts2_frac)
            reply += struct.pack(">II", ts3_int, ts3_frac)
            assert len(reply) == 48
            try:
                self.sock.sendto(reply, peer)
            except OSError:
                pass


@pytest.fixture
def sntp_server() -> _PySntpServer:
    srv = _PySntpServer(stratum=2, reference_id=b"TEST")
    srv.start()
    yield srv
    srv.stop()


def test_query_sntp_returns_typed_response(sntp_server: _PySntpServer) -> None:
    host, port = sntp_server.addr

    async def run() -> iec61850.SntpResponse:
        return await iec61850.query_sntp(f"{host}:{port}", timeout_s=2.0)

    resp = asyncio.run(run())
    assert isinstance(resp, iec61850.SntpResponse)
    assert resp.stratum == 2
    assert resp.reference_id == b"TEST"
    assert resp.version == 4
    assert resp.leap_indicator == 0
    # Local loopback → offset tiny, RTT tiny.
    assert abs(resp.offset_seconds) < 1.0
    assert abs(resp.round_trip_seconds) < 1.0
    # server_time_unix_s within 5 s of current wall clock.
    assert abs(resp.server_time_unix_s - time.time()) < 5.0


def test_query_sntp_timeout_to_dead_address() -> None:
    """Bind a UDP socket but don't read from it → client must time out."""
    dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dead.bind(("127.0.0.1", 0))
    host, port = dead.getsockname()
    try:

        async def run() -> None:
            await iec61850.query_sntp(f"{host}:{port}", timeout_s=0.3)

        with pytest.raises(iec61850.IedTimeoutError):
            asyncio.run(run())
    finally:
        dead.close()


def test_query_sntp_rejects_bad_address() -> None:
    async def run() -> None:
        await iec61850.query_sntp("not-a-socket-addr", timeout_s=1.0)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_query_sntp_rejects_non_positive_timeout() -> None:
    async def run() -> None:
        await iec61850.query_sntp("127.0.0.1:1", timeout_s=0.0)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_sntp_response_server_datetime_utc(sntp_server: _PySntpServer) -> None:
    host, port = sntp_server.addr

    async def run() -> iec61850.SntpResponse:
        return await iec61850.query_sntp(f"{host}:{port}", timeout_s=2.0)

    resp = asyncio.run(run())
    dt = resp.server_datetime_utc()
    assert dt.tzinfo is not None
    assert abs(dt.timestamp() - resp.server_time_unix_s) < 1e-3
