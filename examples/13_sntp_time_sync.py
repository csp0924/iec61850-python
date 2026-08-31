"""Querying an SNTP server to discipline an IED clock.

`iec61850.query_sntp` is a thin async wrapper over the Rust SNTP client and
returns a typed `SntpResponse` — no manual struct.pack. It takes a numeric
`host:port`, so a name is resolved here before the query.

    python 13_sntp_time_sync.py                  # default: pool.ntp.org:123
    python 13_sntp_time_sync.py 192.168.1.1:123 2.0

Expected stdout:

    server          103.186.118.211:123
    stratum           2  (1 = primary reference clock)
    version           4
    leap_indicator  0
    reference_id    b'\xd8\xef#\x00'
    poll            2^0 s
    precision       2^-24 s
    offset          +486.285 ms
    round_trip      3.281 ms
    server clock    2026-08-31T13:15:19.169716+00:00
"""

from __future__ import annotations

import asyncio
import socket
import sys

import iec61850


def resolve(target: str) -> str:
    """Turn `host[:port]` into the numeric `address:port` the query needs."""
    host, _, port = target.rpartition(":")
    if not host:
        host, port = target, "123"
    info = socket.getaddrinfo(host, int(port), type=socket.SOCK_DGRAM)
    address, resolved_port = info[0][4][:2]
    return f"{address}:{resolved_port}"


def format_response(resp: iec61850.SntpResponse) -> str:
    return (
        f"stratum         {resp.stratum:>3}  (1 = primary reference clock)\n"
        f"version         {resp.version:>3}\n"
        f"leap_indicator  {resp.leap_indicator}\n"
        f"reference_id    {resp.reference_id!r}\n"
        f"poll            2^{resp.poll} s\n"
        f"precision       2^{resp.precision} s\n"
        f"offset          {resp.offset_seconds * 1000:+.3f} ms\n"
        f"round_trip      {resp.round_trip_seconds * 1000:.3f} ms\n"
        f"server clock    {resp.server_datetime_utc().isoformat()}"
    )


async def main(target: str, timeout_s: float) -> int:
    try:
        server = resolve(target)
    except OSError as exc:
        print(f"cannot resolve {target}: {exc}", file=sys.stderr)
        return 1
    try:
        resp = await iec61850.query_sntp(server, timeout_s=timeout_s)
    except iec61850.IedError as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 1
    print(f"server          {server}")
    print(format_response(resp))
    return 0


if __name__ == "__main__":
    argument = sys.argv[1] if len(sys.argv) > 1 else "pool.ntp.org:123"
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    sys.exit(asyncio.run(main(argument, timeout)))
