"""Frozen value-type dataclasses for the iec61850 public API.

These are pure-Python because they are immutable values — PyO3 classes are
reserved for opaque handles that hold Rust state (``IedConnection``,
``RcbHandle``, ``ControlObjectClient``, ``MmsValue``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ._enums import FC, ControlAddCause, TlsVersion, Validity


@dataclass(frozen=True, slots=True)
class TriggerOptions:
    """RCB trigger conditions (TrgOps, IEC 61850-7-2 §17.2.3.2.4)."""

    data_change: bool = False
    quality_change: bool = False
    data_update: bool = False
    integrity: bool = False
    general_interrogation: bool = False

    def to_bits(self) -> int:
        """Pack into the native 5-bit representation (bit 0 = data_change ...)."""
        return (
            (0x01 if self.data_change else 0)
            | (0x02 if self.quality_change else 0)
            | (0x04 if self.data_update else 0)
            | (0x08 if self.integrity else 0)
            | (0x10 if self.general_interrogation else 0)
        )

    @classmethod
    def from_bits(cls, bits: int) -> "TriggerOptions":
        return cls(
            data_change=bool(bits & 0x01),
            quality_change=bool(bits & 0x02),
            data_update=bool(bits & 0x04),
            integrity=bool(bits & 0x08),
            general_interrogation=bool(bits & 0x10),
        )


@dataclass(frozen=True, slots=True)
class ReportOptFlds:
    """RCB optional fields (OptFlds, IEC 61850-7-2 §17.2.3.2.5)."""

    sequence_number: bool = False
    report_timestamp: bool = False
    reason_for_inclusion: bool = False
    dataset_name: bool = False
    data_reference: bool = False
    buffer_overflow: bool = False
    entry_id: bool = False
    conf_rev: bool = False
    segmentation: bool = False

    def to_bits(self) -> int:
        """Pack into the native 9-bit representation."""
        return (
            (0x001 if self.sequence_number else 0)
            | (0x002 if self.report_timestamp else 0)
            | (0x004 if self.reason_for_inclusion else 0)
            | (0x008 if self.dataset_name else 0)
            | (0x010 if self.data_reference else 0)
            | (0x020 if self.buffer_overflow else 0)
            | (0x040 if self.entry_id else 0)
            | (0x080 if self.conf_rev else 0)
            | (0x100 if self.segmentation else 0)
        )

    @classmethod
    def from_bits(cls, bits: int) -> "ReportOptFlds":
        return cls(
            sequence_number=bool(bits & 0x001),
            report_timestamp=bool(bits & 0x002),
            reason_for_inclusion=bool(bits & 0x004),
            dataset_name=bool(bits & 0x008),
            data_reference=bool(bits & 0x010),
            buffer_overflow=bool(bits & 0x020),
            entry_id=bool(bits & 0x040),
            conf_rev=bool(bits & 0x080),
            segmentation=bool(bits & 0x100),
        )


@dataclass(frozen=True, slots=True)
class QualityDetail:
    """Detail bits of an IEC 61850 quality attribute (IEC 61850-7-3 §6.2.3)."""

    overflow: bool = False
    out_of_range: bool = False
    bad_reference: bool = False
    oscillatory: bool = False
    failure: bool = False
    old_data: bool = False
    inconsistent: bool = False
    inaccurate: bool = False


@dataclass(frozen=True, slots=True)
class Quality:
    """IEC 61850 quality attribute."""

    validity: Validity = Validity.GOOD
    detail: QualityDetail = field(default_factory=QualityDetail)
    source_substituted: bool = False
    test: bool = False
    operator_blocked: bool = False


@dataclass(frozen=True, slots=True)
class InclusionReason:
    """Reason a member was included in a report (IEC 61850-7-2 §17.2.3.2.6)."""

    data_change: bool = False
    quality_change: bool = False
    data_update: bool = False
    integrity: bool = False
    general_interrogation: bool = False
    application_trigger: bool = False

    @classmethod
    def from_bits(cls, bits: int) -> "InclusionReason":
        return cls(
            data_change=bool(bits & 0x01),
            quality_change=bool(bits & 0x02),
            data_update=bool(bits & 0x04),
            integrity=bool(bits & 0x08),
            general_interrogation=bool(bits & 0x10),
        )


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """One member's snapshot inside a ``ClientReport``."""

    ref: str | None
    value: Any  # native scalar OR raw bytes for composite kinds
    reason: InclusionReason | None = None


