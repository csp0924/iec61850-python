"""Public class surface for iec61850."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from . import _native
from ._dataclasses import (
    ClientReport,
    ControlOutcome,
    DataSetMember,
    Iec61850ClientConfig,
    JournalEntry,
    OriginValue,
    Quality,
    QualityDetail,
    ReportOptFlds,
    TlsConfig,
    TriggerOptions,
    TypeSpec,
)
from ._enums import FC, AcsiClass, ControlModel, SboClass, Validity

_NIE = "Not implemented in this release."


def _compose_reference(ref: str, array_index: int | None, component: str | None) -> str:
    """Encode array-element / sub-component selection into a reference string."""
    if array_index is None:
        if component is not None:
            raise ValueError("component requires array_index")
        return ref
    if array_index < 0:
        raise ValueError("array_index must be >= 0")
    target = f"{ref}({array_index})"
    if component is not None:
        target = f"{target}.{component}"
    return target


# Bit layout per IEC 61850-7-3 §6.2.3 / -8-1 BIT_STRING(13) Quality wire format.
# bits 0-1 = validity; bits 2-9 = detail flags; bit 10 source substituted;
# bit 11 test; bit 12 operator blocked.
_VALIDITY_BY_BITS: tuple[Validity, ...] = (
    Validity.GOOD,
    Validity.RESERVED,
    Validity.INVALID,
    Validity.QUESTIONABLE,
)


def _decode_quality_bits(bits: int) -> Quality:
    """Decode a 16-bit Quality packed value into the dataclass."""
    return Quality(
        validity=_VALIDITY_BY_BITS[bits & 0b11],
        detail=QualityDetail(
            overflow=bool(bits & 0x0004),
            out_of_range=bool(bits & 0x0008),
            bad_reference=bool(bits & 0x0010),
            oscillatory=bool(bits & 0x0020),
            failure=bool(bits & 0x0040),
            old_data=bool(bits & 0x0080),
            inconsistent=bool(bits & 0x0100),
            inaccurate=bool(bits & 0x0200),
        ),
        source_substituted=bool(bits & 0x0400),
        test=bool(bits & 0x0800),
        operator_blocked=bool(bits & 0x1000),
    )


def _decode_utc_time(buf: bytes) -> datetime:
    """Decode IEC 61850-8-1 Annex E UTC time (8-byte wire format) → datetime.

    Bytes 0..4 carry seconds since 1970-01-01 UTC (big-endian u32);
    bytes 4..7 carry a 24-bit big-endian fraction of a second (frac / 2^24);
    byte 7 is the TimeQuality flag byte and is not decoded here.
    """
    if len(buf) != 8:
        raise ValueError(f"UTC time wire buffer must be 8 bytes, got {len(buf)}")
    secs = int.from_bytes(buf[0:4], "big")
    frac_raw = (buf[4] << 16) | (buf[5] << 8) | buf[6]
    micros = (frac_raw * 1_000_000) // (1 << 24)
    return datetime.fromtimestamp(secs, tz=timezone.utc) + timedelta(
        microseconds=micros
    )


class MmsValue:
    """Wrapper for composite MMS values (STRUCTURE / ARRAY / BIT_STRING / OCTET_STRING).

    Scalar values are NOT wrapped — typed read methods return native Python
    types directly, and generic ``IedConnection.read()`` returns a native value
    or this wrapper depending on the underlying MMS kind.
    """

    __slots__ = ()

    @property
    def kind(self) -> str:
        raise NotImplementedError(_NIE)

    def as_dict(self) -> dict[str, Any]:
        """STRUCTURE → dict."""
        raise NotImplementedError(_NIE)

    def as_list(self) -> list[Any]:
        """ARRAY → list."""
        raise NotImplementedError(_NIE)

    def as_bytes(self) -> bytes:
        """OCTET_STRING / BIT_STRING → bytes."""
        raise NotImplementedError(_NIE)

    @staticmethod
    def structure(members: dict[str, Any]) -> "MmsValue":
        raise NotImplementedError(_NIE)

    @staticmethod
    def array(elements: list[Any]) -> "MmsValue":
        raise NotImplementedError(_NIE)

    @staticmethod
    def octet_string(data: bytes) -> "MmsValue":
        raise NotImplementedError(_NIE)


class RcbHandle:
    """Opaque mirror of a server-side Report Control Block.

    Mutate via property setters, then push to the server with
    ``IedConnection.set_rcb_values(handle, mask)``. Read-only fields
    (``conf_rev`` / ``sq_num`` / ``owner`` / ``time_of_entry_ms``) expose
    getters only and are ignored by ``set_rcb_values``.
    """

    __slots__ = ("_native",)

    def __init__(self, native: Any) -> None:
        self._native = native

    @property
    def object_reference(self) -> str:
        return self._native.object_reference

    @property
    def is_buffered(self) -> bool:
        return self._native.is_buffered

    @property
    def rpt_id(self) -> str | None:
        return self._native.rpt_id

    @rpt_id.setter
    def rpt_id(self, value: str) -> None:
        self._native.rpt_id = value

    @property
    def rpt_ena(self) -> bool:
        return self._native.rpt_ena

    @rpt_ena.setter
    def rpt_ena(self, value: bool) -> None:
        self._native.rpt_ena = value

    @property
    def resv(self) -> bool:
        return self._native.resv

    @resv.setter
    def resv(self, value: bool) -> None:
        self._native.resv = value

    @property
    def dataset(self) -> str | None:
        return self._native.data_set_reference

    @dataset.setter
    def dataset(self, value: str) -> None:
        self._native.data_set_reference = value

    @property
    def opt_flds(self) -> ReportOptFlds:
        return ReportOptFlds.from_bits(self._native.opt_flds_bits)

    @opt_flds.setter
    def opt_flds(self, value: ReportOptFlds) -> None:
        self._native.opt_flds_bits = value.to_bits()

    @property
    def buf_time_ms(self) -> int:
        return self._native.buf_tm_ms

    @buf_time_ms.setter
    def buf_time_ms(self, value: int) -> None:
        self._native.buf_tm_ms = value

    @property
    def trigger_options(self) -> TriggerOptions:
        return TriggerOptions.from_bits(self._native.trg_ops_bits)

    @trigger_options.setter
    def trigger_options(self, value: TriggerOptions) -> None:
        self._native.trg_ops_bits = value.to_bits()

    @property
    def integrity_period_ms(self) -> int:
        return self._native.intg_pd_ms

    @integrity_period_ms.setter
    def integrity_period_ms(self, value: int) -> None:
        self._native.intg_pd_ms = value

    @property
    def gi(self) -> bool:
        return self._native.gi

    @gi.setter
    def gi(self, value: bool) -> None:
        self._native.gi = value

    @property
    def purge_buf(self) -> bool:
        return self._native.purge_buf

    @purge_buf.setter
    def purge_buf(self, value: bool) -> None:
        self._native.purge_buf = value

    @property
    def entry_id(self) -> bytes | None:
        return self._native.entry_id

    @entry_id.setter
    def entry_id(self, value: bytes | None) -> None:
        self._native.entry_id = value

    @property
    def resv_tms(self) -> int:
        return self._native.resv_tms

    @resv_tms.setter
    def resv_tms(self, value: int) -> None:
        self._native.resv_tms = value

    @property
    def has_resv_tms(self) -> bool:
        return self._native.has_resv_tms

    @property
    def conf_rev(self) -> int:
        return self._native.conf_rev

    @property
    def sq_num(self) -> int:
        return self._native.sq_num

    @property
    def time_of_entry_ms(self) -> int:
        return self._native.time_of_entry_ms

    @property
    def owner(self) -> bytes | None:
        return self._native.owner


class RcbWriteMask:
    """Selects which ``RcbHandle`` fields are pushed in ``set_rcb_values``.

    Bit layout mirrors the IEC 61850-7-2 `RCB_ELEMENT_*` set.
    """

    _BITS: dict[str, int] = {
        "rpt_id": 0x0001,
        "rpt_ena": 0x0002,
        "resv": 0x0004,
        "dataset": 0x0008,
        "opt_flds": 0x0020,
        "buf_time": 0x0040,
        "trigger_options": 0x0100,
        "integrity_period": 0x0200,
        "gi": 0x0400,
        "purge_buf": 0x0800,
        "entry_id": 0x1000,
        "resv_tms": 0x4000,
    }

    __slots__ = ("_bits",)

    def __init__(self, bits: int = 0) -> None:
        self._bits = bits

    @property
    def bits(self) -> int:
        return self._bits

    @classmethod
    def all(cls) -> "RcbWriteMask":
        bits = 0
        for v in cls._BITS.values():
            bits |= v
        return cls(bits)

    @classmethod
    def fields(cls, *names: str) -> "RcbWriteMask":
        bits = 0
        for n in names:
            if n not in cls._BITS:
                raise ValueError(
                    f"unknown RcbWriteMask field '{n}'; allowed: {sorted(cls._BITS)}"
                )
            bits |= cls._BITS[n]
        return cls(bits)

    def __or__(self, other: "RcbWriteMask") -> "RcbWriteMask":
        return RcbWriteMask(self._bits | other._bits)


class ReportDispatcher:
    """Background task that polls the report queue at a fixed interval."""

    __slots__ = ("_task", "_stopped")

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task
        self._stopped = False

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._task.cancel()

    async def aclose(self) -> None:
        self.stop()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass


class ControlObjectClient:
    """``select`` / ``operate`` / ``cancel`` for a single controllable DO.

    Construct via :py:meth:`IedConnection.create_control_object`. The control
    payload type follows the underlying CDC: ``bool`` for SPC, ``int`` for
    DPC / INC / ENC, ``float`` for APC.
    """

    __slots__ = ("_native",)

    def __init__(self, native: Any) -> None:
        self._native = native

    @property
    def object_reference(self) -> str:
        return self._native.object_reference

    @property
    def domain(self) -> str:
        return self._native.domain

    @property
    def ctl_model(self) -> ControlModel:
        return ControlModel(self._native.ctl_model)

    def set_origin(self, origin: OriginValue) -> None:
        self._native.set_origin(origin.or_cat, origin.or_ident)

    def set_ctl_num(self, ctl_num: int) -> None:
        self._native.set_ctl_num(ctl_num)

    def set_test(self, on: bool) -> None:
        self._native.set_test(on)

    def set_synchro_check(self, on: bool) -> None:
        self._native.set_synchro_check(on)

    def set_interlock_check(self, on: bool) -> None:
        self._native.set_interlock_check(on)

    def set_sbo_class(self, sbo_class: SboClass) -> None:
        self._native.set_sbo_class(sbo_class.value)

    async def select(self) -> bool:
        """SBO-normal select. Returns ``True`` when the server accepted."""
        return await self._native.select()

    async def select_with_value(self, value: Any) -> ControlOutcome:
        """SBO-enhanced select-with-value. The value must match the later
        ``operate`` value or the server returns ``inconsistent-parameters``."""
        pair = await self._native.select_with_value(value)
        return ControlOutcome.from_pair(pair)

    async def operate(self, value: Any) -> ControlOutcome:
        """Operate. In enhanced modes this also waits for
        ``CommandTermination+/-``."""
        pair = await self._native.operate(value)
        return ControlOutcome.from_pair(pair)

    async def cancel(self, value: Any) -> ControlOutcome:
        """Cancel a previously selected operation."""
        pair = await self._native.cancel(value)
        return ControlOutcome.from_pair(pair)


class IedConnection:
    """Async IEC 61850 client connection.

    Construct with ``await IedConnection.connect(addr)`` and release with
    ``await disconnect()``. Methods that are not implemented in this release
    raise ``NotImplementedError``.
    """

    __slots__ = ("_native_conn",)
    _native_conn: _native.IedConnection

    def __init__(self) -> None:
        raise TypeError(
            "Use `await IedConnection.connect(addr)` to construct an IedConnection."
        )

    # ─── lifecycle ────────────────────────────────────────────────────────
    @classmethod
    async def connect(
        cls,
        addr: str,
        *,
        timeout_ms: int = 5000,
        request_timeout_ms: int | None = None,
        max_outstanding: int | None = None,
        local_max_pdu_size: int | None = None,
    ) -> "IedConnection":
        native = await _native.IedConnection.connect(
            addr,
            timeout_ms=timeout_ms,
            request_timeout_ms=request_timeout_ms,
            max_outstanding=max_outstanding,
            local_max_pdu_size=local_max_pdu_size,
        )
        self = cls.__new__(cls)
        self._native_conn = native
        return self

    @classmethod
    async def connect_tls(
        cls,
        addr: str,
        tls: TlsConfig,
        server_name: str,
        *,
        timeout_ms: int = 5000,
        request_timeout_ms: int | None = None,
        max_outstanding: int | None = None,
        local_max_pdu_size: int | None = None,
    ) -> "IedConnection":
        native = await _native.IedConnection.connect_tls(
            addr,
            server_name=server_name,
            ca_pem=tls.ca_pem,
            client_cert_pem=tls.client_cert_pem,
            client_key_pem=tls.client_key_pem,
            verify_hostname=tls.verify_hostname,
            allow_only_known_peers=tls.allow_only_known_peers,
            known_peer_pems=list(tls.known_peer_pems),
            chain_validation=tls.chain_validation,
            time_validation=tls.time_validation,
            crl_pems=list(tls.crl_pems),
            min_tls_version=tls.min_version.value,
            max_tls_version=tls.max_version.value,
            timeout_ms=timeout_ms,
            request_timeout_ms=request_timeout_ms,
            max_outstanding=max_outstanding,
            local_max_pdu_size=local_max_pdu_size,
        )
        self = cls.__new__(cls)
        self._native_conn = native
        return self

    async def disconnect(self) -> None:
        """Graceful MMS Conclude + TCP close."""
        await self._native_conn.disconnect()

    async def abort(self) -> None:
        """Drop the connection without sending an MMS Conclude.

        Use when the peer is unresponsive or the protocol layer is wedged and
        a graceful disconnect would block. Always completes — internal state
        is cleared regardless of the socket state.
        """
        await self._native_conn.abort()

    @property
    def is_connected(self) -> bool:
        return self._native_conn.is_connected

    # ─── typed read ───────────────────────────────────────────────────────
    async def read_bool(self, ref: str, fc: FC) -> bool:
        return await self._native_conn.read_bool(ref, fc)

    async def read_int32(self, ref: str, fc: FC) -> int:
        return await self._native_conn.read_int32(ref, fc)

    async def read_uint32(self, ref: str, fc: FC) -> int:
        return await self._native_conn.read_uint32(ref, fc)

    async def read_int64(self, ref: str, fc: FC) -> int:
        return await self._native_conn.read_int64(ref, fc)

    async def read_float(self, ref: str, fc: FC) -> float:
        return await self._native_conn.read_float(ref, fc)

    async def read_float64(self, ref: str, fc: FC) -> float:
        return await self._native_conn.read_float64(ref, fc)

    async def read_string(self, ref: str, fc: FC) -> str:
        return await self._native_conn.read_string(ref, fc)

    async def read_timestamp(self, ref: str, fc: FC) -> datetime:
        raw: bytes = await self._native_conn.read_timestamp(ref, fc)
        return _decode_utc_time(raw)

    async def read_quality(self, ref: str, fc: FC) -> Quality:
        bits: int = await self._native_conn.read_quality_bits(ref, fc)
        return _decode_quality_bits(bits)

    # ─── typed write ──────────────────────────────────────────────────────
    async def write_bool(self, ref: str, fc: FC, value: bool) -> None:
        await self._native_conn.write_bool(ref, fc, value)

    async def write_int32(self, ref: str, fc: FC, value: int) -> None:
        await self._native_conn.write_int32(ref, fc, value)

    async def write_uint32(self, ref: str, fc: FC, value: int) -> None:
        await self._native_conn.write_uint32(ref, fc, value)

    async def write_float(self, ref: str, fc: FC, value: float) -> None:
        await self._native_conn.write_float(ref, fc, value)

    async def write_octet_string(self, ref: str, fc: FC, value: bytes) -> None:
        await self._native_conn.write_octet_string(ref, fc, value)

    async def write_visible_string(self, ref: str, fc: FC, value: str) -> None:
        await self._native_conn.write_visible_string(ref, fc, value)

    # ─── generic (composite types + alternate access) ─────────────────────
    async def read(
        self,
        ref: str,
        fc: FC,
        *,
        array_index: int | None = None,
        component: str | None = None,
    ) -> Any:
        """Read an object and return its native Python representation.

        Scalars surface as ``bool`` / ``int`` / ``float`` / ``str``.
        ``OCTET_STRING`` / ``BIT_STRING`` / ``UTC_TIME`` / ``BINARY_TIME``
        surface as ``bytes``. ``ARRAY`` and ``STRUCTURE`` surface as ``list``.
        Use the typed read methods on hot paths.

        ``array_index`` selects a single element of an array DO;
        ``component`` selects a sub-component of that element and requires
        ``array_index``.
        """
        target = _compose_reference(ref, array_index, component)
        return await self._native_conn.read_object(target, fc)

    async def write(
        self,
        ref: str,
        fc: FC,
        value: Any,
        *,
        array_index: int | None = None,
        component: str | None = None,
    ) -> None:
        """Write a value to an object.

        Accepts ``bool``, ``int``, ``float``, ``str``, ``bytes``, or a ``list``
        of the same. ``array_index`` and ``component`` select an array element
        or sub-component, with the same semantics as ``read``.
        """
        target = _compose_reference(ref, array_index, component)
        await self._native_conn.write_object(target, fc, value)

    # ─── directory / model ────────────────────────────────────────────────
    async def get_server_directory(self, include_lds: bool = True) -> list[str]:
        """Return all logical device names exposed by the server.

        ``include_lds`` is accepted for forward compatibility and is currently
        ignored; file directory listing is not implemented in this release.
        """
        return await self._native_conn.get_server_directory()

    async def get_logical_device_directory(self, ld: str) -> list[str]:
        return await self._native_conn.get_logical_device_directory(ld)

    async def get_logical_node_directory(
        self, ln_ref: str, cls: AcsiClass
    ) -> list[str]:
        return await self._native_conn.get_logical_node_directory(ln_ref, cls)

    async def get_data_directory(self, data_ref: str) -> list[str]:
        """Return sub-element names one level under a data object.

        ``data_ref`` is ``"<LD>/<LN>.<DO>[.<sub>]*"``. Names returned do not
        carry FC suffixes; sub-elements present under multiple FCs are
        deduplicated.
        """
        return await self._native_conn.get_data_directory(data_ref)

    async def get_variable_specification(self, ref: str, fc: FC) -> TypeSpec:
        """Return the MMS ``TypeSpecification`` of a named variable.

        ``ref`` follows ``"<LD>/<LN>.<DO>[.<sub>]*"`` and must not carry an
        array index — query the container type and inspect ``element_type``
        for element-level info.

        Every node carries a ``"kind"`` discriminator. Scalar kinds add a few
        payload fields (e.g. ``width_bits`` for ``integer`` / ``unsigned``,
        ``max_chars`` for the string kinds). ``"array"`` adds
        ``element_count`` + ``element_type``; ``"structure"`` adds
        ``components`` (a list of ``{"name", "type"}`` entries).
        """
        return await self._native_conn.get_variable_specification(ref, fc)

    async def get_device_model(self, refresh: bool = False) -> dict[str, Any]:
        """Return the device-model snapshot — logical devices and their MMS
        NamedVariable names.

        Shape: ``{"logical_devices": [{"name": str, "variables": [str, ...]}, ...]}``.
        The first call (or one with ``refresh=True``) fetches from the
        server; subsequent calls re-use the cache.
        """
        return await self._native_conn.get_device_model(refresh)

    # ─── reporting ────────────────────────────────────────────────────────
    async def install_report_handler(
        self,
        rcb_ref: str,
        callback: Callable[[ClientReport], None],
        *,
        rpt_id: str | None = None,
    ) -> None:
        """Register a synchronous callback for reports on ``rcb_ref``.

        The callback runs inside the Rust dispatch task with the GIL held.
        Keep it short; offload heavy work via ``asyncio.run_coroutine_threadsafe``
        or a queue.
        """

        def _trampoline(native_dict: dict[str, Any]) -> None:
            callback(ClientReport.from_native_dict(native_dict))

        await self._native_conn.install_report_handler(rcb_ref, rpt_id, _trampoline)

    async def uninstall_report_handler(self, rcb_ref: str) -> None:
        await self._native_conn.uninstall_report_handler(rcb_ref)

    async def get_rcb_values(self, rcb_ref: str) -> RcbHandle:
        native = await self._native_conn.get_rcb_values(rcb_ref)
        return RcbHandle(native)

    async def set_rcb_values(
        self,
        rcb: RcbHandle,
        mask: RcbWriteMask,
        *,
        single_request: bool = False,
    ) -> None:
        await self._native_conn.set_rcb_values(rcb._native, mask.bits, single_request)

    async def refresh_rcb_values(self, rcb: RcbHandle) -> None:
        await self._native_conn.refresh_rcb_values(rcb._native)

    async def trigger_gi(self, rcb_ref: str) -> None:
        """Trigger General Interrogation by writing ``<rcb_ref>$GI = true``."""
        await self._native_conn.trigger_gi(rcb_ref)

    async def poll_reports(self, timeout_ms: int) -> int:
        """Pull pending InformationReports up to ``timeout_ms`` and dispatch
        them to installed handlers. Returns the number of dispatched URCB
        reports.
        """
        return await self._native_conn.poll_reports(timeout_ms)

    def spawn_report_dispatcher(self, interval_ms: int = 100) -> ReportDispatcher:
        """Background dispatch: keep calling ``poll_reports`` until the
        returned ``ReportDispatcher`` is stopped.
        """

        async def _loop() -> None:
            while True:
                try:
                    await self.poll_reports(interval_ms)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Connection drop or transient error — stop polling.
                    return

        task = asyncio.create_task(_loop())
        return ReportDispatcher(task)

    # ─── control ──────────────────────────────────────────────────────────
    def create_control_object(
        self, co_ref: str, ctl_model: ControlModel
    ) -> ControlObjectClient:
        """Create a control handle for ``co_ref`` (``"<LD>/<LN>.<DO>"``).

        ``ctl_model`` must match the server's declared model for the DO; pass
        the value read from ``<DO>.ctlModel`` (FC=CF) or supplied by SCD.
        """
        native = self._native_conn.create_control_object(co_ref, ctl_model.value)
        return ControlObjectClient(native)

    # ─── dataset ──────────────────────────────────────────────────────────
    async def create_data_set(self, ref: str, members: list[DataSetMember]) -> None:
        """Create a dynamic dataset on the server.

        ``ref`` is ``"<LD>/<LN>.<dsName>"``. Each member references a data
        attribute via ``"<LD>/<LN>.<DO>[.<sub>]*"`` plus a Functional
        Constraint. Set ``DataSetMember.array_index`` to target a single array
        element and ``DataSetMember.component`` to target a sub-component of
        that element (``component`` requires ``array_index``).
        """
        encoded: list[tuple[str, str]] = [
            (_compose_reference(m.object_ref, m.array_index, m.component), m.fc.value)
            for m in members
        ]
        await self._native_conn.create_data_set(ref, encoded)

    async def delete_data_set(self, ref: str) -> bool:
        """Delete a dynamic dataset from the server.

        Returns ``True`` when the server confirmed deletion; ``False`` when
        the dataset was unknown or the server refused (for example because
        it is a static dataset with ``mmsDeletable=false``).
        """
        return await self._native_conn.delete_data_set(ref)

    async def get_data_set_values(self, ref: str) -> list[Any]:
        """Read every value of a dataset.

        Each entry follows the same conversion rules as ``read``. Raises
        ``IedDataAccessError`` if any entry's access fails on the server.
        """
        return await self._native_conn.get_data_set_values(ref)

    async def set_data_set_values(self, ref: str, values: list[Any]) -> None:
        """Write every value of a dataset.

        ``values`` must match the server-side member count. Raises
        ``IedDataAccessError`` if any entry's write fails.
        """
        await self._native_conn.set_data_set_values(ref, values)

    # ─── journal / log ────────────────────────────────────────────────────
    async def query_journal_by_time(
        self,
        log_ref: str,
        start_ms: int,
        end_ms: int,
    ) -> tuple[list[JournalEntry], bool]:
        """Query a Log Control Block by time range (QueryLogByTime).

        ``log_ref`` is ``"<domain>/<item>"`` (e.g.
        ``"DemoIEDLD0/LLN0$LG$evlog"``). Bounds are ms epoch and
        inclusive on both ends; the server orders entries by time then by
        entry id. Returns ``(entries, more_follows)`` where ``more_follows``
        signals that the server truncated the response — call again from
        ``entries[-1].time_ms`` to resume.
        """
        raw = await self._native_conn.query_journal_by_time(log_ref, start_ms, end_ms)
        entries = [JournalEntry.from_native_dict(e) for e in raw["entries"]]
        return entries, bool(raw["more_follows"])

    async def query_journal_after_entry(
        self,
        log_ref: str,
        starting_time_ms: int,
        entry_id: bytes,
    ) -> tuple[list[JournalEntry], bool]:
        """Query a Log Control Block strictly after a given entry
        (QueryLogAfterEntry).

        ``entry_id`` must be 8 bytes (raise ``ValueError`` otherwise). Both
        ``starting_time_ms`` and ``entry_id`` are applied as filters — pair
        them with the values recorded against the caller's last seen entry.
        """
        raw = await self._native_conn.query_journal_after_entry(
            log_ref, starting_time_ms, entry_id
        )
        entries = [JournalEntry.from_native_dict(e) for e in raw["entries"]]
        return entries, bool(raw["more_follows"])


class Iec61850Client:
    """Async-context-manager wrapper around ``IedConnection``.

    Use as::

        async with Iec61850Client(cfg) as cli:
            value = await cli.connection.read(ref, FC.MX)

    Manages the underlying ``IedConnection`` lifecycle and, when
    ``config.report_dispatcher_interval_ms`` is set, a background
    ``ReportDispatcher``.
    """

    __slots__ = ("_config", "_connection", "_dispatcher")

    def __init__(self, config: Iec61850ClientConfig) -> None:
        self._config: Iec61850ClientConfig = config
        self._connection: IedConnection | None = None
        self._dispatcher: ReportDispatcher | None = None

    @property
    def config(self) -> Iec61850ClientConfig:
        return self._config

    @property
    def connection(self) -> IedConnection:
        if self._connection is None:
            raise RuntimeError("Iec61850Client is not entered — use `async with`.")
        return self._connection

    async def __aenter__(self) -> "Iec61850Client":
        cfg = self._config
        addr = f"{cfg.address}:{cfg.port}"
        tuning = {
            "timeout_ms": cfg.timeout_ms,
            "request_timeout_ms": cfg.request_timeout_ms,
            "max_outstanding": cfg.max_outstanding,
            "local_max_pdu_size": cfg.local_max_pdu_size,
        }
        if cfg.tls is None:
            conn = await IedConnection.connect(addr, **tuning)
        else:
            sni = cfg.tls_server_name or cfg.address
            conn = await IedConnection.connect_tls(addr, cfg.tls, sni, **tuning)
        self._connection = conn
        if cfg.report_dispatcher_interval_ms is not None:
            self._dispatcher = conn.spawn_report_dispatcher(
                interval_ms=cfg.report_dispatcher_interval_ms
            )
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._dispatcher is not None:
            await self._dispatcher.aclose()
            self._dispatcher = None
        if self._connection is not None:
            try:
                await self._connection.disconnect()
            finally:
                self._connection = None
