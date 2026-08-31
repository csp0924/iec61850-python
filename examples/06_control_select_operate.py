"""Select-Before-Operate with a togglable interlock and a cancel path.

Three independent attempts share one control session. The check handler runs
at the select stage, so an engaged interlock is reported before an operator
can reserve the object rather than after:

    1. interlock engaged -> select is refused, a following operate fails
    2. select then cancel -- releases the reservation without operating
    3. interlock cleared -> select succeeds and operate flips the switch

    python 06_control_select_operate.py

Expected stdout:

    attempt 1 - interlock engaged
      select  -> False
      operate -> success=False  add_cause=unknown

    attempt 2 - cancel after select
      select  -> False
      cancel  -> success=True

    attempt 3 - interlock cleared
      select  -> True
      OPERATE accepted: value=True ctl_num=2
      operate -> success=True  add_cause=None

    final position: closed
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import iec61850
from _shared import DEMO, spawn_demo


@dataclass
class Interlock:
    """Boxed flag so the closures below can flip it from outside."""

    engaged: bool = True


def install_sbo_handler(server: iec61850.IedServer, interlock: Interlock) -> None:
    def check(_path: str, _value: object, _action: dict) -> None:
        if interlock.engaged:
            err = iec61850.IedControlError("racking position blocks operation")
            err.add_cause = "BlockedByInterlocking"
            raise err

    def operate(_path: str, value: object, action: dict) -> None:
        print(f"  OPERATE accepted: value={value} ctl_num={action['ctl_num']}")
        server.update_bool(f"{DEMO.srv_switch}.stVal", bool(value))

    # Overrides the CID's direct-with-normal-security so the same SPC can
    # demonstrate the select stage.
    server.on_control(
        DEMO.srv_switch, ctl_model="sbo-normal", check=check, operate=operate
    )


async def attempt(
    label: str, ctrl: iec61850.ControlObjectClient, *, cancel: bool = False
) -> None:
    print(label)
    print(f"  select  -> {await ctrl.select()}")
    if cancel:
        result = await ctrl.cancel(True)
        print(f"  cancel  -> success={result.success}")
        return
    outcome = await ctrl.operate(True)
    print(f"  operate -> success={outcome.success}  add_cause={outcome.add_cause}")


async def main() -> None:
    interlock = Interlock(engaged=True)

    async with spawn_demo(
        configure=lambda s: install_sbo_handler(s, interlock)
    ) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            ctrl = conn.create_control_object(
                DEMO.switch, iec61850.ControlModel.SBO_NORMAL
            )
            ctrl.set_origin(iec61850.OriginValue(or_cat=8, or_ident=b"cookbook-06"))

            await attempt("attempt 1 - interlock engaged", ctrl)
            print()
            await attempt("attempt 2 - cancel after select", ctrl, cancel=True)
            print()

            interlock.engaged = False
            await attempt("attempt 3 - interlock cleared", ctrl)

            final = await conn.read_bool(f"{DEMO.switch}.stVal", iec61850.FC.ST)
            print(f"\nfinal position: {'closed' if final else 'open'}")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
