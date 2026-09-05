"""ISO connection parameters: validation, wire effect, and association.

The wire cases read the frames a hand-written peer captures, so they assert
what the client actually offers rather than that a permissive server accepted
it. A server is free to ignore the selectors it is addressed with, which makes
"the association succeeded" far too weak an oracle for a change that only
alters addressing.

Byte fragments below follow the field layouts of ISO 8073 (COTP variable part),
ISO 8327-1 (session parameter units), ISO 8823-1 (presentation selectors) and
ISO 8650-1 (ACSE AP-title), with the OBJECT IDENTIFIER content octets of
ISO 8825-1.
"""

from __future__ import annotations

import asyncio

import pytest

import iec61850
from iec61850 import IsoConnectionParameters, _native

from _iso_peer import COTP_CR_FIXED_LEN, session_peer

# COTP variable-part parameter codes for the transport selectors.
COTP_CALLING_TSAP = 0xC1
COTP_CALLED_TSAP = 0xC2

# Session parameter-group identifiers for the session selectors.
SESSION_CALLING_SSEL = 0x33
SESSION_CALLED_SSEL = 0x34

# Presentation context-list tags for the presentation selectors.
PRESENTATION_CALLING_PSEL = 0x81
PRESENTATION_CALLED_PSEL = 0x82

# ACSE AARQ tags for the application-entity titles.
ACSE_CALLED_AP_TITLE = 0xA2
ACSE_CALLING_AP_TITLE = 0xA6

# BER universal tag of an OBJECT IDENTIFIER.
BER_OBJECT_IDENTIFIER = 0x06

# Content octets of the default AP-titles, 1.1.1.999 and 1.1.1.999.1.
DEFAULT_LOCAL_AP_TITLE_OID = bytes([0x29, 0x01, 0x87, 0x67])
DEFAULT_REMOTE_AP_TITLE_OID = bytes([0x29, 0x01, 0x87, 0x67, 0x01])

# A distinctive set of parameters, none of which is a default.
CUSTOM = IsoConnectionParameters(
    local_t_sel=bytes([0x00, 0x02]),
    remote_t_sel=bytes([0x00, 0x03]),
    local_s_sel=bytes([0x00, 0x0A]),
    remote_s_sel=bytes([0x00, 0x0B]),
    local_p_sel=bytes([0x00, 0x00, 0x00, 0x0C]),
    remote_p_sel=bytes([0x00, 0x00, 0x00, 0x0D]),
    local_ap_title="1.3.9999.13",
    remote_ap_title="1.3.9999.13.1",
)

# Content octets of the custom AP-titles: 40 * 1 + 3, then 9999 and 13 written
# base-128, and the extra arc 1 on the called title.
CUSTOM_LOCAL_AP_TITLE_OID = bytes([0x2B, 0xCE, 0x0F, 0x0D])
CUSTOM_REMOTE_AP_TITLE_OID = bytes([0x2B, 0xCE, 0x0F, 0x0D, 0x01])

CONNECT_TIMEOUT_MS = 2000


def selector_field(tag: int, value: bytes) -> bytes:
    """The tag-length-value a selector occupies in COTP, session or CP."""
    return bytes([tag, len(value)]) + value


def ap_title_field(tag: int, oid_content: bytes) -> bytes:
    """The AARQ AP-title field wrapping an OBJECT IDENTIFIER."""
    inner = bytes([BER_OBJECT_IDENTIFIER, len(oid_content)]) + oid_content
    return bytes([tag, len(inner)]) + inner


def cotp_variable_part(cr: bytes) -> bytes:
    """The negotiated options of a COTP CR, without its varying references."""
    li = cr[0]
    return cr[1 + COTP_CR_FIXED_LEN : 1 + li]


async def capture_offer(
    iso: IsoConnectionParameters | None,
) -> tuple[bytes, bytes]:
    """Return the COTP CR and session CONNECT a `connect` attempt sends.

    The peer records both and then closes, so the attempt always fails; what
    matters is the bytes it put on the wire before it did.
    """
    async with session_peer() as peer:
        attempt = asyncio.ensure_future(
            iec61850.IedConnection.connect(
                peer.addr, timeout_ms=CONNECT_TIMEOUT_MS, iso=iso
            )
        )
        try:
            cr = await peer.connect_request()
            spdu = await peer.connect_spdu()
        finally:
            with pytest.raises(iec61850.IedError):
                await attempt
    return cr, spdu


async def test_omitting_iso_offers_the_dataclass_defaults() -> None:
    """`iso=None` and a default-constructed instance must be interchangeable.

    This is what pins the Python defaults to the ones the stack applies: the
    two paths never meet in the code, only on the wire.
    """
    absent_cr, absent_spdu = await capture_offer(None)
    explicit_cr, explicit_spdu = await capture_offer(IsoConnectionParameters())

    assert cotp_variable_part(absent_cr) == cotp_variable_part(explicit_cr)
    assert absent_spdu == explicit_spdu


