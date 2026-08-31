"""IedServer hosting — offline + e2e against the in-package client."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iec61850 import FC, IedConnection, IedError, IedServer, IedServerError

# Synthetic SCL covering a Boolean, a Float32, an Enum, and a constructed DA.
SERVER_SCL = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0"/>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1"/>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="ENC_1"/>
    </LNodeType>
    <LNodeType id="GGIO_1" lnClass="GGIO">
      <DO name="Ind1" type="SPS_1"/>
      <DO name="AnIn1" type="MV_1"/>
      <DO name="SetPt1" type="ING_1"/>
    </LNodeType>
    <DOType id="ENC_1" cdc="ENC">
      <DA name="stVal" fc="ST" bType="Enum" type="BehaviourKind"/>
      <DA name="q" fc="ST" bType="Quality"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN"/>
      <DA name="q" fc="ST" bType="Quality"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <DOType id="MV_1" cdc="MV">
      <DA name="mag" fc="MX" bType="Struct" type="AnalogueValue"/>
      <DA name="q" fc="MX" bType="Quality"/>
      <DA name="t" fc="MX" bType="Timestamp"/>
    </DOType>
    <DOType id="ING_1" cdc="ING">
      <DA name="setVal" fc="SP" bType="INT32"/>
    </DOType>
    <DAType id="AnalogueValue">
      <BDA name="f" bType="FLOAT32"/>
    </DAType>
    <EnumType id="BehaviourKind">
      <EnumVal ord="1">on</EnumVal>
      <EnumVal ord="2">blocked</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""


def _write_scl(tmp_path: Path) -> Path:
    p = tmp_path / "server.icd"
    p.write_text(SERVER_SCL, encoding="utf-8")
    return p


# SCL fixture whose LLN0 carries a setting group control block.
SERVER_SCL_WITH_SGCB = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
            <SettingControl numOfSGs="3" actSG="1" resvTms="30"/>
          </LN0>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1"/>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="ENC_1"/>
    </LNodeType>
    <LNodeType id="GGIO_1" lnClass="GGIO">
      <DO name="Ind1" type="SPS_1"/>
    </LNodeType>
    <DOType id="ENC_1" cdc="ENC">
      <DA name="stVal" fc="ST" bType="Enum" type="BehaviourKind"/>
      <DA name="q" fc="ST" bType="Quality"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN"/>
      <DA name="q" fc="ST" bType="Quality"/>
      <DA name="t" fc="ST" bType="Timestamp"/>
    </DOType>
    <EnumType id="BehaviourKind">
      <EnumVal ord="1">on</EnumVal>
      <EnumVal ord="2">blocked</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""


# ── Construction / config / repr ────────────────────────────────────────────


def test_from_scl_str_returns_handle() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    assert "not-started" in repr(s)
    assert not s.is_running


def test_from_scl_file(tmp_path: Path) -> None:
    p = _write_scl(tmp_path)
    s = IedServer.from_scl(str(p), ied_name="IED1")
    assert not s.is_running
    # PathLike form too
    s2 = IedServer.from_scl(p, ied_name="IED1")
    assert not s2.is_running


def test_from_scl_unknown_ied_raises() -> None:
    from iec61850 import SclError

    with pytest.raises(SclError):
        IedServer.from_scl_str(SERVER_SCL, ied_name="DOES_NOT_EXIST")


def test_bind_invalid_address_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError):
        s.bind("not-an-address")


def test_ied_server_error_is_ied_error_subclass() -> None:
    assert issubclass(IedServerError, IedError)


# ── Lifecycle ───────────────────────────────────────────────────────────────


async def test_start_without_bind_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(RuntimeError, match="bind"):
        await s.start()


async def test_start_stop_cycle() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    await s.start()
    assert s.is_running
    host, port = s.bound_addr.rsplit(":", 1)
    assert host == "127.0.0.1"
    assert int(port) > 0
    assert s.connection_count == 0
    await s.stop()
    assert not s.is_running
    assert "stopped" in repr(s)


async def test_double_start_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    await s.start()
    try:
        with pytest.raises(RuntimeError, match="already been started"):
            await s.start()
    finally:
        await s.stop()


async def test_stop_before_start_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(RuntimeError, match="not running"):
        await s.stop()


async def test_async_context_manager() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s as entered:
        assert entered is s
        assert s.is_running
    assert not s.is_running


# ── Property setters reject post-start ──────────────────────────────────────


async def test_config_setters_locked_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.vendor = "ACME"
    s.model_name = "Demo"
    s.revision = "1.0"
    s.max_connections = 4
    async with s:
        with pytest.raises(RuntimeError):
            s.vendor = "Other"
        with pytest.raises(RuntimeError):
            s.max_connections = 8
        with pytest.raises(RuntimeError):
            s.bind("127.0.0.1:1")


# ── update_* methods ────────────────────────────────────────────────────────


async def test_update_before_start_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(RuntimeError, match="not running"):
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)


async def test_update_unknown_path_raises_key_error() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(KeyError):
            s.update_bool("NoSuchPath", True)


async def test_update_type_mismatch_raises_data_access_error() -> None:
    from iec61850 import IedDataAccessError

    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        # GGIO1.Ind1.stVal is BOOLEAN — pushing float32 should fail.
        with pytest.raises(IedDataAccessError):
            s.update_float32("GenericIO/GGIO1.Ind1.stVal", 1.5)


# ── End-to-end against in-package client ────────────────────────────────────


async def test_client_reads_updated_value() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
        s.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 42.5)

        conn = await IedConnection.connect(s.bound_addr)
        try:
            ind = await conn.read_bool("IED1GenericIO/GGIO1.Ind1.stVal", FC.ST)
            assert ind is True
            mag = await conn.read_float("IED1GenericIO/GGIO1.AnIn1.mag.f", FC.MX)
            assert abs(mag - 42.5) < 1e-4
        finally:
            await conn.disconnect()


async def test_connection_count_reflects_clients() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        assert s.connection_count == 0
        conn = await IedConnection.connect(s.bound_addr)
        try:
            # accept loop may need a tick to register
            for _ in range(20):
                if s.connection_count >= 1:
                    break
                await asyncio.sleep(0.05)
            assert s.connection_count >= 1
        finally:
            await conn.disconnect()


# ── Handler surface (on_read / on_write) ────────────────────────────────────


def test_on_read_rejects_non_callable() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="callable"):
        s.on_read("GenericIO/GGIO1.Ind1.stVal", 42)


def test_on_read_rejects_unknown_path() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(KeyError):
        s.on_read("Nope/X.Y.z", lambda path: None)


async def test_on_read_overrides_cache() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")

    calls: list[str] = []

    def reader(path: str) -> bool:
        calls.append(path)
        return True

    s.on_read("GenericIO/GGIO1.Ind1.stVal", reader)

    async with s:
        # Cache says False (default); handler overrides to True.
        conn = await IedConnection.connect(s.bound_addr)
        try:
            val = await conn.read_bool("IED1GenericIO/GGIO1.Ind1.stVal", FC.ST)
            assert val is True
        finally:
            await conn.disconnect()
    assert calls and calls[0] == "GenericIO/GGIO1.Ind1.stVal"


async def test_on_read_none_falls_through_to_cache() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.on_read("GenericIO/GGIO1.Ind1.stVal", lambda _path: None)

    async with s:
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
        conn = await IedConnection.connect(s.bound_addr)
        try:
            val = await conn.read_bool("IED1GenericIO/GGIO1.Ind1.stVal", FC.ST)
            assert val is True
        finally:
            await conn.disconnect()


async def test_on_read_exception_maps_to_data_access_error() -> None:
    from iec61850 import IedDataAccessError

    def boom(_path: str) -> None:
        err = IedDataAccessError("hardware down")
        err.code = "HardwareFault"
        raise err

    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.on_read("GenericIO/GGIO1.Ind1.stVal", boom)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.read_bool("IED1GenericIO/GGIO1.Ind1.stVal", FC.ST)
        finally:
            await conn.disconnect()


async def test_on_read_can_register_while_running() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        s.on_read("GenericIO/GGIO1.Ind1.stVal", lambda _p: True)
        conn = await IedConnection.connect(s.bound_addr)
        try:
            assert await conn.read_bool(
                "IED1GenericIO/GGIO1.Ind1.stVal", FC.ST
            ) is True
        finally:
            await conn.disconnect()


def test_on_write_rejects_non_callable() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="callable"):
        s.on_write("GenericIO/GGIO1.Ind1.stVal", "not a function")


async def test_on_write_accepts_and_updates_cache() -> None:
    seen: list[tuple[str, object]] = []

    def writer(path: str, value: object) -> bool:
        seen.append((path, value))
        return True

    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.on_write("GenericIO/GGIO1.SetPt1.setVal", writer)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP, 42)
            assert (
                await conn.read_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP)
                == 42
            )
        finally:
            await conn.disconnect()
    assert seen == [("GenericIO/GGIO1.SetPt1.setVal", 42)]


async def test_on_write_reject_raises_on_client() -> None:
    from iec61850 import IedDataAccessError

    def deny(_path: str, _value: object) -> bool:
        err = IedDataAccessError("read only")
        err.code = "ObjectAccessDenied"
        raise err

    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.on_write("GenericIO/GGIO1.SetPt1.setVal", deny)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.write_int32(
                    "IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP, 99
                )
            # Cache should still be the default (0)
            assert (
                await conn.read_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP)
                == 0
            )
        finally:
            await conn.disconnect()


async def test_on_write_accept_no_update_skips_cache() -> None:
    def shadow(_path: str, _value: object) -> None:
        # None → AcceptNoUpdate (handler manages cache externally)
        return None

    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.on_write("GenericIO/GGIO1.SetPt1.setVal", shadow)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP, 7)
            # Write succeeded from the client's perspective, but cache untouched
            assert (
                await conn.read_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP)
                == 0
            )
        finally:
            await conn.disconnect()


# ── Control handler surface (on_control) ────────────────────────────────────

# `models/demo.cid` carries the one controllable point these cases need,
# `GGIO1.SPCSO1`, so the control surface is exercised against the same model
# the cookbook uses.
DEMO_CID = Path(__file__).resolve().parents[1] / "models" / "demo.cid"
DEMO_IED = "DemoIED"
DEMO_DOMAIN = "DemoIEDLD0"
DEMO_SPC = "LD0/GGIO1.SPCSO1"


def _demo_server() -> IedServer:
    return IedServer.from_scl(DEMO_CID, ied_name=DEMO_IED)


def test_on_control_rejects_invalid_ctl_model() -> None:
    s = _demo_server()
    with pytest.raises(ValueError, match="ctl_model"):
        s.on_control(
            DEMO_SPC,
            ctl_model="bogus",
            operate=lambda _p, _v, _a: None,
        )


def test_on_control_rejects_missing_handlers() -> None:
    s = _demo_server()
    with pytest.raises(ValueError, match="at least one"):
        s.on_control(DEMO_SPC, ctl_model="direct-normal")


def test_on_control_rejects_non_callable() -> None:
    s = _demo_server()
    with pytest.raises(ValueError, match="callable"):
        s.on_control(
            DEMO_SPC, ctl_model="direct-normal", operate="not callable"
        )


def test_on_control_rejects_unknown_ld() -> None:
    s = _demo_server()
    with pytest.raises(KeyError):
        s.on_control(
            "NoSuchLD/GGIO1.SPCSO1",
            ctl_model="direct-normal",
            operate=lambda _p, _v, _a: None,
        )


def test_on_control_rejects_path_pointing_at_da() -> None:
    s = _demo_server()
    with pytest.raises(ValueError, match="DO"):
        s.on_control(
            f"{DEMO_SPC}.stVal",
            ctl_model="direct-normal",
            operate=lambda _p, _v, _a: None,
        )


async def test_on_control_direct_normal_sync_operate() -> None:
    from iec61850 import ControlModel

    seen: list[tuple[str, object, dict]] = []

    def operate(path: str, value: object, action: dict) -> None:
        seen.append((path, value, action))

    s = _demo_server()
    s.bind("127.0.0.1:0")
    s.on_control(DEMO_SPC, ctl_model="direct-normal", operate=operate)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            ctrl = conn.create_control_object(
                f"{DEMO_DOMAIN}/GGIO1.SPCSO1", ControlModel.DIRECT_NORMAL
            )
            outcome = await ctrl.operate(True)
            assert outcome.success
        finally:
            await conn.disconnect()

    assert len(seen) == 1
    path, value, action = seen[0]
    assert path == DEMO_SPC
    assert value is True
    assert set(action.keys()) >= {
        "ctl_num",
        "test",
        "synchro_check",
        "interlock_check",
        "is_select",
        "ctl_time_ms",
        "origin",
    }
    assert "or_cat" in action["origin"]


async def test_on_control_async_operate() -> None:
    from iec61850 import ControlModel

    completed = asyncio.Event()

    async def operate(_path: str, _value: object, _action: dict) -> None:
        await asyncio.sleep(0)
        completed.set()

    s = _demo_server()
    s.bind("127.0.0.1:0")
    s.on_control(DEMO_SPC, ctl_model="direct-normal", operate=operate)

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            ctrl = conn.create_control_object(
                f"{DEMO_DOMAIN}/GGIO1.SPCSO1", ControlModel.DIRECT_NORMAL
            )
            outcome = await ctrl.operate(True)
            assert outcome.success
        finally:
            await conn.disconnect()

    assert completed.is_set()


async def test_on_control_check_reject_maps_to_add_cause() -> None:
    from iec61850 import ControlModel, IedControlError

    def deny(_path: str, _value: object, _action: dict) -> None:
        err = IedControlError("interlock")
        err.add_cause = "BlockedByInterlocking"
        raise err

    s = _demo_server()
    s.bind("127.0.0.1:0")
    s.on_control(
        DEMO_SPC,
        ctl_model="direct-normal",
        check=deny,
        operate=lambda _p, _v, _a: None,
    )

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            ctrl = conn.create_control_object(
                f"{DEMO_DOMAIN}/GGIO1.SPCSO1", ControlModel.DIRECT_NORMAL
            )
            outcome = await ctrl.operate(True)
            assert not outcome.success
        finally:
            await conn.disconnect()


async def test_on_control_async_reject_maps_to_add_cause() -> None:
    from iec61850 import ControlModel, IedControlError

    async def deny(_path: str, _value: object, _action: dict) -> None:
        await asyncio.sleep(0)
        err = IedControlError("hw fault")
        err.add_cause = "BlockedByProcess"
        raise err

    s = _demo_server()
    s.bind("127.0.0.1:0")
    s.on_control(
        DEMO_SPC, ctl_model="direct-normal", operate=deny
    )

    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            ctrl = conn.create_control_object(
                f"{DEMO_DOMAIN}/GGIO1.SPCSO1", ControlModel.DIRECT_NORMAL
            )
            outcome = await ctrl.operate(True)
            assert not outcome.success
        finally:
            await conn.disconnect()


async def test_on_control_can_register_while_running() -> None:
    from iec61850 import ControlModel

    called = asyncio.Event()

    def op(_p: str, _v: object, _a: dict) -> None:
        called.set()

    s = _demo_server()
    s.bind("127.0.0.1:0")
    async with s:
        s.on_control(
            DEMO_SPC, ctl_model="direct-normal", operate=op
        )
        conn = await IedConnection.connect(s.bound_addr)
        try:
            ctrl = conn.create_control_object(
                f"{DEMO_DOMAIN}/GGIO1.SPCSO1", ControlModel.DIRECT_NORMAL
            )
            outcome = await ctrl.operate(True)
            assert outcome.success
        finally:
            await conn.disconnect()
    assert called.is_set()


# ── Dataset / URCB / batch (reporting surface) ──────────────────────────────


def test_add_dataset_rejects_empty_paths() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="at least one"):
        s.add_dataset("GGIO1$ds1", [])


def test_add_dataset_rejects_empty_name() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="name must not be empty"):
        s.add_dataset("", ["GenericIO/GGIO1.Ind1.stVal"])


def test_add_dataset_rejects_unknown_path() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(KeyError):
        s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.NopeDO.x"])


def test_add_dataset_duplicate_name_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="already registered"):
        s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.AnIn1.mag.f"])


def test_register_urcb_rejects_unknown_dataset() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(KeyError, match="not registered"):
        s.register_urcb("GenericIO/LLN0.urcb01", dataset="GGIO1$ds1")


def test_register_urcb_rejects_unknown_ld() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(KeyError):
        s.register_urcb("NoSuchLD/LLN0.urcb01", dataset="GGIO1$ds1")


def test_register_urcb_unknown_trg_ops_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="trg_ops"):
        s.register_urcb(
            "GenericIO/LLN0.urcb01",
            dataset="GGIO1$ds1",
            trg_ops=["data_changed", "no_such_flag"],
        )


def test_register_urcb_unknown_opt_flds_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="opt_flds"):
        s.register_urcb(
            "GenericIO/LLN0.urcb01",
            dataset="GGIO1$ds1",
            opt_flds=["seq_num", "no_such_flag"],
        )


def test_register_urcb_duplicate_path_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    s.register_urcb("GenericIO/LLN0.urcb01", dataset="GGIO1$ds1")
    with pytest.raises(ValueError, match="already registered"):
        s.register_urcb("GenericIO/LLN0.urcb01", dataset="GGIO1$ds1")


async def test_add_dataset_rejects_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(RuntimeError, match="before start"):
            s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])


async def test_register_urcb_rejects_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(RuntimeError, match="before start"):
            s.register_urcb("GenericIO/LLN0.urcb01", dataset="GGIO1$ds1")


async def test_dataset_and_urcb_survive_start() -> None:
    """End-to-end: register dataset + URCB, start, push updates, stop cleanly."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset(
        "GGIO1$ds1",
        [
            "GenericIO/GGIO1.Ind1.stVal",
            "GenericIO/GGIO1.AnIn1.mag.f",
        ],
    )
    s.register_urcb(
        "GenericIO/LLN0.urcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed", "gi"],
        opt_flds=["seq_num", "time_stamp", "reason", "data_set"],
        buf_tm_ms=50,
    )
    async with s:
        # Updates should fire reports through the engine without panicking.
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
        s.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 7.25)
        # Client can still read the underlying values back.
        conn = await IedConnection.connect(s.bound_addr)
        try:
            assert await conn.read_bool(
                "IED1GenericIO/GGIO1.Ind1.stVal", FC.ST
            ) is True
        finally:
            await conn.disconnect()


