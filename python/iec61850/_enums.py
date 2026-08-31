"""Enums for IEC 61850 functional constraints, ACSI classes, and quality."""

from __future__ import annotations

from enum import StrEnum


class FC(StrEnum):
    """Functional Constraint (IEC 61850-7-2 §6)."""

    ST = "ST"  # status information
    MX = "MX"  # measurands (analogue)
    SP = "SP"  # setpoint
    SV = "SV"  # substituted value
    CF = "CF"  # configuration
    DC = "DC"  # description
    SG = "SG"  # setting group
    SE = "SE"  # setting group editable
    SR = "SR"  # service response tracking
    OR = "OR"  # operate received
    BL = "BL"  # blocking
    EX = "EX"  # extended definition
    CO = "CO"  # control
    US = "US"  # unicast SCSM
    MS = "MS"  # multicast SCSM
    RP = "RP"  # unbuffered reporting
    BR = "BR"  # buffered reporting
    LG = "LG"  # logging


class AcsiClass(StrEnum):
    """ACSI class used by ``get_logical_node_directory``."""

    DATA_OBJECT = "DO"
    DATASET = "DS"
    REPORT = "RP"
    BUFFERED_REPORT = "BR"
    LOG = "LG"
    GOOSE = "GO"
    SETTING_GROUP = "SG"
    CONTROL = "CO"


class Validity(StrEnum):
    """Validity field of an IEC 61850 quality attribute (IEC 61850-7-3 §6.2.3.1)."""

    GOOD = "good"
    INVALID = "invalid"
    RESERVED = "reserved"
    QUESTIONABLE = "questionable"


class ControlModel(StrEnum):
    """Wire ``ctlModel`` token (IEC 61850-7-2 Table 67)."""

    STATUS_ONLY = "status-only"
    DIRECT_NORMAL = "direct-normal"
    SBO_NORMAL = "sbo-normal"
    DIRECT_ENHANCED = "direct-enhanced"
    SBO_ENHANCED = "sbo-enhanced"


class TlsVersion(StrEnum):
    """TLS protocol version (IEC 62351-3 Annex A mandates >= TLS 1.2)."""

    TLS_1_2 = "tls1.2"
    TLS_1_3 = "tls1.3"


class SboClass(StrEnum):
    """Select-Before-Operate selection class (IEC 61850-7-2 ``sboClass``)."""

    OPERATE_ONCE = "operate-once"
    OPERATE_MANY = "operate-many"


class ControlAddCause(StrEnum):
    """Reason a control action was rejected (IEC 61850-7-2 §20.1.4)."""

    UNKNOWN = "unknown"
    NOT_SUPPORTED = "not-supported"
    BLOCKED_BY_SWITCHING_HIERARCHY = "blocked-by-switching-hierarchy"
    SELECT_FAILED = "select-failed"
    INVALID_POSITION = "invalid-position"
    POSITION_REACHED = "position-reached"
    PARAMETER_CHANGE_IN_EXECUTION = "parameter-change-in-execution"
    STEP_LIMIT = "step-limit"
    BLOCKED_BY_MODE = "blocked-by-mode"
    BLOCKED_BY_PROCESS = "blocked-by-process"
    BLOCKED_BY_INTERLOCKING = "blocked-by-interlocking"
    BLOCKED_BY_SYNCHROCHECK = "blocked-by-synchrocheck"
    COMMAND_ALREADY_IN_EXECUTION = "command-already-in-execution"
    BLOCKED_BY_HEALTH = "blocked-by-health"
    ONE_OF_N_CONTROL = "one-of-n-control"
    ABORTION_BY_CANCEL = "abortion-by-cancel"
    TIME_LIMIT_OVER = "time-limit-over"
    ABORTION_BY_TRIP = "abortion-by-trip"
    OBJECT_NOT_SELECTED = "object-not-selected"
    OBJECT_ALREADY_SELECTED = "object-already-selected"
    NO_ACCESS_AUTHORITY = "no-access-authority"
    ENDED_WITH_OVERSHOOT = "ended-with-overshoot"
    ABORTION_DUE_TO_DEVIATION = "abortion-due-to-deviation"
    ABORTION_BY_COMMUNICATION_LOSS = "abortion-by-communication-loss"
    ABORTION_BY_COMMAND = "abortion-by-command"
    NONE = "none"
    INCONSISTENT_PARAMETERS = "inconsistent-parameters"
    LOCKED_BY_OTHER_CLIENT = "locked-by-other-client"
