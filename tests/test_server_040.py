"""Server API end to end: each case starts a server hosted by this package and
drives it from the in-package client over TCP."""

from __future__ import annotations

import pytest

import _demo
from iec61850 import (
    ControlModel,
    IedConnection,
    IedDataAccessError,
    IedServer,
    IedServerError,
)

URCB_MEAS = f"{_demo.DOMAIN}/LLN0$RP$urcbMeas"
BRCB_MEAS = f"{_demo.DOMAIN}/LLN0$BR$brcbMeas"


def _demo_server(*, indexed: bool = False) -> IedServer:
    """The demonstration IED on a free loopback port, its RCBs optionally
    indexed."""
    text = _demo.DEMO_CID.read_text(encoding="utf-8")
    if indexed:
        text = text.replace('indexed="false"', 'indexed="true"')
    server = IedServer.from_scl_str(text, ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    return server


# ── register_from_model ─────────────────────────────────────────────────────


async def test_model_control_blocks_are_not_served_without_register_from_model() -> None:
    server = _demo_server()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(URCB_MEAS)
            assert server.connection_count == 1
        finally:
            await conn.disconnect()


async def test_register_from_model_while_running_returns_the_summary() -> None:
    server = _demo_server()
    async with server:
        summary = server.register_from_model()
        assert summary == {
            "data_sets": 2,
            "urcbs": 1,
            "brcbs": 1,
            "gocbs": 1,
            "lcbs": 0,
            "sgcbs": 0,
            "control_objects": 1,
        }
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MEAS)
            assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
            brcb = await conn.get_rcb_values(BRCB_MEAS)
            assert brcb.is_buffered
        finally:
            await conn.disconnect()


async def test_register_from_model_names_indexed_instances_with_two_digits() -> None:
    server = _demo_server(indexed=True)
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["urcbs"] == 2
        assert summary["brcbs"] == 2
        conn = await IedConnection.connect(server.bound_addr)
        try:
            for name in ("urcbMeas01", "urcbMeas02"):
                rcb = await conn.get_rcb_values(f"{_demo.DOMAIN}/LLN0$RP${name}")
                assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(URCB_MEAS)
        finally:
            await conn.disconnect()


async def test_register_from_model_twice_raises_with_the_reference() -> None:
    server = _demo_server()
    async with server:
        assert server.register_from_model() is not None
        with pytest.raises(IedServerError) as info:
            server.register_from_model()
        assert info.value.reference is not None
        assert info.value.reference.startswith(f"{_demo.DOMAIN}/")


async def test_register_from_model_queued_before_start_runs_at_start() -> None:
    server = _demo_server()
    assert server.register_from_model() is None
    with pytest.raises(RuntimeError, match="already queued"):
        server.register_from_model()
    async with server:
        conn = await IedConnection.connect(server.bound_addr)
        try:
            rcb = await conn.get_rcb_values(URCB_MEAS)
            assert rcb.dataset == f"{_demo.DOMAIN}/LLN0$dsMeas"
        finally:
            await conn.disconnect()


async def test_queued_register_from_model_conflicting_with_add_dataset_fails_start() -> None:
    server = _demo_server()
    server.add_dataset(_demo.DS_MEAS, [_demo.SRV_POWER])
    server.register_from_model()
    with pytest.raises(IedServerError) as info:
        await server.start()
    assert info.value.reference == f"{_demo.DOMAIN}/{_demo.DS_MEAS}"
    assert not server.is_running


async def test_register_from_model_after_explicit_urcb_raises_while_running() -> None:
    server = _demo_server()
    _demo.wire_control_blocks(server)
    async with server:
        with pytest.raises(IedServerError) as info:
            server.register_from_model()
        assert info.value.reference is not None
        conn = await IedConnection.connect(server.bound_addr)
        try:
            # Nothing of the model was registered: the BRCB stays absent.
            with pytest.raises(IedDataAccessError):
                await conn.get_rcb_values(BRCB_MEAS)
            assert (await conn.get_rcb_values(URCB_MEAS)).dataset is not None
        finally:
            await conn.disconnect()


async def test_register_from_model_keeps_the_on_control_handlers() -> None:
    server = _demo_server()
    operated: list[object] = []

    def operate(_path: str, value: object, _action: dict) -> None:
        operated.append(value)
        server.update_bool(f"{_demo.SRV_SWITCH}.stVal", bool(value))

    server.on_control(_demo.SRV_SWITCH, ctl_model="direct-normal", operate=operate)
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["control_objects"] == 0
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(_demo.SWITCH, ControlModel.DIRECT_NORMAL)
            outcome = await spc.operate(True)
            assert outcome.success is True
            assert operated == [True]
        finally:
            await conn.disconnect()


async def test_on_control_after_register_from_model_replaces_the_entry() -> None:
    server = _demo_server()
    operated: list[object] = []
    async with server:
        summary = server.register_from_model()
        assert summary is not None
        assert summary["control_objects"] == 1
        server.on_control(
            _demo.SRV_SWITCH,
            ctl_model="direct-normal",
            operate=lambda _p, v, _a: operated.append(v),
        )
        conn = await IedConnection.connect(server.bound_addr)
        try:
            spc = conn.create_control_object(_demo.SWITCH, ControlModel.DIRECT_NORMAL)
            outcome = await spc.operate(True)
            assert outcome.success is True
            assert operated == [True]
        finally:
            await conn.disconnect()


async def test_register_from_model_after_stop_raises() -> None:
    server = _demo_server()
    async with server:
        pass
    with pytest.raises(RuntimeError, match="stopped"):
        server.register_from_model()
