"""Direct-operate control — one Operate command, no select stage.

The server prints what it received, mutates the cached value, and the
client reads back the post-operate state. Keep the originator metadata
(`OriginValue.or_ident`) human-readable so operator audit trails make
sense in a real SCADA.

    python 05_control_direct_operate.py

Expected stdout:

    OPERATE  value=True  ctl_num=1  or_cat=8  by=b'cookbook-05'
    client result: success=True  add_cause=None
    switch position after operate: closed
"""

from __future__ import annotations

import asyncio

import iec61850
from _shared import DEMO, spawn_demo


def install_switch_handler(server: iec61850.IedServer) -> None:
    def on_operate(_path: str, value: object, action: dict) -> None:
        origin = action["origin"]
        print(
            f"OPERATE  value={value!r}  ctl_num={action['ctl_num']}  "
            f"or_cat={origin['or_cat']}  by={origin['or_ident']!r}"
        )
        server.update_bool(f"{DEMO.srv_switch}.stVal", bool(value))

    # The CID configures SPCSO1 as direct-with-normal-security; the runtime
    # control model is chosen here and must agree with what the client uses.
    server.on_control(DEMO.srv_switch, ctl_model="direct-normal", operate=on_operate)


async def main() -> None:
    async with spawn_demo(configure=install_switch_handler) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            ctrl = conn.create_control_object(
                DEMO.switch, iec61850.ControlModel.DIRECT_NORMAL
            )
            ctrl.set_origin(iec61850.OriginValue(or_cat=8, or_ident=b"cookbook-05"))

            outcome = await ctrl.operate(True)
            print(
                f"client result: success={outcome.success}  "
                f"add_cause={outcome.add_cause}"
            )

            stval = await conn.read_bool(f"{DEMO.switch}.stVal", iec61850.FC.ST)
            print(f"switch position after operate: {'closed' if stval else 'open'}")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