async def test_batch_serializes_updates() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with s.batch():
            s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
            s.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 1.5)
        conn = await IedConnection.connect(s.bound_addr)
        try:
            assert await conn.read_bool(
                "IED1GenericIO/GGIO1.Ind1.stVal", FC.ST
            ) is True
            mag = await conn.read_float(
                "IED1GenericIO/GGIO1.AnIn1.mag.f", FC.MX
            )
            assert abs(mag - 1.5) < 1e-4
        finally:
            await conn.disconnect()


async def test_batch_nested_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with s.batch():
            with pytest.raises(RuntimeError):
                with s.batch():
                    pass


def test_batch_before_start_raises() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(RuntimeError, match="not running"):
        s.batch()


# ── BRCB (buffered reporting surface) ────────────────────────────────────────


def test_register_brcb_rejects_unknown_dataset() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(KeyError, match="not registered"):
        s.register_brcb("GenericIO/LLN0.brcb01", dataset="GGIO1$ds1")


def test_register_brcb_rejects_unknown_ld() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(KeyError):
        s.register_brcb("NoSuchLD/LLN0.brcb01", dataset="GGIO1$ds1")


def test_register_brcb_unknown_trg_ops_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="trg_ops"):
        s.register_brcb(
            "GenericIO/LLN0.brcb01",
            dataset="GGIO1$ds1",
            trg_ops=["data_changed", "no_such_flag"],
        )


