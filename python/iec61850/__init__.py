"""iec61850 — IEC 61850 MMS client and server for Python (MMS over TCP).

Async-first. Public surface re-exported from this module. ``IedConnection``
and ``Iec61850Client`` are the client entry points; ``IedServer`` hosts an
MMS server built from SCL or from a model spec.
"""

from __future__ import annotations

from typing import Any

from ._dataclasses import (
    ClientReport,
    ControlOutcome,
    DataAccessFailure,
    DataSetDirectory,
    DataSetMember,
    GoCBValues,
    Iec61850ClientConfig,
    InclusionReason,
    IsoConnectionParameters,
    JournalEntry,
    JournalEntryVariable,
    JournalQuery,
    LastApplError,
    OriginValue,
    PhyComAddress,
    Quality,
    QualityDetail,
    ReportEntry,
    ReportOptFlds,
    ServerIdentity,
    ServerStatus,
    SntpResponse,
    TimeQuality,
    TlsConfig,
    TriggerOptions,
    TypeSpec,
)
from ._enums import (
    FC,
    AcsiClass,
    ControlAddCause,
    ControlModel,
    SboClass,
    TlsVersion,
    Validity,
)
from ._facade import (
    ControlObjectClient,
    Iec61850Client,
    IedConnection,
    RcbHandle,
    RcbWriteMask,
    ReportDispatcher,
)
from ._native import (
    IedConnectionError,
    IedControlError,
    IedDataAccessError,
    IedError,
    IedServer,
    IedServerError,
    IedServiceError,
    IedSessionRefusedError,
    IedTimeoutError,
    IedTlsError,
    Scl,
    SclError,
    __version__,
    load_scl,
    parse_scl,
)
from ._native import query_sntp as _native_query_sntp


async def query_sntp(server: str, timeout_s: float = 3.0) -> SntpResponse:
    """Query an SNTP/NTP server and return a typed :class:`SntpResponse`.

    ``server`` is an IP address and port, ``"192.0.2.10:123"`` or
    ``"[2001:db8::1]:123"``; a host name is not resolved and raises
    ``ValueError``. See :class:`SntpResponse` for field semantics. The default
    ``timeout_s`` of 3.0 seconds matches typical NTP wait budgets; raise it for
    high-latency or constrained-link IED deployments.
    """
    raw: dict[str, Any] = await _native_query_sntp(server, timeout_s)
    return SntpResponse.from_native(raw)


__all__ = [
    "AcsiClass",
    "ClientReport",
    "ControlAddCause",
    "ControlModel",
    "ControlObjectClient",
    "ControlOutcome",
    "DataAccessFailure",
    "DataSetDirectory",
    "DataSetMember",
    "FC",
    "GoCBValues",
    "Iec61850Client",
    "Iec61850ClientConfig",
    "IedConnection",
    "IedConnectionError",
    "IedControlError",
    "IedDataAccessError",
    "IedError",
    "IedServer",
    "IedServerError",
    "IedServiceError",
    "IedSessionRefusedError",
    "IedTimeoutError",
    "IedTlsError",
    "InclusionReason",
    "IsoConnectionParameters",
    "JournalEntry",
    "JournalEntryVariable",
    "JournalQuery",
    "LastApplError",
    "OriginValue",
    "PhyComAddress",
    "Quality",
    "QualityDetail",
    "RcbHandle",
    "RcbWriteMask",
    "ReportDispatcher",
    "ReportEntry",
    "ReportOptFlds",
    "SboClass",
    "Scl",
    "SclError",
    "ServerIdentity",
    "ServerStatus",
    "SntpResponse",
    "TimeQuality",
    "TlsConfig",
    "TlsVersion",
    "TriggerOptions",
    "TypeSpec",
    "Validity",
    "__version__",
    "load_scl",
    "parse_scl",
    "query_sntp",
]
