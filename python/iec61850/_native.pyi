"""Type stubs for the ``iec61850._native`` PyO3 extension.

These stubs cover the public surface of the Rust bindings. Users normally
interact with the higher-level wrappers in ``iec61850._facade`` (re-exported
from ``iec61850.__init__``); the stubs here exist so that mypy / pyright can
check the facade's internal use of ``_native`` and so that direct callers
of ``IedServer`` / ``Scl`` / module-level functions get full completion.

Async functions are typed as ``Awaitable[T]`` rather than ``async def`` because
the Rust side returns native futures rather than Python coroutines, but they
behave identically from ``await`` syntax's perspective.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ._dataclasses import Quality

__version__: str

# ─── Exception hierarchy ────────────────────────────────────────────────────

class IedError(Exception):
    """Base error for the iec61850 package."""

class IedConnectionError(IedError):
    """TCP / OSI stack connection failure.

    ``reason`` names the failure when the stack reports one:
    ``"connection-lost"``, ``"released-by-peer"``, ``"transport"`` (with the
    transport's ``kind`` and ``detail``), ``"associate-failed"`` or
    ``"initiate-failed"`` (with the Initiate-ErrorPDU subcode as ``code``).
    Attributes that do not apply are ``None``.
    """

    reason: str | None
    kind: str | None
    detail: str | None
    code: int | None

class IedSessionRefusedError(IedConnectionError):
    """Peer answered the connect request with a session REFUSE SPDU.

    ``reason_code`` is PI 50 of the RF SPDU, ``transport_disconnect`` PI 17,
    and ``provider_reason`` the provider-reason of the CPR PPDU the refusal
    carries. Each is ``None`` when the refusal omits it.
    """

    reason_code: int | None
    transport_disconnect: int | None
    provider_reason: int | None

class IedTlsError(IedConnectionError):
    """TLS handshake failed while the connection was being established.

    ``kind`` is one of ``"verification"``, ``"alert_received"``,
    ``"version_unsupported"``, ``"cipher_unsupported"``, ``"timeout"``,
    ``"io"`` or ``"other"``; ``message`` is the text of the TLS error.
    """

    kind: str | None
    message: str | None

class IedAuthenticationError(IedConnectionError):
    """The peer refused the association for the calling identity or its
    credentials.

    ``diagnostic`` is the acse-service-user diagnostic code of the rejecting
    AARE and ``diagnostic_name`` its name, for instance 13
    ``"authentication-failure"``; ``reason`` is ``"authentication-rejected"``.
    """

    diagnostic: int | None
    diagnostic_name: str | None

class IedTimeoutError(IedError):
    """Operation exceeded its deadline.

    ``timeout_ms`` is the deadline that elapsed, when the stack reports it.
    """

    timeout_ms: int | None

class IedDataAccessError(IedError):
    """Server returned a DataAccess service error.

    ``code`` is the DataAccessError value, when the server reported one.
    """

    code: int | None

class IedServiceError(IedError):
    """Other MMS service-level failure.

    ``reason`` names the failure: ``"service-error"`` (a Confirmed-ErrorPDU,
    with ``error_class`` the errorClass alternative, ``code`` its subcode and
    ``number_deleted`` the count a refused DeleteDataSet still deleted),
    ``"reject"`` (with ``reject_type`` and the rejectReason as ``code``),
    ``"conclude-rejected"``, ``"outstanding-limit"``, ``"pdu-too-large"``
    (with ``pdu_size`` and ``max_size``), ``"name-list-limit"`` (with
    ``limit``), ``"name-list-not-advancing"`` or ``"journal-not-advancing"``.
    Attributes that do not apply are ``None``.
    """

    reason: str | None
    error_class: int | None
    code: int | None
    reject_type: int | None
    number_deleted: int | None
    pdu_size: int | None
    max_size: int | None
    limit: int | None

class IedControlError(IedError):
    """Control object Select / Operate / Cancel failure."""

class IedServerError(IedError):
    """Model or registration the hosted server refuses.

    Raised by ``start()`` and ``register_from_model()``. ``reference`` names
    the object a refused duplicate registration addressed, else ``None``.
    A port that cannot be bound raises ``OSError``, and a call in the wrong
    lifecycle state ``RuntimeError``, not this class.
    """

    reference: str | None

class SclError(IedError):
    """SCL / ICD / CID parse / resolve failure."""

# ─── Module-level functions ─────────────────────────────────────────────────

def parse_scl(xml: str) -> Scl:
    """Parse an SCL / ICD / CID XML string into an in-memory ``Scl`` document."""

def load_scl(path: str | os.PathLike[str]) -> Scl:
    """Read an SCL / ICD / CID document from a filesystem path."""

def query_sntp(server: str, timeout_s: float = 3.0) -> Awaitable[dict[str, Any]]:
    """Query an SNTP/NTP server. Returns a dict — wrap with
    :class:`iec61850.SntpResponse` for a typed result.

    ``server`` is an IP address and port, ``"192.0.2.10:123"`` or
    ``"[2001:db8::1]:123"``; a host name is not resolved and raises
    ``ValueError``, as does a ``timeout_s`` that is not a positive finite
    number.
    """

# ─── Native types used internally by ``_facade.py`` ─────────────────────────

class RcbHandle:
    """Native report control block handle."""

    @property
    def object_reference(self) -> str: ...
    @property
    def is_buffered(self) -> bool: ...
    rpt_id: str | None
    rpt_ena: bool
    resv: bool
    data_set_reference: str | None
    opt_flds_bits: int
    buf_tm_ms: int
    trg_ops_bits: int
    intg_pd_ms: int
    gi: bool
    purge_buf: bool
    entry_id: bytes | None
    resv_tms: int
    @property
    def has_resv_tms(self) -> bool: ...
    @property
    def conf_rev(self) -> int: ...
    @property
    def sq_num(self) -> int: ...
    @property
    def time_of_entry_ms(self) -> int: ...
    @property
    def owner(self) -> bytes | None: ...

class ControlObjectClient:
    """Native control object client (Select / Operate / Cancel)."""

    @property
    def object_reference(self) -> str: ...
    @property
    def domain(self) -> str: ...
    @property
    def ctl_model(self) -> str: ...
    def set_origin(self, or_cat: int, or_ident: bytes) -> None: ...
    def set_ctl_num(self, ctl_num: int) -> None: ...
    def set_test(self, on: bool) -> None: ...
    def set_synchro_check(self, on: bool) -> None: ...
    def set_interlock_check(self, on: bool) -> None: ...
    def set_sbo_class(self, sbo_class: str) -> None: ...
    def select(self) -> Awaitable[dict[str, Any]]: ...
    def select_with_value(self, value: Any) -> Awaitable[dict[str, Any]]: ...
    def operate(self, value: Any) -> Awaitable[dict[str, Any]]: ...
    def cancel(self, value: Any) -> Awaitable[dict[str, Any]]: ...

class IedConnection:
    """Native MMS client connection."""

    @classmethod
    def connect(
        cls,
        addr: str,
        *,
        timeout_ms: int = ...,
        request_timeout_ms: int | None = ...,
        max_outstanding: int | None = ...,
        local_max_pdu_size: int | None = ...,
        iso: dict[str, Any] | None = ...,
        max_name_list_names: int | None = ...,
    ) -> Awaitable[IedConnection]: ...
    @classmethod
    def connect_tls(
        cls,
        addr: str,
        *,
        server_name: str,
        ca_pem: bytes,
        client_cert_pem: bytes | None = ...,
        client_key_pem: bytes | None = ...,
        verify_hostname: bool = ...,
        allow_only_known_peers: bool = ...,
        known_peer_pems: Sequence[bytes] = ...,
        time_validation: bool = ...,
        crl_pems: Sequence[bytes] = ...,
        min_tls_version: str = ...,
        max_tls_version: str = ...,
        timeout_ms: int = ...,
        request_timeout_ms: int | None = ...,
        max_outstanding: int | None = ...,
        local_max_pdu_size: int | None = ...,
        iso: dict[str, Any] | None = ...,
        max_name_list_names: int | None = ...,
    ) -> Awaitable[IedConnection]: ...
    def disconnect(self) -> Awaitable[None]: ...
    def abort(self) -> Awaitable[None]: ...
    @property
    def is_connected(self) -> bool: ...

    # Typed read methods
    def read_bool(self, reference: str, fc: str) -> Awaitable[bool]: ...
    def read_int32(self, reference: str, fc: str) -> Awaitable[int]: ...
    def read_uint32(self, reference: str, fc: str) -> Awaitable[int]: ...
    def read_int64(self, reference: str, fc: str) -> Awaitable[int]: ...
    def read_float(self, reference: str, fc: str) -> Awaitable[float]: ...
    def read_float64(self, reference: str, fc: str) -> Awaitable[float]: ...
    def read_string(self, reference: str, fc: str) -> Awaitable[str]: ...
    def read_timestamp(self, reference: str, fc: str) -> Awaitable[bytes]: ...
    def read_quality_bits(self, reference: str, fc: str) -> Awaitable[int]: ...
    def read_bit_string(
        self, reference: str, fc: str
    ) -> Awaitable[tuple[bytes, int]]: ...

    # Typed write methods
    def write_bool(self, reference: str, fc: str, value: bool) -> Awaitable[None]: ...
    def write_int32(self, reference: str, fc: str, value: int) -> Awaitable[None]: ...
    def write_uint32(self, reference: str, fc: str, value: int) -> Awaitable[None]: ...
    def write_float(self, reference: str, fc: str, value: float) -> Awaitable[None]: ...
    def write_octet_string(
        self, reference: str, fc: str, value: bytes
    ) -> Awaitable[None]: ...
    def write_visible_string(
        self, reference: str, fc: str, value: str
    ) -> Awaitable[None]: ...
    def write_bit_string(
        self, reference: str, fc: str, data: bytes, padding: int
    ) -> Awaitable[None]: ...

    # Generic typed I/O
    # Scalars arrive as bool / int / float / str, byte-shaped kinds as bytes,
    # ARRAY and STRUCTURE as list.
    def read_object(self, reference: str, fc: str) -> Awaitable[Any]: ...
    def write_object(self, reference: str, fc: str, value: Any) -> Awaitable[None]: ...

    # Directory / model browsing
    def get_server_directory(self) -> Awaitable[list[str]]: ...
    def get_logical_device_directory(self, ld_name: str) -> Awaitable[list[str]]: ...
    # `class_token` is an ACSI class spelling: DO, DS, RP, BR, LG (logs),
    # LCB (log control blocks), GO, SG.
    def get_logical_node_directory(
        self, ln_ref: str, class_token: str
    ) -> Awaitable[list[str]]: ...
    def get_data_directory(self, data_ref: str) -> Awaitable[list[str]]: ...
    def get_variable_specification(
        self, reference: str, fc: str
    ) -> Awaitable[dict[str, Any]]: ...
    # One MMS Read over `(reference, fc_token)` pairs. Result order and length
    # follow `targets`; a failing entry is the marker dict
    # `{"error": <DataAccessError name>, "code": <int>}`.
    def read_objects(
        self, targets: Sequence[tuple[str, str]]
    ) -> Awaitable[list[Any]]: ...
    def get_device_model(self, refresh: bool = ...) -> Awaitable[dict[str, Any]]: ...

    # Data sets
    def create_data_set(
        self, reference: str, members: Sequence[tuple[str, str]]
    ) -> Awaitable[None]: ...
    # False when the server held no data set under that reference.
    def delete_data_set(self, reference: str) -> Awaitable[bool]: ...
    # `{"deletable": bool, "members": [{"object_ref", "fc", "array_index",
    # "component"}, ...]}`, members in the order the server holds them.
    def get_data_set_directory(self, reference: str) -> Awaitable[dict[str, Any]]: ...
    # Under `strict` a failing entry raises; otherwise it is the marker dict
    # `{"error": <DataAccessError name>, "code": <int>}`.
    def get_data_set_values(
        self, reference: str, strict: bool = ...
    ) -> Awaitable[list[Any]]: ...
    def set_data_set_values(
        self, reference: str, values: Sequence[Any]
    ) -> Awaitable[None]: ...

    # RCB
    def get_rcb_values(self, rcb_ref: str) -> Awaitable[RcbHandle]: ...
    def refresh_rcb_values(self, rcb: RcbHandle) -> Awaitable[None]: ...
    def set_rcb_values(
        self, rcb: RcbHandle, mask_bits: int, single_request: bool
    ) -> Awaitable[None]: ...
    def trigger_gi(self, rcb_ref: str) -> Awaitable[None]: ...

    # Reporting
    def install_report_handler(
        self, rcb_ref: str, rpt_id: str | None, callback: Callable[[dict[str, Any]], Any]
    ) -> Awaitable[None]: ...
    def uninstall_report_handler(self, rcb_ref: str) -> Awaitable[None]: ...
    # Blocks up to `timeout_ms`; resolves to the number of reports dispatched.
    def poll_reports(self, timeout_ms: int) -> Awaitable[int]: ...

    # Journaling
    def query_journal_by_time(
        self, log_ref: str, start_ms: int, end_ms: int
    ) -> Awaitable[dict[str, Any]]: ...
    def query_journal_after_entry(
        self, log_ref: str, starting_time_ms: int, entry_id: bytes
    ) -> Awaitable[dict[str, Any]]: ...
    # `query` is the mapping `iec61850.JournalQuery.to_native()` returns.
    def query_journal(
        self, log_ref: str, query: dict[str, Any]
    ) -> Awaitable[dict[str, Any]]: ...
    def query_journal_all(
        self, log_ref: str, query: dict[str, Any]
    ) -> Awaitable[list[dict[str, Any]]]: ...

    # GOOSE control blocks
    # `{"go_ena", "go_id", "dat_set", "conf_rev", "nds_com", "dst_address":
    # {"addr", "priority", "vlan_id", "app_id"}, "min_time_ms", "max_time_ms",
    # "fixed_offs"}`.
    def get_gocb_values(self, reference: str) -> Awaitable[dict[str, Any]]: ...
    def set_gocb_values(
        self,
        reference: str,
        *,
        go_ena: bool | None = ...,
        go_id: str | None = ...,
        dat_set: str | None = ...,
    ) -> Awaitable[None]: ...

    # Server identification
    # `{"vendor", "model", "revision", "abstract_syntaxes": [dotted OID, ...]}`.
    def get_server_identity(self) -> Awaitable[dict[str, Any]]: ...
    # `{"logical", "logical_code", "physical", "physical_code", "local_detail"}`.
    def get_server_status(
        self, extended_derivation: bool = ...
    ) -> Awaitable[dict[str, Any]]: ...

    # Control
    def create_control_object(
        self, object_ref: str, ctl_model: str
    ) -> ControlObjectClient: ...

# ─── Scl document ───────────────────────────────────────────────────────────

class Scl:
    """Parsed and resolved SCL / ICD / CID document."""

    def ieds(self) -> list[str]: ...
    def to_dict(self) -> dict[str, Any]: ...
    def summary(self, ied_name: str) -> str: ...
    def __repr__(self) -> str: ...

# ─── IedServer ──────────────────────────────────────────────────────────────

class BatchGuard:
    """Context manager that batches server-side updates into one notification."""

    def __enter__(self) -> BatchGuard: ...
    def __exit__(
        self,
        _exc_type: object = ...,
        _exc_value: object = ...,
        _traceback: object = ...,
    ) -> None: ...
    def __repr__(self) -> str: ...

class IedServer:
    """IEC 61850 MMS server hosting one IED.

    Construct with :meth:`from_scl`, :meth:`from_scl_str`, or
    :meth:`from_model_spec`. Configure with :meth:`bind`, :meth:`with_tls`,
    and the ``max_connections`` / ``vendor`` / ``model_name`` / ``revision``
    setters, and the ``association_*_timeout_ms`` and
    ``max_dynamic_datasets*`` limits. Start via ``async with`` or
    :meth:`start` / :meth:`stop`.

    ``start()`` raises ``OSError`` when it cannot listen on the bound
    address, ``RuntimeError`` without a prior ``bind()`` or on a second call,
    and ``IedServerError`` for a model or registration the server refuses.
    A setter or ``register_*`` call after ``start()``, and a running-only
    call while the server is not running, raise ``RuntimeError``.
    """

    @classmethod
    def from_scl(cls, path: str | os.PathLike[str], *, ied_name: str) -> IedServer: ...
    @classmethod
    def from_scl_str(cls, xml: str, *, ied_name: str) -> IedServer: ...
    @classmethod
    def from_model_spec(cls, spec: dict[str, Any]) -> IedServer: ...
    def bind(self, addr: str) -> None: ...
    def with_tls(
        self,
        server_cert_pem: bytes,
        server_key_pem: bytes,
        *,
        client_ca_pem: bytes | None = ...,
        allow_only_known_peers: bool = ...,
        known_peer_pems: Sequence[bytes] = ...,
        require_client_auth: bool = ...,
        time_validation: bool = ...,
        crl_pems: Sequence[bytes] = ...,
        session_resumption: bool = ...,
        min_tls_version: str = ...,
        max_tls_version: str = ...,
    ) -> None: ...

    # Write-only settings, assignable before ``start()``; reading one raises
    # ``AttributeError``, and assigning one after ``start()`` ``RuntimeError``.
    max_connections: int  # write-only
    association_setup_timeout_ms: int  # write-only
    association_idle_timeout_ms: int | None  # write-only
    association_send_timeout_ms: int  # write-only
    max_dynamic_datasets: int  # write-only
    max_dynamic_datasets_per_association: int  # write-only
    vendor: str | None  # write-only
    model_name: str | None  # write-only
    revision: str | None  # write-only

    def set_edition(self, edition: str) -> None: ...
    def set_write_access_policy(self, fc: str, allow: bool) -> None: ...
    def set_authenticator(
        self, authenticator: Callable[[dict[str, Any]], bool | str | int | None] | None
    ) -> None: ...
    def write_access_allowed(self, fc: str) -> bool: ...
    @property
    def is_running(self) -> bool: ...
    @property
    def bound_addr(self) -> str: ...
    @property
    def connection_count(self) -> int: ...
    def abort_all_connections(self) -> int: ...
    def abort_connection(self, conn_id: int) -> None: ...
    def dynamic_dataset_count(self) -> int: ...
    def start(self) -> Awaitable[None]: ...
    def stop(self) -> Awaitable[None]: ...
    def __aenter__(self) -> Awaitable[IedServer]: ...
    def __aexit__(
        self, _exc_type: object, _exc_value: object, _traceback: object
    ) -> Awaitable[None]: ...

    # Handler registration. Callbacks receive Python values; the argument they
    # are called with is documented on the corresponding Rust item, which ships
    # as the method's Python __doc__.
    def on_read(
        self, path: str, callback: Callable[..., Any], *, with_context: bool = ...
    ) -> None: ...
    def on_control(
        self,
        path: str,
        *,
        ctl_model: str,
        check: Callable[..., Any] | None = ...,
        operate: Callable[..., Any] | None = ...,
        wait: Callable[..., Any] | None = ...,
        sbo_timeout_ms: int = ...,
        sbo_class: str = ...,
        oper_timeout_ms: int = ...,
    ) -> None: ...
    def on_write(
        self, path: str, callback: Callable[..., Any], *, with_context: bool = ...
    ) -> None: ...

    # Data sets / report blocks / log blocks
    def register_from_model(self) -> dict[str, int] | None: ...
    def add_dataset(self, name: str, paths: Sequence[str]) -> None: ...
    def register_urcb(
        self,
        path: str,
        *,
        dataset: str,
        rpt_id: str | None = ...,
        conf_rev: int = ...,
        trg_ops: Sequence[str] | None = ...,
        opt_flds: Sequence[str] | None = ...,
        buf_tm_ms: int = ...,
        intg_pd_ms: int = ...,
    ) -> None: ...
    def register_brcb(
        self,
        path: str,
        *,
        dataset: str,
        rpt_id: str | None = ...,
        conf_rev: int = ...,
        trg_ops: Sequence[str] | None = ...,
        opt_flds: Sequence[str] | None = ...,
        buf_tm_ms: int = ...,
        intg_pd_ms: int = ...,
        buffer_capacity: int = ...,
        with_resv_tms: bool = ...,
        with_owner: bool = ...,
    ) -> None: ...
    def register_log_control(
        self,
        path: str,
        *,
        dataset: str,
        log_ref: str | None = ...,
        trg_ops: Sequence[str] | None = ...,
        intg_pd_ms: int = ...,
        storage_capacity: int | None = ...,
        include_reason_code: bool = ...,
        default_enabled: bool = ...,
    ) -> None: ...

    # Logging API
    def set_log_ena(self, path: str, on: bool) -> None: ...
    def log_value(
        self,
        path: str,
        *,
        data_ref: str,
        value: Any,
        time_ms: int | None = ...,
        reason_code: int = ...,
    ) -> int | None: ...

    # Setting groups
    def register_setting_group_handler(
        self,
        ld_inst: str,
        *,
        on_act_sg: Callable[[int, int], bool | None] | None = ...,
        on_edit_sg: Callable[[int, int], bool | None] | None = ...,
        on_confirm: Callable[[int, int], bool | None] | None = ...,
    ) -> None: ...
    def get_setting_group_info(self, ld_inst: str) -> dict[str, Any]: ...
    def force_active_setting_group(self, ld_inst: str, sg: int) -> None: ...
    def get_setting_group_value(self, path: str, group: int) -> Any: ...
    def set_setting_group_value(self, path: str, group: int, value: Any) -> None: ...

    # Update API
    def batch(self) -> BatchGuard: ...
    def update_bool(self, path: str, value: bool) -> None: ...
    def update_int32(self, path: str, value: int) -> None: ...
    def update_int64(self, path: str, value: int) -> None: ...
    def update_uint32(self, path: str, value: int) -> None: ...
    def update_float32(self, path: str, value: float) -> None: ...
    def update_float64(self, path: str, value: float) -> None: ...
    def update_string(self, path: str, value: str) -> None: ...
    def update_bit_string(self, path: str, data: bytes, padding: int) -> None: ...
    def update_bit_string_from_int(self, path: str, value: int) -> None: ...
    def update_quality(self, path: str, quality: Quality | int) -> None: ...
    def update_array(self, path: str, values: Sequence[Any]) -> None: ...
    def __repr__(self) -> str: ...