@dataclass(frozen=True, slots=True)
class ClientReport:
    """A report delivered to a callback registered via ``install_report_handler``."""

    rcb_reference: str
    rpt_id: str
    sequence_number: int | None
    timestamp_ms: int | None
    dataset_name: str | None
    buffer_overflow: bool | None
    entry_id: bytes | None
    conf_rev: int | None
    entries: Sequence[ReportEntry]

    @classmethod
    def from_native_dict(cls, d: Mapping[str, Any]) -> "ClientReport":
        """Normalise the dict the native callback delivers into the public dataclass."""
        values: Sequence[Any] = d["values"]
        reasons: Sequence[int] = d["reasons"]
        refs: Sequence[str | None] = d["data_references"]
        entries: list[ReportEntry] = []
        for idx, value in enumerate(values):
            ref = refs[idx] if idx < len(refs) else None
            reason = (
                InclusionReason.from_bits(reasons[idx]) if idx < len(reasons) else None
            )
            entries.append(ReportEntry(ref=ref, value=value, reason=reason))
        return cls(
            rcb_reference=d["rcb_reference"],
            rpt_id=d["rpt_id"],
            sequence_number=d.get("sequence_number"),
            timestamp_ms=d.get("timestamp_ms"),
            dataset_name=d.get("data_set_name"),
            buffer_overflow=d.get("buffer_overflow"),
            entry_id=d.get("entry_id"),
            conf_rev=d.get("conf_rev"),
            entries=entries,
        )


@dataclass(frozen=True, slots=True)
class ControlParams:
    """Parameters for ``ControlObjectClient.operate()``."""

    ctl_val: Any
    operate_time: datetime | None = None
    test: bool = False
    check_synchrocheck: bool = False
    check_interlock: bool = False
    origin_orcat: int = 0
    origin_orident: bytes = b""


@dataclass(frozen=True, slots=True)
class OriginValue:
    """``origin`` DA payload (IEC 61850-7-2 §17.2.5)."""

    or_cat: int = 3  # default: bay-control
    or_ident: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.or_cat <= 8:
            raise ValueError(f"or_cat must be in [0, 8], got {self.or_cat}")
        if len(self.or_ident) > 64:
            raise ValueError(f"or_ident must be <= 64 bytes, got {len(self.or_ident)}")


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    """Result of ``select`` / ``operate`` / ``cancel``."""

    success: bool
    add_cause: ControlAddCause | None = None

    @classmethod
    def from_pair(cls, pair: tuple[bool, str | None]) -> "ControlOutcome":
        ok, cause = pair
        return cls(
            success=ok,
            add_cause=ControlAddCause(cause) if cause is not None else None,
        )


