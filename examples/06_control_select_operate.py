"""Select-Before-Operate with a togglable interlock and a cancel path.

Three independent attempts share one control session. The check handler runs
at the select stage, so an engaged interlock is reported before an operator
can reserve the object rather than after. `select()` returns a
`ControlOutcome` like `operate()` does: it is truthy exactly when the
selection succeeded. A normal-security selection is a read of `SBO`, which
carries no add cause, so a refused one reads `unknown`; the enhanced-security
models report the cause in a LastApplError.

A control model the SCL declares takes precedence over the one passed to
`on_control`, so the recipe loads the CID with `GGIO1.SPCSO1` declared as
`sbo-with-normal-security`.

    1. interlock engaged -> select is refused; an operate without a
       selection fails and the server reports why in its LastApplError
    2. interlock cleared, select then cancel -- releases the reservation
       without operating
    3. select succeeds again and operate flips the switch

    python 06_control_select_operate.py

Expected stdout:

    attempt 1 - interlock engaged
      select  -> success=False  add_cause=unknown
      operate -> success=False  add_cause=object-not-selected
      LastApplError: error=no-error  ctl_num=1  add_cause=object-not-selected

    attempt 2 - cancel after select
      select  -> success=True  add_cause=None
      cancel  -> success=True

    attempt 3 - select and operate
      select  -> success=True  add_cause=None
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

    server.on_control(
        DEMO.srv_switch, ctl_model="sbo-normal", check=check, operate=operate
    )


def declare_sbo(scl: str) -> str:
    """Declare `GGIO1.SPCSO1` as select-before-operate with normal security."""
    return scl.replace(
        "<Val>direct-with-normal-security</Val>",
        "<Val>sbo-with-normal-security</Val>",
    )


def describe(outcome: iec61850.ControlOutcome) -> str:
    cause = outcome.add_cause.value if outcome.add_cause is not None else None
    return f"success={outcome.success}  add_cause={cause}"


async def attempt(
    label: str, ctrl: iec61850.ControlObjectClient, *, cancel: bool = False
) -> None:
    print(label)
    selected = await ctrl.select()
    print(f"  select  -> {describe(selected)}")
    if cancel:
        result = await ctrl.cancel(True)
        print(f"  cancel  -> success={result.success}")
        return
    outcome = await ctrl.operate(True)
    print(f"  operate -> {describe(outcome)}")
    if outcome.last_appl_error is not None:
        lae = outcome.last_appl_error
        print(
            f"  LastApplError: error={lae.error}  ctl_num={lae.ctl_num}  "
            f"add_cause={lae.add_cause.value}"
        )


async def main() -> None:
    interlock = Interlock(engaged=True)

    async with spawn_demo(
        edit_scl=declare_sbo,
        configure=lambda s: install_sbo_handler(s, interlock),
    ) as server:
        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            ctrl = conn.create_control_object(
                DEMO.switch, iec61850.ControlModel.SBO_NORMAL
            )
            ctrl.set_origin(iec61850.OriginValue(or_cat=8, or_ident=b"cookbook-06"))

            await attempt("attempt 1 - interlock engaged", ctrl)
            print()
            interlock.engaged = False
            await attempt("attempt 2 - cancel after select", ctrl, cancel=True)
            print()

            await attempt("attempt 3 - select and operate", ctrl)

            final = await conn.read_bool(f"{DEMO.switch}.stVal", iec61850.FC.ST)
            print(f"\nfinal position: {'closed' if final else 'open'}")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
