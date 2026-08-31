"""Switching active setting groups and recording every change.

A real protection IED would carry different pickup thresholds per group;
this example shows the wire-level operations a SCADA needs to drive that
machinery: `ActSG` for immediate activation, `EditSG` + `CnfEdit` for a
staged commit.

`models/demo.cid` declares no `SettingControl`, so the recipe builds a small
model that does — the SGCB is what the setting-group services attach to.

    python 10_setting_groups.py

Expected stdout:

    initial: 3 groups, active=1
    after activation: server sees act_sg=2

    audit trail:
      activate  sg=2  conn=1
      confirm   sg=3  conn=1
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

import iec61850

IED = "SgIED"
LD = "Prot"
DOMAIN = f"{IED}{LD}"

Event = tuple[Literal["activate", "confirm"], int, int]


@dataclass
class Auditor:
    events: list[Event] = field(default_factory=list)

    def on_act_sg(self, new_sg: int, conn_id: int) -> bool:
        self.events.append(("activate", new_sg, conn_id))
        return True  # accept the activation

    def on_confirm(self, edit_sg: int, conn_id: int) -> None:
        self.events.append(("confirm", edit_sg, conn_id))


def build_spec() -> dict[str, Any]:
    """One LD whose LLN0 carries a three-group SGCB and a status point."""
    return {
        "ied_name": IED,
        "lds": [
            {
                "inst": LD,
                "lns": [
                    {
                        "lln0": True,
                        "dos": [
                            {
                                "name": "Mod",
                                "das": [
                                    {
                                        "name": "stVal",
                                        "fc": "ST",
                                        "type": "Enumerated",
                                        "value": {"type": "int", "value": 1},
                                    },
                                ],
                            },
                        ],
                        "sgcb": {"num_of_sg": 3, "act_sg": 1},
                    },
                    {
                        "class": "GGIO",
                        "inst": "1",
                        "dos": [
                            {
                                "name": "Ind1",
                                "das": [{"name": "stVal", "fc": "ST", "type": "Boolean"}],
                            },
                        ],
                    },
                ],
            },
        ],
    }


async def main() -> None:
    auditor = Auditor()

    server = iec61850.IedServer.from_model_spec(build_spec())
    server.bind("127.0.0.1:0")
    server.register_setting_group_handler(
        LD,
        on_act_sg=auditor.on_act_sg,
        on_confirm=auditor.on_confirm,
    )

    async with server:
        info = server.get_setting_group_info(LD)
        print(f"initial: {info['num_of_sg']} groups, active={info['act_sg']}")

        conn = await iec61850.IedConnection.connect(server.bound_addr)
        try:
            # Activate group 2 immediately.
            await conn.write(f"{DOMAIN}/LLN0.SGCB.ActSG", iec61850.FC.SP, 2)
            await asyncio.sleep(0.05)
            active = server.get_setting_group_info(LD)["act_sg"]
            print(f"after activation: server sees act_sg={active}")

            # Stage edits on group 3 then commit.
            await conn.write(f"{DOMAIN}/LLN0.SGCB.EditSG", iec61850.FC.SP, 3)
            await conn.write(f"{DOMAIN}/LLN0.SGCB.CnfEdit", iec61850.FC.SP, True)
            await asyncio.sleep(0.05)
        finally:
            await conn.disconnect()

        print("\naudit trail:")
        for kind, sg, conn_id in auditor.events:
            print(f"  {kind:9s} sg={sg}  conn={conn_id}")


if __name__ == "__main__":
    asyncio.run(main())
