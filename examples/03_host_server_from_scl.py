"""Smallest viable IED — load the CID, bind, idle until interrupted.

Run this in one terminal, then point the client-side examples at the
printed `host:port`. Synthetic measurement drift makes the cache look like
something is actually happening.

    python 03_host_server_from_scl.py

Expected stdout:

    DemoIED listening on 127.0.0.1:54321
    Try:  python 01_typed_io.py 127.0.0.1:54321
          python 02_browse_data_model.py 127.0.0.1:54321
    Press Ctrl-C to stop.
"""

from __future__ import annotations

import asyncio

from _shared import DEMO, spawn_demo


async def main() -> None:
    async with spawn_demo() as server:
        print(f"DemoIED listening on {server.bound_addr}")
        print("Try:  python 01_typed_io.py", server.bound_addr)
        print("      python 02_browse_data_model.py", server.bound_addr)
        print("Press Ctrl-C to stop.\n")

        # Walk the synthetic measurement up and down so subscribers see motion.
        try:
            tick = 0
            while True:
                server.update_float32(DEMO.srv_power, 230.0 + 5.0 * (tick % 8))
                server.update_float32(DEMO.srv_freq, 49.9 + 0.05 * (tick % 4))
                server.update_bool(DEMO.srv_ind1, tick % 2 == 0)
                tick += 1
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nshutting down")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