async def test_default_parameters_carry_the_annex_a_values() -> None:
    """The defaults are the IEC 61850-8-1 Annex A values, read off the wire."""
    cr, spdu = await capture_offer(IsoConnectionParameters())
    options = cotp_variable_part(cr)
    two_bytes = bytes([0x00, 0x01])
    four_bytes = bytes([0x00, 0x00, 0x00, 0x01])

    assert selector_field(COTP_CALLING_TSAP, two_bytes) in options
    assert selector_field(COTP_CALLED_TSAP, two_bytes) in options
    assert selector_field(SESSION_CALLING_SSEL, two_bytes) in spdu
    assert selector_field(SESSION_CALLED_SSEL, two_bytes) in spdu
    assert selector_field(PRESENTATION_CALLING_PSEL, four_bytes) in spdu
    assert selector_field(PRESENTATION_CALLED_PSEL, four_bytes) in spdu
    assert ap_title_field(ACSE_CALLING_AP_TITLE, DEFAULT_LOCAL_AP_TITLE_OID) in spdu
    assert ap_title_field(ACSE_CALLED_AP_TITLE, DEFAULT_REMOTE_AP_TITLE_OID) in spdu


async def test_custom_parameters_reach_the_wire() -> None:
    """Every field the caller sets must appear in the frames it addresses."""
    cr, spdu = await capture_offer(CUSTOM)
    options = cotp_variable_part(cr)

    assert selector_field(COTP_CALLING_TSAP, CUSTOM.local_t_sel) in options
    assert selector_field(COTP_CALLED_TSAP, CUSTOM.remote_t_sel) in options
    assert selector_field(SESSION_CALLING_SSEL, CUSTOM.local_s_sel) in spdu
    assert selector_field(SESSION_CALLED_SSEL, CUSTOM.remote_s_sel) in spdu
    assert selector_field(PRESENTATION_CALLING_PSEL, CUSTOM.local_p_sel) in spdu
    assert selector_field(PRESENTATION_CALLED_PSEL, CUSTOM.remote_p_sel) in spdu
    assert ap_title_field(ACSE_CALLING_AP_TITLE, CUSTOM_LOCAL_AP_TITLE_OID) in spdu
    assert ap_title_field(ACSE_CALLED_AP_TITLE, CUSTOM_REMOTE_AP_TITLE_OID) in spdu


async def test_a_none_ap_title_is_left_out_of_the_aarq() -> None:
    """`None` omits the field rather than sending an empty one."""
    _, spdu = await capture_offer(IsoConnectionParameters(local_ap_title=None))

    assert (
        ap_title_field(ACSE_CALLING_AP_TITLE, DEFAULT_LOCAL_AP_TITLE_OID) not in spdu
    )
    assert ap_title_field(ACSE_CALLED_AP_TITLE, DEFAULT_REMOTE_AP_TITLE_OID) in spdu


async def test_custom_parameters_still_associate(demo_server: str) -> None:
    """Non-default addressing must still complete a whole association."""
    conn = await iec61850.IedConnection.connect(demo_server, iso=CUSTOM)
    try:
        assert conn.is_connected
        devices = await conn.get_server_directory()
        assert devices
    finally:
        await conn.disconnect()


async def test_client_config_forwards_iso(demo_server: str) -> None:
    """`Iec61850ClientConfig.iso` reaches the connection the wrapper opens."""
    host, port = demo_server.rsplit(":", 1)
    cfg = iec61850.Iec61850ClientConfig(
        address=host,
        port=int(port),
        report_dispatcher_interval_ms=None,
        iso=CUSTOM,
    )
    async with iec61850.Iec61850Client(cfg) as client:
        assert client.connection.is_connected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("local_t_sel", bytes(5)),
        ("remote_t_sel", bytes(5)),
        ("local_s_sel", bytes(17)),
        ("remote_s_sel", bytes(17)),
        ("local_p_sel", bytes(17)),
        ("remote_p_sel", bytes(17)),
    ],
)
def test_over_long_selector_is_rejected(field: str, value: bytes) -> None:
    with pytest.raises(ValueError, match="at most"):
        IsoConnectionParameters(**{field: value})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("1.x", "not a decimal number"),
        ("1", "at least 2 arcs"),
        ("3.1", "outside the encodable range"),
        ("1.40", "outside the encodable range"),
        ("1.1.4294967296", "does not fit in 32 bits"),
    ],
)
def test_malformed_ap_title_is_rejected(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        IsoConnectionParameters(local_ap_title=value)


def test_out_of_range_ae_qualifier_is_rejected() -> None:
    with pytest.raises(ValueError, match="signed 32-bit"):
        IsoConnectionParameters(local_ae_qualifier=2**31)


def test_native_rejects_an_unknown_iso_key() -> None:
    """A misspelled parameter must fail loudly, not be silently dropped."""
    with pytest.raises(ValueError, match="unknown iso parameter 'local_tsel'"):
        _native.IedConnection.connect(
            "127.0.0.1:102", iso={"local_tsel": bytes([0x00, 0x01])}
        )


def test_native_rejects_a_malformed_ap_title() -> None:
    with pytest.raises(ValueError, match="local_ap_title"):
        _native.IedConnection.connect("127.0.0.1:102", iso={"local_ap_title": "1.x"})


def test_to_native_covers_every_field() -> None:
    """The mapping the facade sends must name every dataclass field."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(IsoConnectionParameters)}
    assert set(CUSTOM.to_native()) == names
