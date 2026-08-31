"""Shared fixtures for the cookbook.

`DemoPaths` collects every server-side and client-side reference of
`models/demo.cid` into one frozen dataclass — examples import the instance
`DEMO` and read attributes rather than re-assembling strings. The
`spawn_demo()` helper wraps the server boilerplate (load the CID, wire the
control blocks the CID declares, bind, async-enter, push initial values) in an
`@asynccontextmanager` so each example reads as a normal `async with`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import iec61850

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


@dataclass(frozen=True, slots=True)
class DemoPaths:
    """Every named reference the cookbook touches, in one place."""

    ied: str = "DemoIED"
    ld: str = "LD0"
    cid: Path = MODELS_DIR / "demo.cid"

    @property
    def domain(self) -> str:
        """`<IED><LD>` — the MMS domain every client-side path starts with."""
        return f"{self.ied}{self.ld}"

    # ── Server-side points ──────────────────────────────────────────────
    @property
    def srv_ind1(self) -> str:
        return f"{self.ld}/GGIO1.Ind1.stVal"

    @property
    def srv_ind2(self) -> str:
        return f"{self.ld}/GGIO1.Ind2.stVal"

    @property
    def srv_ind3(self) -> str:
        return f"{self.ld}/GGIO1.Ind3.stVal"

    @property
    def srv_ind4(self) -> str:
        return f"{self.ld}/GGIO1.Ind4.stVal"

    @property
    def srv_switch(self) -> str:
        """Control object `GGIO1.SPCSO1`, at DO level as `on_control` wants it."""
        return f"{self.ld}/GGIO1.SPCSO1"

    @property
    def srv_power(self) -> str:
        return f"{self.ld}/MMXU1.TotW.mag.f"

    @property
    def srv_freq(self) -> str:
        return f"{self.ld}/MMXU1.Hz.mag.f"

    @property
    def srv_volts(self) -> str:
        """Phase-A voltage.

        Server-side only: it is reachable through `dsMeas` and reports, but
        the Read service does not yet resolve a client reference that walks
        into an SDO (`MMXU1.PhV.phsA.cVal.mag.f`).
        """
        return f"{self.ld}/MMXU1.PhV.phsA.cVal.mag.f"

    @property
    def srv_mode(self) -> str:
        return f"{self.ld}/LLN0.Mod.stVal"

    # ── Client-side references ──────────────────────────────────────────
    @property
    def ind1(self) -> str:
        return f"{self.domain}/GGIO1.Ind1.stVal"

    @property
    def ind2(self) -> str:
        return f"{self.domain}/GGIO1.Ind2.stVal"

    @property
    def switch(self) -> str:
        return f"{self.domain}/GGIO1.SPCSO1"

    @property
    def power(self) -> str:
        return f"{self.domain}/MMXU1.TotW.mag.f"

    @property
    def freq(self) -> str:
        return f"{self.domain}/MMXU1.Hz.mag.f"

    @property
    def mode(self) -> str:
        return f"{self.domain}/LLN0.Mod.stVal"

    @property
    def vendor(self) -> str:
        return f"{self.domain}/LLN0.NamPlt.vendor"

    def urcb(self, name: str = "urcbMeas") -> str:
        return f"{self.domain}/LLN0$RP${name}"

    def brcb(self, name: str = "brcbMeas") -> str:
        return f"{self.domain}/LLN0$BR${name}"

    def lcb(self, name: str) -> str:
        return f"{self.domain}/LLN0$LG${name}"


DEMO = DemoPaths()

# Data set and report control block names as declared in `models/demo.cid`.
DS_MEAS = "LLN0$dsMeas"
DS_STATUS = "LLN0$dsStatus"
URCB_MEAS = "urcbMeas"
BRCB_MEAS = "brcbMeas"


def wire_control_blocks(server: iec61850.IedServer) -> None:
    """Register the data sets and RCBs the CID declares.

    Parsing SCL populates the model; the runtime still needs each control
    block bound to a live data set before a client can reach it, which is
    what this does for `dsMeas`, `dsStatus`, `urcbMeas`, and `brcbMeas`.
    """
    server.add_dataset(DS_MEAS, [DEMO.srv_power, DEMO.srv_freq, DEMO.srv_volts])
    server.add_dataset(
        DS_STATUS, [DEMO.srv_ind1, DEMO.srv_ind2, DEMO.srv_ind3, DEMO.srv_ind4]
    )
    server.register_urcb(
        f"{DEMO.ld}/LLN0.{URCB_MEAS}",
        dataset=DS_MEAS,
        rpt_id=f"{DEMO.domain}/LLN0$RP${URCB_MEAS}",
        trg_ops=["data_changed", "quality_changed", "integrity", "gi"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set", "data_reference"],
        buf_tm_ms=100,
        intg_pd_ms=1000,
    )
    server.register_brcb(
        f"{DEMO.ld}/LLN0.{BRCB_MEAS}",
        dataset=DS_MEAS,
        rpt_id=f"{DEMO.domain}/LLN0$BR${BRCB_MEAS}",
        trg_ops=["data_changed", "quality_changed", "gi"],
        opt_flds=[
            "seq_num",
            "time_stamp",
            "reason",
            "data_set",
            "data_reference",
            "buffer_overflow",
            "entry_id",
        ],
        buf_tm_ms=100,
        buffer_capacity=128,
    )


@asynccontextmanager
async def spawn_demo(
    *,
    bind: str = "127.0.0.1:0",
    control_blocks: bool = True,
    configure: Callable[[iec61850.IedServer], None] | None = None,
) -> AsyncIterator[iec61850.IedServer]:
    """Spin up the demonstration IED on a free port and yield it.

    `control_blocks` wires the data sets and RCBs declared in the CID.
    `configure` runs after `bind()` but before `async with server`, so the
    example can register further data sets, RCBs, or control handlers — all
    of which have to land before the server starts.
    """
    server = iec61850.IedServer.from_scl(DEMO.cid, ied_name=DEMO.ied)
    server.bind(bind)
    if control_blocks:
        wire_control_blocks(server)
    if configure is not None:
        configure(server)
    async with server:
        # Seed the cache with something plausible so the first client read
        # doesn't show defaults everywhere.
        server.update_int32(DEMO.srv_mode, 1)
        server.update_bool(DEMO.srv_ind1, True)
        server.update_float32(DEMO.srv_power, 230.5)
        server.update_float32(DEMO.srv_freq, 50.0)
        server.update_float32(DEMO.srv_volts, 11_000.0)
        yield server


__all__ = [
    "BRCB_MEAS",
    "DEMO",
    "DS_MEAS",
    "DS_STATUS",
    "DemoPaths",
    "URCB_MEAS",
    "spawn_demo",
    "wire_control_blocks",
]