def test_register_brcb_unknown_opt_flds_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="opt_flds"):
        s.register_brcb(
            "GenericIO/LLN0.brcb01",
            dataset="GGIO1$ds1",
            opt_flds=["seq_num", "no_such_flag"],
        )


def test_register_brcb_duplicate_path_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    s.register_brcb("GenericIO/LLN0.brcb01", dataset="GGIO1$ds1")
    with pytest.raises(ValueError, match="already registered"):
        s.register_brcb("GenericIO/LLN0.brcb01", dataset="GGIO1$ds1")


def test_register_brcb_buffer_capacity_zero_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    with pytest.raises(ValueError, match="buffer_capacity"):
        s.register_brcb(
            "GenericIO/LLN0.brcb01",
            dataset="GGIO1$ds1",
            buffer_capacity=0,
        )


async def test_register_brcb_rejects_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(RuntimeError, match="before start"):
            s.register_brcb("GenericIO/LLN0.brcb01", dataset="GGIO1$ds1")


async def test_dataset_and_brcb_survive_start() -> None:
    """End-to-end: register dataset + BRCB, start, push updates, stop cleanly."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset(
        "GGIO1$ds1",
        [
            "GenericIO/GGIO1.Ind1.stVal",
            "GenericIO/GGIO1.AnIn1.mag.f",
        ],
    )
    s.register_brcb(
        "GenericIO/LLN0.brcb01",
        dataset="GGIO1$ds1",
        trg_ops=["data_changed", "gi"],
        opt_flds=[
            "seq_num",
            "time_stamp",
            "reason",
            "data_set",
            "buffer_overflow",
            "entry_id",
        ],
        buf_tm_ms=50,
        buffer_capacity=128,
    )
    async with s:
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
        s.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 7.25)
        conn = await IedConnection.connect(s.bound_addr)
        try:
            assert await conn.read_bool(
                "IED1GenericIO/GGIO1.Ind1.stVal", FC.ST
            ) is True
        finally:
            await conn.disconnect()


async def test_urcb_and_brcb_share_dataset() -> None:
    """A single dataset can back both URCB and BRCB registrations."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.add_dataset("GGIO1$ds1", ["GenericIO/GGIO1.Ind1.stVal"])
    s.register_urcb("GenericIO/LLN0.urcb01", dataset="GGIO1$ds1")
    s.register_brcb("GenericIO/LLN0.brcb01", dataset="GGIO1$ds1")
    async with s:
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)


