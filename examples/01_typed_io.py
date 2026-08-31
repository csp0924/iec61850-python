"""Reading typed values, quality, and timestamps from the demonstration IED.

Pairs with `03_host_server_from_scl.py` — start that script first, then run
this one with the address it printed:

    python 01_typed_io.py 127.0.0.1:PORT

Expected stdout:

    switch:  open   q=good   t=00:00:00
    power:     230.50 kW
    freq:       50.00 Hz
    mode:    1  (1=on, 2=blocked, 5=off)

    parallel read: 230.50 kW / 50.00 Hz
"""

from __future__ import annotations

import asyncio
import sys

import iec61850
from _shared import DEMO


async def snapshot(client: iec61850.Iec61850Client) -> None:
    """Print one consistent view of the switch, the measurands, and the mode."""
    conn = client.connection
    switch, power, freq, mode = await asyncio.gather(
        conn.read_bool(f"{DEMO.switch}.stVal", iec61850.FC.ST),
        conn.read_float(DEMO.power, iec61850.FC.MX),
        conn.read_float(DEMO.freq, iec61850.FC.MX),
        conn.read_int32(DEMO.mode, iec61850.FC.ST),
    )
    quality = await conn.read_quality(f"{DEMO.switch}.q", iec61850.FC.ST)
    timestamp = await conn.read_timestamp(f"{DEMO.switch}.t", iec61850.FC.ST)

    state = "closed" if switch else "open"
    print(
        f"switch:  {state}   q={quality.validity.name.lower()}   "
        f"t={timestamp:%H:%M:%S}"
    )
    print(f"power:  {power:9.2f} kW")
    print(f"freq:   {freq:9.2f} Hz")
    print(f"mode:    {mode}  (1=on, 2=blocked, 5=off)")


async def read_pair(client: iec61850.Iec61850Client) -> tuple[float, float]:
    """Two measurands over one association, issued in parallel.

    The connection multiplexes outstanding requests by invoke ID, so
    `asyncio.gather` overlaps the two round-trips instead of serialising them.
    """
    conn = client.connection
    power, freq = await asyncio.gather(
        conn.read_float(DEMO.power, iec61850.FC.MX),
        conn.read_float(DEMO.freq, iec61850.FC.MX),
    )
    return power, freq


async def main(address: str) -> None:
    host, _, port = address.partition(":")
    config = iec61850.Iec61850ClientConfig(
        address=host, port=int(port), timeout_ms=5000
    )
    async with iec61850.Iec61850Client(config) as client:
        await snapshot(client)
        power, freq = await read_pair(client)
        print(f"\nparallel read: {power:.2f} kW / {freq:.2f} Hz")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:102"))
