"""Shape of the public API, checked without a server.

These guard what `iec61850` re-exports, the exception hierarchy, enum
spellings, and the bit layouts the dataclasses encode. Behavior against a
live association is covered by the end-to-end modules.
"""

from __future__ import annotations

import pytest

import iec61850


def test_version_exposed() -> None:
    assert isinstance(iec61850.__version__, str)
    assert iec61850.__version__


def test_public_surface_complete() -> None:
    expected = {
        "AcsiClass", "ClientReport", "ControlAddCause", "ControlModel",
        "ControlObjectClient", "ControlOutcome", "ControlParams",
        "DataSetMember", "FC", "Iec61850Client", "Iec61850ClientConfig",
        "IedConnection", "IedConnectionError", "IedControlError",
        "IedDataAccessError", "IedError", "IedServiceError", "IedTimeoutError",
        "InclusionReason", "MmsValue", "OriginValue", "Quality", "QualityDetail",
        "RcbHandle", "RcbWriteMask", "ReportDispatcher", "ReportEntry",
        "ReportOptFlds", "SboClass", "TlsConfig", "TriggerOptions", "TypeSpec",
        "Validity",
    }
    missing = expected - set(iec61850.__all__)
    assert not missing, f"missing from __all__: {sorted(missing)}"


def test_exception_hierarchy() -> None:
    assert issubclass(iec61850.IedConnectionError, iec61850.IedError)
    assert issubclass(iec61850.IedTimeoutError, iec61850.IedError)
    assert issubclass(iec61850.IedDataAccessError, iec61850.IedError)
    assert issubclass(iec61850.IedServiceError, iec61850.IedError)
    assert issubclass(iec61850.IedControlError, iec61850.IedError)


def test_enum_values() -> None:
    assert iec61850.FC.MX.value == "MX"
    assert iec61850.AcsiClass.BUFFERED_REPORT.value == "BR"
    assert iec61850.Validity.GOOD.value == "good"
    assert iec61850.ControlModel.SBO_ENHANCED.value == "sbo-enhanced"
    assert iec61850.SboClass.OPERATE_ONCE.value == "operate-once"
    assert iec61850.ControlAddCause.BLOCKED_BY_INTERLOCKING.value == "blocked-by-interlocking"


def test_origin_value_validation() -> None:
    iec61850.OriginValue(or_cat=0, or_ident=b"")
    iec61850.OriginValue(or_cat=8, or_ident=b"x" * 64)
    with pytest.raises(ValueError):
        iec61850.OriginValue(or_cat=9)
    with pytest.raises(ValueError):
        iec61850.OriginValue(or_ident=b"x" * 65)


def test_control_outcome_from_pair() -> None:
    ok = iec61850.ControlOutcome.from_pair((True, None))
    assert ok.success is True and ok.add_cause is None

    bad = iec61850.ControlOutcome.from_pair((False, "blocked-by-interlocking"))
    assert bad.success is False
    assert bad.add_cause is iec61850.ControlAddCause.BLOCKED_BY_INTERLOCKING


def test_dataclass_defaults() -> None:
    trg = iec61850.TriggerOptions()
    assert trg.data_change is False
    assert trg.integrity is False

    opt = iec61850.ReportOptFlds(sequence_number=True, report_timestamp=True)
    assert opt.sequence_number is True
    assert opt.entry_id is False


def test_mms_value_structure_is_not_constructible() -> None:
    """`MmsValue` is a read-side handle; a structure cannot be built locally."""
    with pytest.raises(NotImplementedError):
        iec61850.MmsValue.structure({})


def test_rcb_write_mask_builds_from_field_names() -> None:
    full = iec61850.RcbWriteMask.all()
    assert full.bits != 0

    pair = iec61850.RcbWriteMask.fields("rpt_ena", "trigger_options")
    # The bit layout is part of the wire contract, so assert the exact union.
    assert pair.bits == 0x0002 | 0x0100

    with pytest.raises(ValueError, match="unknown RcbWriteMask field"):
        iec61850.RcbWriteMask.fields("not_a_field")


def test_report_opt_flds_bit_round_trip() -> None:
    opt = iec61850.ReportOptFlds(sequence_number=True, conf_rev=True)
    assert opt.to_bits() == 0x001 | 0x080
    assert iec61850.ReportOptFlds.from_bits(opt.to_bits()) == opt


def test_trigger_options_bit_round_trip() -> None:
    trg = iec61850.TriggerOptions(data_change=True, general_interrogation=True)
    assert trg.to_bits() == 0x01 | 0x10
    assert iec61850.TriggerOptions.from_bits(trg.to_bits()) == trg


async def test_connection_cannot_be_constructed_directly() -> None:
    """An association exists only once `connect` has completed."""
    with pytest.raises(TypeError, match="Use `await IedConnection.connect"):
        iec61850.IedConnection()


async def test_client_wrapper_requires_async_with() -> None:
    cfg = iec61850.Iec61850ClientConfig(address="127.0.0.1")
    cli = iec61850.Iec61850Client(cfg)
    with pytest.raises(RuntimeError, match="not entered"):
        _ = cli.connection