@dataclass(frozen=True, slots=True)
class JournalEntryVariable:
    """One data point inside a :class:`JournalEntry`.

    ``value`` follows the same conversion rules as ``IedConnection.read``:
    scalars surface natively; ``BIT_STRING`` / ``OCTET_STRING`` / ``UTC_TIME``
    / ``BINARY_TIME`` surface as ``bytes``; composites surface as ``list``.
    """

    data_ref: str
    value: Any
    reason_code: int


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One entry retrieved from a Log Control Block via ``query_journal_*``.

    ``entry_id`` is the 8-byte big-endian server-assigned identifier. Pair it
    with ``time_ms`` when calling ``query_journal_after_entry`` to resume.
    """

    entry_id: bytes
    time_ms: int
    variables: tuple[JournalEntryVariable, ...]

    @classmethod
    def from_native_dict(cls, d: Mapping[str, Any]) -> "JournalEntry":
        variables = tuple(
            JournalEntryVariable(
                data_ref=v["data_ref"],
                value=v["value"],
                reason_code=v["reason_code"],
            )
            for v in d["variables"]
        )
        return cls(
            entry_id=d["entry_id"],
            time_ms=d["time_ms"],
            variables=variables,
        )


@dataclass(frozen=True, slots=True)
class DataSetMember:
    """Member spec for ``create_data_set()``."""

    object_ref: str
    fc: FC
    array_index: int | None = None
    component: str | None = None


@dataclass(frozen=True, slots=True)
class DataSetDirectory:
    """Result of ``get_data_set_directory()``.

    ``members`` is in the order the server holds them, which is the order
    ``get_data_set_values()`` returns its entries in, so the list can be fed
    straight back into ``create_data_set()`` to clone the set. ``deletable``
    mirrors MMS ``mmsDeletable``: true only for a data set created at runtime
    through ``create_data_set()``, false for one declared in the SCL.
    """

    deletable: bool
    members: list[DataSetMember]


@dataclass(frozen=True, slots=True)
class DataAccessFailure:
    """One failed entry of a per-entry read result.

    Stands in place of a value in the list returned by ``read_multiple()`` and
    ``get_data_set_values()`` under ``strict=False``. ``error`` is the symbolic
    MMS ``DataAccessError`` name and ``code`` the value carried on the wire.
    """

    code: int
    error: str


# Selector length limits. ISO 8327-1 bounds the S-Selector and ISO 8823-1 the
# P-Selector at 16 octets each. ISO 8073 leaves the T-Selector variable in
# length; the 4 octets accepted here are this binding's own limit, matching the
# fixed four-octet transport selector the stack below encodes.
_T_SELECTOR_MAX_LEN = 4
_S_SELECTOR_MAX_LEN = 16
_P_SELECTOR_MAX_LEN = 16

# OBJECT IDENTIFIER bounds of ISO 8825-1: at least two arcs, a first arc of at
# most 2, a second arc of at most 39 unless the first is 2, and arcs that fit
# the 32-bit range this package encodes.
_OID_MIN_ARCS = 2
_OID_FIRST_ARC_MAX = 2
_OID_SECOND_ARC_MAX = 39
_OID_ARC_MAX = 0xFFFFFFFF

# AE-qualifier is an ISO 8650 INTEGER carried as a 32-bit signed value.
_AE_QUALIFIER_MIN = -(2**31)
_AE_QUALIFIER_MAX = 2**31 - 1


def _check_selector(name: str, value: bytes, max_len: int) -> None:
    """Reject a selector that is not bytes or exceeds its layer's length."""
    if not isinstance(value, (bytes, bytearray)):
        raise ValueError(f"{name} must be bytes, got {type(value).__name__}")
    if len(value) > max_len:
        raise ValueError(
            f"{name} is {len(value)} bytes, at most {max_len} are allowed"
        )


def _check_ap_title(name: str, value: str | None) -> None:
    """Reject an AP-title that is not a dotted, encodable OBJECT IDENTIFIER."""
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a dotted OID string or None")
    arcs = []
    for part in value.split("."):
        if not part.isdigit():
            raise ValueError(f"{name} arc '{part}' is not a decimal number")
        arc = int(part)
        if arc > _OID_ARC_MAX:
            raise ValueError(f"{name} arc {arc} does not fit in 32 bits")
        arcs.append(arc)
    if len(arcs) < _OID_MIN_ARCS:
        raise ValueError(f"{name} needs at least {_OID_MIN_ARCS} arcs, got {len(arcs)}")
    first, second = arcs[0], arcs[1]
    if first > _OID_FIRST_ARC_MAX or (
        first < _OID_FIRST_ARC_MAX and second > _OID_SECOND_ARC_MAX
    ):
        raise ValueError(f"{name} arcs {first}.{second} are outside the encodable range")