# ── TLS server hosting (IEC 62351-3) ─────────────────────────────────────────


def test_with_tls_rejects_empty_cert() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="server_cert_pem"):
        s.with_tls(server_cert_pem=b"", server_key_pem=b"placeholder")


def test_with_tls_rejects_empty_key() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="server_key_pem"):
        s.with_tls(server_cert_pem=b"placeholder", server_key_pem=b"")


def test_with_tls_invalid_version_token_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="invalid TLS version"):
        s.with_tls(
            server_cert_pem=b"placeholder",
            server_key_pem=b"placeholder",
            min_tls_version="tls1.0",
        )


def test_with_tls_double_config_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.with_tls(server_cert_pem=b"placeholder", server_key_pem=b"placeholder")
    with pytest.raises(RuntimeError, match="already been configured"):
        s.with_tls(server_cert_pem=b"placeholder", server_key_pem=b"placeholder")


async def test_with_tls_rejects_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(RuntimeError, match="before start"):
            s.with_tls(server_cert_pem=b"placeholder", server_key_pem=b"placeholder")


async def test_start_with_bad_tls_pem_surfaces_error() -> None:
    """``with_tls`` queues PEM bytes; the actual parse happens at start()."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.with_tls(
        server_cert_pem=b"not a real pem",
        server_key_pem=b"not a real pem either",
    )
    with pytest.raises(ValueError, match="server cert/key|TLS server"):
        async with s:
            pass


# ── LCB (Log Control Block) ──────────────────────────────────────────────────


def test_register_log_control_rejects_unknown_ld() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(KeyError):
        s.register_log_control("NoSuchLD/LLN0.lcb01", dataset="LLN0$evlogds")


def test_register_log_control_unknown_trg_ops_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="trg_ops"):
        s.register_log_control(
            "GenericIO/LLN0.lcb01",
            dataset="LLN0$evlogds",
            trg_ops=["data_changed", "no_such_flag"],
        )


def test_register_log_control_duplicate_path_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.register_log_control("GenericIO/LLN0.lcb01", dataset="LLN0$evlogds")
    with pytest.raises(ValueError, match="already registered"):
        s.register_log_control("GenericIO/LLN0.lcb01", dataset="LLN0$evlogds")


def test_register_log_control_storage_capacity_zero_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    with pytest.raises(ValueError, match="storage_capacity"):
        s.register_log_control(
            "GenericIO/LLN0.lcb01",
            dataset="LLN0$evlogds",
            storage_capacity=0,
        )


async def test_register_log_control_rejects_after_start() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(RuntimeError, match="before start"):
            s.register_log_control("GenericIO/LLN0.lcb01", dataset="LLN0$evlogds")


def test_set_log_ena_before_start_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.register_log_control("GenericIO/LLN0.lcb01", dataset="LLN0$evlogds")
    with pytest.raises(RuntimeError, match="not running"):
        s.set_log_ena("GenericIO/LLN0.lcb01", True)


def test_log_value_before_start_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.register_log_control("GenericIO/LLN0.lcb01", dataset="LLN0$evlogds")
    with pytest.raises(RuntimeError, match="not running"):
        s.log_value(
            "GenericIO/LLN0.lcb01",
            data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
            value=True,
        )


async def test_log_value_unknown_lcb_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(KeyError, match="not registered"):
            s.log_value(
                "GenericIO/LLN0.lcb01",
                data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
                value=True,
            )


async def test_log_value_persists_and_query_returns_entries() -> None:
    """End-to-end: register LCB, push journal entries, query them back via the
    in-package client. Verifies the LogControl registry is wired into the MMS
    dispatcher and ReadJournal routing works for Python-registered blocks."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_log_control(
        "GenericIO/LLN0.lcb01",
        dataset="LLN0$evlogds",
        log_ref="IED1GenericIO/LLN0$GeneralLog",
        trg_ops=["data_changed"],
    )
    log_ref = "IED1GenericIO/LLN0$LG$lcb01"
    base = 1_700_000_000_000
    async with s:
        for i in range(5):
            entry_id = s.log_value(
                "GenericIO/LLN0.lcb01",
                data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
                value=(i % 2 == 0),
                time_ms=base + i * 10,
                reason_code=0x02,
            )
            assert isinstance(entry_id, int) and entry_id >= 1
        conn = await IedConnection.connect(s.bound_addr)
        try:
            entries, more = await conn.query_journal_by_time(
                log_ref, base, base + 4 * 10
            )
            assert more is False
            assert [e.time_ms for e in entries] == [base + i * 10 for i in range(5)]
            assert all(
                v.data_ref == "IED1GenericIO/GGIO1$ST$Ind1$stVal"
                for e in entries for v in e.variables
            )
            assert [e.variables[0].value for e in entries] == [
                True, False, True, False, True
            ]
            assert all(v.reason_code == 0x02 for e in entries for v in e.variables)
        finally:
            await conn.disconnect()


