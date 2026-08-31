"""SCL / ICD parser surface — offline, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from iec61850 import IedError, Scl, SclError, load_scl, parse_scl

# Synthetic SCL covering an IED, one LD, two LNs, all four DataTypeTemplates
# tables, a report control block, and a data set.
MINIMAL_SCL = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1" manufacturer="ACME" configVersion="1.0">
    <AccessPoint name="AP1">
      <Server>
        <LDevice inst="GenericIO">
          <LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
            <DataSet name="events">
              <FCDA ldInst="GenericIO" prefix="" lnClass="LLN0" lnInst="" doName="Mod" daName="stVal" fc="ST"/>
            </DataSet>
            <ReportControl name="rcb1" datSet="events" confRev="1" buffered="true" bufTime="500">
              <TrgOps dchg="true" qchg="true"/>
              <OptFields seqNum="true" timeStamp="true" reasonCode="true"/>
            </ReportControl>
          </LN0>
          <LN prefix="" lnClass="GGIO" inst="1" lnType="GGIO_1"/>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LLN0_0" lnClass="LLN0">
      <DO name="Mod" type="ENC_1"/>
      <DO name="Beh" type="ENC_1"/>
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
      <EnumVal ord="3">test</EnumVal>
    </EnumType>
  </DataTypeTemplates>
</SCL>
"""


def test_parse_scl_returns_scl_handle() -> None:
    scl = parse_scl(MINIMAL_SCL)
    assert isinstance(scl, Scl)
    assert scl.ieds() == ["IED1"]
    assert "Scl(ieds=1)" in repr(scl)


def test_to_dict_top_level_shape() -> None:
    d = parse_scl(MINIMAL_SCL).to_dict()
    assert set(d.keys()) == {"ieds", "data_type_templates"}
    assert isinstance(d["ieds"], list)
    assert len(d["ieds"]) == 1


def test_to_dict_ied_branch() -> None:
    d = parse_scl(MINIMAL_SCL).to_dict()
    ied = d["ieds"][0]
    assert ied["name"] == "IED1"
    assert ied["manufacturer"] == "ACME"
    assert ied["config_version"] == "1.0"
    assert ied["desc"] is None
    # access_points → server → logical_devices → logical_nodes
    ap = ied["access_points"][0]
    assert ap["name"] == "AP1"
    ld = ap["server"]["logical_devices"][0]
    assert ld["inst"] == "GenericIO"
    assert ld["ld_name"] is None
    assert len(ld["logical_nodes"]) == 2
    lln0 = ld["logical_nodes"][0]
    assert lln0["ln_class"] == "LLN0"
    assert lln0["ln_type"] == "LLN0_0"
    assert lln0["inst"] == ""


def test_to_dict_data_set_and_rcb_present() -> None:
    d = parse_scl(MINIMAL_SCL).to_dict()
    lln0 = d["ieds"][0]["access_points"][0]["server"]["logical_devices"][0][
        "logical_nodes"
    ][0]
    assert len(lln0["data_sets"]) == 1
    ds = lln0["data_sets"][0]
    assert ds["name"] == "events"
    assert ds["fcdas"][0]["fc"] == "ST"
    assert ds["fcdas"][0]["do_name"] == "Mod"
    # ReportControl
    assert len(lln0["report_controls"]) == 1
    rcb = lln0["report_controls"][0]
    assert rcb["name"] == "rcb1"
    assert rcb["buffered"] is True
    assert rcb["data_set"] == "events"
    assert rcb["trg_ops"]["data_change"] is True
    assert rcb["trg_ops"]["quality_change"] is True
    assert rcb["opt_fields"]["seq_num"] is True
    assert rcb["opt_fields"]["reason_code"] is True


def test_to_dict_data_type_templates() -> None:
    dtt = parse_scl(MINIMAL_SCL).to_dict()["data_type_templates"]
    assert set(dtt["ln_node_types"]) == {"LLN0_0", "GGIO_1"}
    assert set(dtt["do_types"]) == {"ENC_1", "SPS_1"}
    assert dtt["enum_types"]["BehaviourKind"]["values"][0] == {
        "ord": 1,
        "name": "on",
        "desc": None,
    }
    # DOType → DA list
    enc = dtt["do_types"]["ENC_1"]
    assert enc["cdc"] == "ENC"
    da_names = [da["name"] for da in enc["das"]]
    assert da_names == ["stVal", "q", "t"]
    stval = enc["das"][0]
    assert stval["b_type"] == "Enum"
    assert stval["type_ref"] == "BehaviourKind"
    assert stval["fc"] == "ST"


def test_summary_is_canonical_text() -> None:
    scl = parse_scl(MINIMAL_SCL)
    s = scl.summary("IED1")
    assert s.startswith("IED name=IED1")
    assert "LD inst=GenericIO" in s
    assert "LN class=LLN0" in s
    # Equivalence oracle property: same input → identical text bytes.
    assert s == parse_scl(MINIMAL_SCL).summary("IED1")


def test_summary_unknown_ied_raises() -> None:
    scl = parse_scl(MINIMAL_SCL)
    with pytest.raises(SclError):
        scl.summary("DOES_NOT_EXIST")


def test_xml_syntax_error_carries_line_and_kind() -> None:
    with pytest.raises(SclError) as exc:
        parse_scl("<not_closed")
    err = exc.value
    assert err.kind == "Xml"
    assert err.line >= 1
    assert err.column >= 1
    assert err.message  # non-empty


def test_duplicate_identifier_error() -> None:
    xml = """<?xml version="1.0"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1"><AccessPoint name="AP"/></IED>
  <IED name="IED1"><AccessPoint name="AP"/></IED>
</SCL>
"""
    with pytest.raises(SclError) as exc:
        parse_scl(xml)
    err = exc.value
    assert err.kind == "DuplicateIdentifier"
    assert err.line == 4


def test_unresolved_type_reference_error() -> None:
    xml = """<?xml version="1.0"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <IED name="IED1"><AccessPoint name="AP"><Server><LDevice inst="LD0">
    <LN0 lnClass="LLN0" inst="" lnType="MISSING"/>
  </LDevice></Server></AccessPoint></IED>
</SCL>
"""
    with pytest.raises(SclError) as exc:
        parse_scl(xml)
    err = exc.value
    assert err.kind == "UnresolvedTypeReference"
    assert err.attribute == "lnType"
    assert "MISSING" in err.message
    assert "LN[lnClass=" in err.element_path


def test_scl_error_is_ied_error_subclass() -> None:
    # All package exceptions inherit from IedError, so blanket `except IedError`
    # also catches SCL failures — useful when the same code path could hit
    # either a network or an offline-parse error.
    assert issubclass(SclError, IedError)


def test_load_scl_from_path(tmp_path: Path) -> None:
    p = tmp_path / "minimal.icd"
    p.write_text(MINIMAL_SCL, encoding="utf-8")
    scl = load_scl(str(p))
    assert scl.ieds() == ["IED1"]
    # PathLike acceptance — Path object instead of str.
    scl2 = load_scl(p)
    assert scl2.ieds() == ["IED1"]


def test_load_scl_missing_file_raises_os_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.icd"
    with pytest.raises(OSError):
        load_scl(str(missing))