def _check_ae_qualifier(name: str, value: int | None) -> None:
    """Reject an AE-qualifier outside the signed 32-bit range."""
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an int or None")
    if not _AE_QUALIFIER_MIN <= value <= _AE_QUALIFIER_MAX:
        raise ValueError(f"{name} {value} does not fit in a signed 32-bit integer")


@dataclass(frozen=True, slots=True)
class IsoConnectionParameters:
    """ISO addressing offered when a client opens an association.

    ``local_*`` names the calling side and ``remote_*`` the called side. The
    default selectors are the values IEC 61850-8-1 specifies for an MMS
    association: T-Selector ``00 01``, S-Selector ``00 01``, P-Selector
    ``00 00 00 01``. The default AP-titles and AE-qualifiers are the values IEDs
    customarily expect; the standard leaves them to the configuration. An
    AP-title of ``None`` leaves that field out of the ACSE AARQ.

    Pass an instance to ``IedConnection.connect()`` or ``connect_tls()`` to
    address a server that expects selectors other than the defaults.
    """

    local_t_sel: bytes = b"\x00\x01"
    remote_t_sel: bytes = b"\x00\x01"
    local_s_sel: bytes = b"\x00\x01"
    remote_s_sel: bytes = b"\x00\x01"
    local_p_sel: bytes = b"\x00\x00\x00\x01"
    remote_p_sel: bytes = b"\x00\x00\x00\x01"
    local_ap_title: str | None = "1.1.1.999"
    remote_ap_title: str | None = "1.1.1.999.1"
    local_ae_qualifier: int | None = 12
    remote_ae_qualifier: int | None = 12

    def __post_init__(self) -> None:
        """Validate every field, raising ``ValueError`` on the first offender."""
        _check_selector("local_t_sel", self.local_t_sel, _T_SELECTOR_MAX_LEN)
        _check_selector("remote_t_sel", self.remote_t_sel, _T_SELECTOR_MAX_LEN)
        _check_selector("local_s_sel", self.local_s_sel, _S_SELECTOR_MAX_LEN)
        _check_selector("remote_s_sel", self.remote_s_sel, _S_SELECTOR_MAX_LEN)
        _check_selector("local_p_sel", self.local_p_sel, _P_SELECTOR_MAX_LEN)
        _check_selector("remote_p_sel", self.remote_p_sel, _P_SELECTOR_MAX_LEN)
        _check_ap_title("local_ap_title", self.local_ap_title)
        _check_ap_title("remote_ap_title", self.remote_ap_title)
        _check_ae_qualifier("local_ae_qualifier", self.local_ae_qualifier)
        _check_ae_qualifier("remote_ae_qualifier", self.remote_ae_qualifier)

    def to_native(self) -> dict[str, Any]:
        """Return the mapping the native ``connect`` entry points accept."""
        return {
            "local_t_sel": bytes(self.local_t_sel),
            "remote_t_sel": bytes(self.remote_t_sel),
            "local_s_sel": bytes(self.local_s_sel),
            "remote_s_sel": bytes(self.remote_s_sel),
            "local_p_sel": bytes(self.local_p_sel),
            "remote_p_sel": bytes(self.remote_p_sel),
            "local_ap_title": self.local_ap_title,
            "remote_ap_title": self.remote_ap_title,
            "local_ae_qualifier": self.local_ae_qualifier,
            "remote_ae_qualifier": self.remote_ae_qualifier,
        }