async def test_log_value_disabled_lcb_returns_none() -> None:
    """`default_enabled=False` → `log_single_value` skips the write and returns
    `None` until `set_log_ena(True)` is called."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_log_control(
        "GenericIO/LLN0.lcb01",
        dataset="LLN0$evlogds",
        default_enabled=False,
    )
    async with s:
        result = s.log_value(
            "GenericIO/LLN0.lcb01",
            data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
            value=True,
            time_ms=1_700_000_000_000,
        )
        assert result is None
        # Enable, then a fresh log_value records and returns the new id.
        s.set_log_ena("GenericIO/LLN0.lcb01", True)
        recorded = s.log_value(
            "GenericIO/LLN0.lcb01",
            data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
            value=True,
            time_ms=1_700_000_000_100,
        )
        assert isinstance(recorded, int)


async def test_log_value_storage_capacity_evicts_oldest() -> None:
    """Bounded ring buffer drops the oldest entry on overflow (mirrors BRCB)."""
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_log_control(
        "GenericIO/LLN0.lcb01",
        dataset="LLN0$evlogds",
        storage_capacity=3,
    )
    log_ref = "IED1GenericIO/LLN0$LG$lcb01"
    base = 1_700_000_000_000
    async with s:
        for i in range(5):
            s.log_value(
                "GenericIO/LLN0.lcb01",
                data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
                value=True,
                time_ms=base + i,
            )
        conn = await IedConnection.connect(s.bound_addr)
        try:
            entries, _ = await conn.query_journal_by_time(
                log_ref, base, base + 100
            )
            # Only the last 3 survive; earliest two were evicted.
            assert [e.time_ms for e in entries] == [base + 2, base + 3, base + 4]
        finally:
            await conn.disconnect()


# ── SGCB (Setting Group Control Block) ──────────────────────────────────────


def test_setting_group_info_before_start_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    with pytest.raises(RuntimeError, match="not running"):
        s.get_setting_group_info("GenericIO")


def test_force_active_setting_group_before_start_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    with pytest.raises(RuntimeError, match="not running"):
        s.force_active_setting_group("GenericIO", 2)


def test_register_setting_group_handler_unknown_ld_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    with pytest.raises(KeyError, match="logical device"):
        s.register_setting_group_handler("NoSuchLD", on_act_sg=lambda *_: True)


async def test_register_setting_group_handler_ld_without_sgcb_rejected() -> None:
    # The default fixture has GenericIO without `<SettingControl>`. Queue is
    # accepted (NotStarted state has no model insight); start() should surface
    # the InvalidModel error from the server registry.
    s = IedServer.from_scl_str(SERVER_SCL, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_setting_group_handler("GenericIO", on_act_sg=lambda *_: True)
    with pytest.raises(KeyError, match="no SGCB"):
        async with s:
            pass


async def test_get_setting_group_info_returns_snapshot() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        info = s.get_setting_group_info("GenericIO")
        assert info["num_of_sg"] == 3
        assert info["act_sg"] == 1
        assert info["edit_sg"] == 0
        assert info["cnf_edit"] is False
        assert info["resv_tms_s"] == 30
        assert info["last_act_tm_ms"] >= 0


async def test_force_active_setting_group_changes_act_sg() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        s.force_active_setting_group("GenericIO", 2)
        info = s.get_setting_group_info("GenericIO")
        assert info["act_sg"] == 2


async def test_force_active_setting_group_out_of_range_rejected() -> None:
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")
    async with s:
        with pytest.raises(ValueError):
            s.force_active_setting_group("GenericIO", 99)


async def test_setting_group_handler_act_sg_callback_fires() -> None:
    """Client write `LLN0$SP$SGCB$ActSG` → server callback fires; act_sg updated."""
    calls: list[tuple[int, int]] = []
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")

    def on_act(new_sg: int, conn_id: int) -> bool:
        calls.append((new_sg, conn_id))
        return True

    s.register_setting_group_handler("GenericIO", on_act_sg=on_act)
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write(
                "IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, 2
            )
        finally:
            await conn.disconnect()
        # Allow a tick for the handler to be invoked on the server thread.
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert calls[0][0] == 2
        assert calls[0][1] > 0
        assert s.get_setting_group_info("GenericIO")["act_sg"] == 2


async def test_setting_group_handler_veto_returns_object_access_denied() -> None:
    from iec61850 import IedDataAccessError

    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_setting_group_handler("GenericIO", on_act_sg=lambda *_: False)
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            with pytest.raises(IedDataAccessError):
                await conn.write(
                    "IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, 3
                )
            # act_sg should be unchanged after a vetoed write.
            assert s.get_setting_group_info("GenericIO")["act_sg"] == 1
        finally:
            await conn.disconnect()


async def test_setting_group_confirm_callback_fires_on_round_trip() -> None:
    """EditSG=2 → CnfEdit=true → `on_confirm` invoked with edit_sg=2."""
    confirmed: list[int] = []
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")

    def on_confirm(edit_sg: int, _conn_id: int) -> None:
        confirmed.append(edit_sg)

    s.register_setting_group_handler("GenericIO", on_confirm=on_confirm)
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write(
                "IED1GenericIO/LLN0.SGCB.EditSG", FC.SP, 2
            )
            await conn.write(
                "IED1GenericIO/LLN0.SGCB.CnfEdit", FC.SP, True
            )
        finally:
            await conn.disconnect()
        await asyncio.sleep(0.05)
        assert confirmed == [2]
        # session cleared
        info = s.get_setting_group_info("GenericIO")
        assert info["edit_sg"] == 0


async def test_setting_group_handler_runtime_install_replaces_previous() -> None:
    """Calling `register_setting_group_handler` while running atomically swaps."""
    first_calls: list[int] = []
    second_calls: list[int] = []
    s = IedServer.from_scl_str(SERVER_SCL_WITH_SGCB, ied_name="IED1")
    s.bind("127.0.0.1:0")
    s.register_setting_group_handler(
        "GenericIO", on_act_sg=lambda sg, _: (first_calls.append(sg), True)[1]
    )
    async with s:
        conn = await IedConnection.connect(s.bound_addr)
        try:
            await conn.write("IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, 2)
            await asyncio.sleep(0.05)
            # Swap handler at runtime.
            s.register_setting_group_handler(
                "GenericIO",
                on_act_sg=lambda sg, _: (second_calls.append(sg), True)[1],
            )
            await conn.write("IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, 3)
            await asyncio.sleep(0.05)
        finally:
            await conn.disconnect()
        assert first_calls == [2]
        assert second_calls == [3]


# ── Direct IedModel construction (no SCL) ──────────────────────────────────


def _basic_model_spec() -> dict:
    """Return a model spec equivalent to ``SERVER_SCL``."""
    return {
        "ied_name": "IED1",
        "lds": [
            {
                "inst": "GenericIO",
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
                                        "trg_ops": ["data_changed"],
                                        "value": {"type": "int", "value": 1},
                                    },
                                    {"name": "q", "fc": "ST", "type": "Quality"},
                                    {"name": "t", "fc": "ST", "type": "Timestamp"},
                                ],
                            },
                        ],
                    },
                    {
                        "class": "GGIO",
                        "inst": "1",
                        "dos": [
                            {
                                "name": "Ind1",
                                "das": [
                                    {
                                        "name": "stVal",
                                        "fc": "ST",
                                        "type": "Boolean",
                                        "trg_ops": ["data_changed", "quality_changed"],
                                    },
                                    {"name": "q", "fc": "ST", "type": "Quality"},
                                    {"name": "t", "fc": "ST", "type": "Timestamp"},
                                ],
                            },
                            {
                                "name": "AnIn1",
                                "constructed_das": [
                                    {
                                        "name": "mag",
                                        "fc": "MX",
                                        "children": [
                                            {
                                                "name": "f",
                                                "fc": "MX",
                                                "type": "Float32",
                                                "trg_ops": ["data_changed"],
                                            },
                                        ],
                                    },
                                ],
                                "das": [
                                    {"name": "q", "fc": "MX", "type": "Quality"},
                                    {"name": "t", "fc": "MX", "type": "Timestamp"},
                                ],
                            },
                            {
                                "name": "SetPt1",
                                "das": [
                                    {
                                        "name": "setVal",
                                        "fc": "SP",
                                        "type": "Int32",
                                        "value": {"type": "int", "value": 0},
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }


def test_from_model_spec_rejects_missing_ied_name() -> None:
    spec = _basic_model_spec()
    del spec["ied_name"]
    with pytest.raises(ValueError, match="missing required key 'ied_name'"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_rejects_empty_lds() -> None:
    with pytest.raises(ValueError, match="at least one logical device"):
        IedServer.from_model_spec({"ied_name": "IED1", "lds": []})


def test_from_model_spec_rejects_lln0_not_first() -> None:
    """LLN0 invariant is enforced by the underlying IedModelBuilder."""
    spec = {
        "ied_name": "IED1",
        "lds": [
            {
                "inst": "WD1",
                "lns": [
                    {"class": "GGIO", "inst": "1", "dos": []},
                    {"lln0": True, "dos": []},
                ],
            },
        ],
    }
    with pytest.raises(ValueError, match="LLN0"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_rejects_bad_fc() -> None:
    spec = _basic_model_spec()
    spec["lds"][0]["lns"][0]["dos"][0]["das"][0]["fc"] = "XX"
    with pytest.raises(ValueError, match="invalid FC token"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_rejects_bad_da_type() -> None:
    spec = _basic_model_spec()
    spec["lds"][0]["lns"][0]["dos"][0]["das"][0]["type"] = "NotAType"
    with pytest.raises(ValueError, match="not a known DataAttributeType"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_rejects_bad_value_kind() -> None:
    spec = _basic_model_spec()
    spec["lds"][0]["lns"][0]["dos"][0]["das"][0]["value"] = {
        "type": "elephant",
        "value": 1,
    }
    with pytest.raises(ValueError, match="not supported"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_dataset_entry_unresolved() -> None:
    """Builder rejects datasets that point at a missing LN."""
    spec = _basic_model_spec()
    spec["lds"][0]["lns"][1]["datasets"] = [
        {
            "name": "Bad",
            "entries": [
                {"ln_name": "MissingLN1", "fc": "ST", "do_path": ["Ind1", "stVal"]},
            ],
        },
    ]
    with pytest.raises(ValueError, match="MissingLN1"):
        IedServer.from_model_spec(spec)


def test_from_model_spec_utc_time_size_validated() -> None:
    spec = _basic_model_spec()
    spec["lds"][0]["lns"][0]["dos"][0]["das"].append(
        {
            "name": "extraTime",
            "fc": "ST",
            "type": "Timestamp",
            "value": {"type": "utc_time", "value": b"\x00" * 4},
        }
    )
    with pytest.raises(ValueError, match="exactly 8 bytes"):
        IedServer.from_model_spec(spec)


async def test_from_model_spec_e2e_read_write() -> None:
    """Build a server from a dict spec and round-trip three primitive types."""
    s = IedServer.from_model_spec(_basic_model_spec())
    s.bind("127.0.0.1:0")
    async with s:
        s.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
        s.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 42.5)
        s.update_int32("GenericIO/GGIO1.SetPt1.setVal", 7)

        conn = await IedConnection.connect(s.bound_addr)
        try:
            assert (
                await conn.read_bool("IED1GenericIO/GGIO1.Ind1.stVal", FC.ST) is True
            )
            mag = await conn.read_float("IED1GenericIO/GGIO1.AnIn1.mag.f", FC.MX)
            assert abs(mag - 42.5) < 1e-4
            sp = await conn.read_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP)
            assert sp == 7

            # Client write also flows through to the cached server value.
            await conn.write("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP, 99)
            sp = await conn.read_int32("IED1GenericIO/GGIO1.SetPt1.setVal", FC.SP)
            assert sp == 99
        finally:
            await conn.disconnect()


async def test_from_model_spec_sgcb_force_active() -> None:
    """SGCB declared in the spec wires up to the same runtime handler API."""
    spec = _basic_model_spec()
    # Attach SGCB to LLN0.
    spec["lds"][0]["lns"][0]["sgcb"] = {
        "num_of_sg": 3,
        "act_sg": 1,
        "has_resv_tms": True,
        "default_resv_tms_s": 30,
    }
    s = IedServer.from_model_spec(spec)
    s.bind("127.0.0.1:0")
    async with s:
        info = s.get_setting_group_info("GenericIO")
        assert info["num_of_sg"] == 3
        assert info["act_sg"] == 1
        s.force_active_setting_group("GenericIO", 2)
        info = s.get_setting_group_info("GenericIO")
        assert info["act_sg"] == 2
