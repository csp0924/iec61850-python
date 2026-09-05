"""iec61850 — Python client for IEC 61850 (MMS over TCP).

Async-first. Public surface re-exported from this module. See ``IedConnection``
for the primary client entry point.
"""

from __future__ import annotations

from typing import Any

from ._dataclasses import (
    ClientReport,
    ControlOutcome,
    ControlParams,
    DataAccessFailure,
    DataSetDirectory,
    DataSetMember,
    Iec61850ClientConfig,
    InclusionReason,
    IsoConnectionParameters,
    JournalEntry,
    JournalEntryVariable,
    OriginValue,
    Quality,
    QualityDetail,
    ReportEntry,
    ReportOptFlds,
    SntpResponse,
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
    MmsValue,
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
    Scl,
    SclError,
    __version__,
    load_scl,
    parse_scl,
)
from ._native import query_sntp as _native_query_sntp


async def query_sntp(server: str, timeout_s: float = 3.0) -> SntpResponse:
    """Query an SNTP/NTP server and return a typed :class:`SntpResponse`.

    See :class:`SntpResponse` for field semantics. The default ``timeout_s`` of
    3.0 seconds matches typical NTP wait budgets; raise it for high-latency or
    constrained-link IED deployments.
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
    "ControlParams",
    "DataAccessFailure",
    "DataSetDirectory",
    "DataSetMember",
    "FC",
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
    "InclusionReason",
    "IsoConnectionParameters",
    "JournalEntry",
    "JournalEntryVariable",
    "MmsValue",
    "OriginValue",
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
    "SntpResponse",
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