@dataclass(frozen=True, slots=True)
class TlsConfig:
    """TLS configuration for ``IedConnection.connect_tls()``.

    PEM-encoded bytes are accepted directly so callers can load from any source
    (file, KMS, secrets manager) without this package taking a filesystem dep.

    Defaults match the strict IEC 62351-3 profile: TLS 1.2-1.3, full chain +
    time validation, SNI verification on, no allow-list. Override the
    ``allow_only_known_peers`` / ``known_peer_pems`` pair to pin specific
    server certificates (IEC 62351-3 known-peer mode).
    """

    ca_pem: bytes
    client_cert_pem: bytes | None = None
    client_key_pem: bytes | None = None
    verify_hostname: bool = True
    allow_only_known_peers: bool = False
    known_peer_pems: tuple[bytes, ...] = ()
    chain_validation: bool = True
    time_validation: bool = True
    crl_pems: tuple[bytes, ...] = ()
    min_version: TlsVersion = TlsVersion.TLS_1_2
    max_version: TlsVersion = TlsVersion.TLS_1_3


@dataclass(frozen=True, slots=True)
class Iec61850ClientConfig:
    """High-level config consumed by the ``Iec61850Client`` wrapper.

    ``timeout_ms`` is the deadline wrapping the entire connect handshake
    (TCP + ISO stack + MMS Initiate, plus TLS when applicable).
    ``request_timeout_ms`` / ``max_outstanding`` / ``local_max_pdu_size`` apply
    after the connection is established and tune per-request behavior. ``iso``
    replaces the ISO addressing the association offers; ``None`` keeps the
    ``IsoConnectionParameters`` defaults.
    """

    address: str
    port: int = 102
    timeout_ms: int = 5000
    request_timeout_ms: int | None = None
    max_outstanding: int | None = None
    local_max_pdu_size: int | None = None
    tls: TlsConfig | None = None
    tls_server_name: str | None = None
    report_dispatcher_interval_ms: int | None = 100
    iso: IsoConnectionParameters | None = None


# TypeSpec is a recursive MMS type descriptor surfaced as a nested dict.
TypeSpec = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SntpResponse:
    """One SNTP/NTP query result, parsed and converted to Unix-epoch seconds.

    ``offset_seconds`` is ``server_time - client_time`` (positive means the
    server clock is ahead of the local clock). ``round_trip_seconds`` is the
    one-shot RTT estimate from RFC 4330 §5. Both are derived from the four
    timestamps t1..t4 captured around the wire send/recv; a single sample is
    not as accurate as a multi-sample NTP algorithm but matches IEC 61850
    SNTP-class accuracy expectations.

    ``reference_id`` is a 4-byte identifier. For stratum-1 servers it is a
    4-char ASCII source code (``b"GPS\\0"``, ``b"LOCL"``, etc.). For higher
    strata it is typically an IPv4 address or an MD5 hash slice; treat as
    opaque bytes unless you know the server's convention.
    """

    server_time_unix_s: float
    client_receive_unix_s: float
    offset_seconds: float
    round_trip_seconds: float
    stratum: int
    poll: int
    precision: int
    reference_id: bytes
    leap_indicator: int
    version: int

    @classmethod
    def from_native(cls, raw: Mapping[str, Any]) -> "SntpResponse":
        """Build from the dict returned by :func:`iec61850.query_sntp`."""
        return cls(
            server_time_unix_s=float(raw["server_time_unix_s"]),
            client_receive_unix_s=float(raw["client_receive_unix_s"]),
            offset_seconds=float(raw["offset_seconds"]),
            round_trip_seconds=float(raw["round_trip_seconds"]),
            stratum=int(raw["stratum"]),
            poll=int(raw["poll"]),
            precision=int(raw["precision"]),
            reference_id=bytes(raw["reference_id"]),
            leap_indicator=int(raw["leap_indicator"]),
            version=int(raw["version"]),
        )

    def server_datetime_utc(self) -> datetime:
        """Convenience: return the server transmit time as a UTC ``datetime``."""
        return datetime.fromtimestamp(self.server_time_unix_s, tz=timezone.utc)
