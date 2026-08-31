"""Model constants and server helpers shared by the end-to-end tests.

Every server in this test suite is hosted by this package: `models/demo.cid`
is loaded into an `IedServer` that binds an OS-assigned loopback port. No
external process, container, or third-party peer is involved.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import iec61850

DEMO_CID = Path(__file__).resolve().parents[1] / "models" / "demo.cid"

IED_NAME = "DemoIED"
LD_INST = "LD0"
DOMAIN = f"{IED_NAME}{LD_INST}"

# Server-side references: "<LD>/<LN>.<DO>.<DA>".
SRV_MODE = f"{LD_INST}/LLN0.Mod.stVal"
SRV_IND1 = f"{LD_INST}/GGIO1.Ind1.stVal"
SRV_IND2 = f"{LD_INST}/GGIO1.Ind2.stVal"
SRV_IND3 = f"{LD_INST}/GGIO1.Ind3.stVal"
SRV_IND4 = f"{LD_INST}/GGIO1.Ind4.stVal"
SRV_SWITCH = f"{LD_INST}/GGIO1.SPCSO1"
SRV_POWER = f"{LD_INST}/MMXU1.TotW.mag.f"
SRV_FREQ = f"{LD_INST}/MMXU1.Hz.mag.f"

# Client-side references: "<IED><LD>/<LN>.<DO>.<DA>".
MODE = f"{DOMAIN}/LLN0.Mod.stVal"
VENDOR = f"{DOMAIN}/LLN0.NamPlt.vendor"
IND1 = f"{DOMAIN}/GGIO1.Ind1.stVal"
IND2 = f"{DOMAIN}/GGIO1.Ind2.stVal"
SWITCH = f"{DOMAIN}/GGIO1.SPCSO1"
POWER = f"{DOMAIN}/MMXU1.TotW.mag.f"
FREQ = f"{DOMAIN}/MMXU1.Hz.mag.f"

# Control blocks, named as in the CID.
DS_MEAS = "LLN0$dsMeas"
DS_STATUS = "LLN0$dsStatus"
URCB_REF = f"{DOMAIN}/LLN0$RP$urcbMeas"
LOG_REF = f"{DOMAIN}/LLN0$LG$evlog"

# Journal fixture: 20 entries, 100 ms apart, alternating Boolean values.
JOURNAL_BASE_MS = 1_700_000_000_000
JOURNAL_STEP_MS = 100
JOURNAL_COUNT = 20
JOURNAL_LAST_MS = JOURNAL_BASE_MS + (JOURNAL_COUNT - 1) * JOURNAL_STEP_MS
JOURNAL_DATA_REF = f"{DOMAIN}/GGIO1$ST$Ind1$stVal"

# Programmatic model used where the CID cannot serve: it carries a setpoint
# under FC=SP, one of the constraints the server accepts a remote Write on.
WRITABLE_IED = "RwIED"
WRITABLE_LD = "Bay"
WRITABLE_DOMAIN = f"{WRITABLE_IED}{WRITABLE_LD}"
WRITABLE_SETPOINT = f"{WRITABLE_DOMAIN}/GGIO1.SetPt1.setVal"
WRITABLE_LABEL = f"{WRITABLE_DOMAIN}/GGIO1.Label.setSrc"


def writable_spec() -> dict[str, Any]:
    """Model spec with an Int32 setpoint and a VisibleString, both FC=SP."""
    return {
        "ied_name": WRITABLE_IED,
        "lds": [
            {
                "inst": WRITABLE_LD,
                "lns": [
                    {"lln0": True, "dos": []},
                    {
                        "class": "GGIO",
                        "inst": "1",
                        "dos": [
                            {
                                "name": "Ind1",
                                "das": [
                                    {"name": "stVal", "fc": "ST", "type": "Boolean"},
                                ],
                            },
                            {
                                "name": "SetPt1",
                                "das": [
                                    {"name": "setVal", "fc": "SP", "type": "Int32"},
                                ],
                            },
                            {
                                "name": "Label",
                                "das": [
                                    {
                                        "name": "setSrc",
                                        "fc": "SP",
                                        "type": "VisibleString",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }


def wire_control_blocks(server: iec61850.IedServer) -> None:
    """Bind the data sets and the unbuffered RCB the CID declares."""
    server.add_dataset(DS_MEAS, [SRV_POWER, SRV_FREQ])
    server.add_dataset(DS_STATUS, [SRV_IND1, SRV_IND2, SRV_IND3, SRV_IND4])
    server.register_urcb(
        f"{LD_INST}/LLN0.urcbMeas",
        dataset=DS_MEAS,
        rpt_id=URCB_REF,
        trg_ops=["data_changed", "quality_changed", "gi"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set", "data_reference"],
        buf_tm_ms=50,
    )


def seed(server: iec61850.IedServer) -> None:
    """Push the values the read-side tests assert on."""
    server.update_int32(SRV_MODE, 1)
    server.update_bool(SRV_IND1, True)
    server.update_bool(SRV_IND2, False)
    server.update_float32(SRV_POWER, 230.5)
    server.update_float32(SRV_FREQ, 50.0)


@asynccontextmanager
async def hosted_demo(
    *,
    control_blocks: bool = True,
    configure: Callable[[iec61850.IedServer], None] | None = None,
) -> AsyncIterator[iec61850.IedServer]:
    """Start a server from `models/demo.cid` on a free loopback port."""
    server = iec61850.IedServer.from_scl(DEMO_CID, ied_name=IED_NAME)
    server.bind("127.0.0.1:0")
    if control_blocks:
        wire_control_blocks(server)
    if configure is not None:
        configure(server)
    async with server:
        seed(server)
        yield server


@asynccontextmanager
async def hosted_control(
    ctl_model: str,
    *,
    blocked: bool = False,
    sbo_class: str = "operate-once",
) -> AsyncIterator[iec61850.IedServer]:
    """Start the demo IED with `GGIO1.SPCSO1` bound to one control model.

    `blocked=True` installs a check handler that always answers
    `BlockedByInterlocking`, the server-side half of a refused command.
    """

    def configure(server: iec61850.IedServer) -> None:
        def check(_path: str, _value: object, _action: dict) -> None:
            err = iec61850.IedControlError("interlock blocks operation")
            err.add_cause = "BlockedByInterlocking"
            raise err

        def operate(_path: str, value: object, _action: dict) -> None:
            server.update_bool(f"{SRV_SWITCH}.stVal", bool(value))

        server.on_control(
            SRV_SWITCH,
            ctl_model=ctl_model,
            check=check if blocked else None,
            operate=operate,
            sbo_class=sbo_class,
        )

    async with hosted_demo(control_blocks=False, configure=configure) as server:
        yield server


@asynccontextmanager
async def hosted_journal() -> AsyncIterator[iec61850.IedServer]:
    """Start the demo IED with an LCB pre-seeded with `JOURNAL_COUNT` entries."""

    def configure(server: iec61850.IedServer) -> None:
        server.register_log_control(
            f"{LD_INST}/LLN0.evlog",
            dataset=DS_STATUS,
            log_ref=f"{DOMAIN}/LLN0$GeneralLog",
            trg_ops=["data_changed"],
        )

    async with hosted_demo(configure=configure) as server:
        for i in range(JOURNAL_COUNT):
            server.log_value(
                f"{LD_INST}/LLN0.evlog",
                data_ref=JOURNAL_DATA_REF,
                value=i % 2 == 0,
                time_ms=JOURNAL_BASE_MS + i * JOURNAL_STEP_MS,
                reason_code=0x02,
            )
        yield server


@asynccontextmanager
async def hosted_writable() -> AsyncIterator[iec61850.IedServer]:
    """Start the programmatic model that exposes writable FC=SP attributes."""
    server = iec61850.IedServer.from_model_spec(writable_spec())
    server.bind("127.0.0.1:0")
    async with server:
        server.update_int32(f"{WRITABLE_LD}/GGIO1.SetPt1.setVal", 0)
        server.update_string(f"{WRITABLE_LD}/GGIO1.Label.setSrc", "initial")
        yield server
