"""Typed attributes of the exceptions a client operation raises.

Each failure is produced on the wire against a server hosted by this package,
and the test asserts the class of the exception and the attributes it carries
rather than its message text.
"""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import _demo
import iec61850

SERVER_CN = "demo-ied"


@dataclass(frozen=True)
class Identity:
    cert: bytes
    key: bytes


def mint_identity(common_name: str) -> Identity:
    """A self-signed certificate valid for one day, naming `common_name`."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return Identity(
        cert=cert.public_bytes(serialization.Encoding.PEM),
        key=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


@pytest.fixture(scope="module")
def server_id() -> Identity:
    return mint_identity(SERVER_CN)


@pytest.fixture(scope="module")
def client_id() -> Identity:
    return mint_identity("scada-client")


async def test_untrusted_server_certificate_raises_tls_error(
    server_id: Identity,
) -> None:
    """A server certificate the client does not trust fails verification."""
    stranger = mint_identity(SERVER_CN)

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(server_cert_pem=server_id.cert, server_key_pem=server_id.key)

    async with _demo.hosted_demo(configure=configure) as server:
        tls = iec61850.TlsConfig(ca_pem=stranger.cert)
        with pytest.raises(iec61850.IedTlsError) as info:
            await iec61850.IedConnection.connect_tls(server.bound_addr, tls, SERVER_CN)
    err = info.value
    assert isinstance(err, iec61850.IedConnectionError)
    assert err.kind == "verification"
    assert isinstance(err.message, str) and err.message


async def test_client_certificate_refused_under_tls13_raises_tls_error(
    server_id: Identity, client_id: Identity
) -> None:
    """A TLS 1.3 server that refuses the client certificate sends an alert.

    Under TLS 1.3 the client learns of the refusal only after its side of the
    handshake has finished; it still surfaces as a failed handshake carrying
    the alert, not as a broken transport.
    """
    other = mint_identity("someone-else")

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(
            server_cert_pem=server_id.cert,
            server_key_pem=server_id.key,
            client_ca_pem=client_id.cert,
            allow_only_known_peers=True,
            known_peer_pems=[other.cert],
            min_tls_version="tls1.3",
        )

    async with _demo.hosted_demo(configure=configure) as server:
        tls = iec61850.TlsConfig(
            ca_pem=server_id.cert,
            client_cert_pem=client_id.cert,
            client_key_pem=client_id.key,
            min_version=iec61850.TlsVersion.TLS_1_3,
        )
        with pytest.raises(iec61850.IedTlsError) as info:
            await iec61850.IedConnection.connect_tls(server.bound_addr, tls, SERVER_CN)
    assert info.value.kind == "alert_received"


async def test_trusted_tls_session_connects(
    server_id: Identity, client_id: Identity
) -> None:
    """Control case: the same server accepts a client it trusts."""

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(
            server_cert_pem=server_id.cert,
            server_key_pem=server_id.key,
            client_ca_pem=client_id.cert,
            min_tls_version="tls1.3",
        )

    async with _demo.hosted_demo(configure=configure) as server:
        tls = iec61850.TlsConfig(
            ca_pem=server_id.cert,
            client_cert_pem=client_id.cert,
            client_key_pem=client_id.key,
            min_version=iec61850.TlsVersion.TLS_1_3,
        )
        conn = await iec61850.IedConnection.connect_tls(server.bound_addr, tls, SERVER_CN)
        try:
            assert await conn.read_bool(_demo.IND1, iec61850.FC.ST) is True
        finally:
            await conn.disconnect()


async def test_required_client_certificate_refuses_a_client_without_one(
    server_id: Identity, client_id: Identity
) -> None:
    """`require_client_auth=True` refuses a client that presents no
    certificate, where the same server without it accepts that client."""

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(
            server_cert_pem=server_id.cert,
            server_key_pem=server_id.key,
            client_ca_pem=client_id.cert,
            require_client_auth=True,
            min_tls_version="tls1.3",
        )

    async with _demo.hosted_demo(configure=configure) as server:
        anonymous = iec61850.TlsConfig(
            ca_pem=server_id.cert, min_version=iec61850.TlsVersion.TLS_1_3
        )
        with pytest.raises(iec61850.IedTlsError):
            await iec61850.IedConnection.connect_tls(
                server.bound_addr, anonymous, SERVER_CN
            )

        trusted = iec61850.TlsConfig(
            ca_pem=server_id.cert,
            client_cert_pem=client_id.cert,
            client_key_pem=client_id.key,
            min_version=iec61850.TlsVersion.TLS_1_3,
        )
        conn = await iec61850.IedConnection.connect_tls(
            server.bound_addr, trusted, SERVER_CN
        )
        try:
            assert await conn.read_bool(_demo.IND1, iec61850.FC.ST) is True
        finally:
            await conn.disconnect()


async def test_optional_client_certificate_admits_a_client_without_one(
    server_id: Identity, client_id: Identity
) -> None:
    """With a client CA but without `require_client_auth`, a client that
    presents no certificate is admitted."""

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(
            server_cert_pem=server_id.cert,
            server_key_pem=server_id.key,
            client_ca_pem=client_id.cert,
            min_tls_version="tls1.3",
        )

    async with _demo.hosted_demo(configure=configure) as server:
        anonymous = iec61850.TlsConfig(
            ca_pem=server_id.cert, min_version=iec61850.TlsVersion.TLS_1_3
        )
        conn = await iec61850.IedConnection.connect_tls(
            server.bound_addr, anonymous, SERVER_CN
        )
        try:
            assert await conn.read_bool(_demo.IND1, iec61850.FC.ST) is True
        finally:
            await conn.disconnect()


async def test_required_client_certificate_without_a_ca_fails_to_start(
    server_id: Identity,
) -> None:
    server = iec61850.IedServer.from_scl(_demo.DEMO_CID, ied_name=_demo.IED_NAME)
    server.bind("127.0.0.1:0")
    server.with_tls(
        server_cert_pem=server_id.cert,
        server_key_pem=server_id.key,
        require_client_auth=True,
    )
    with pytest.raises(ValueError, match="trust anchor"):
        await server.start()
    assert not server.is_running


async def test_missing_object_carries_the_data_access_code(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedDataAccessError) as info:
            await conn.read_bool(f"{_demo.DOMAIN}/GGIO1.NoSuchDO.stVal", iec61850.FC.ST)
        assert info.value.code == 10  # object-non-existent
    finally:
        await conn.disconnect()


async def test_attributes_that_do_not_apply_read_none(demo_server: str) -> None:
    conn = await iec61850.IedConnection.connect(demo_server)
    try:
        with pytest.raises(iec61850.IedServiceError) as info:
            await conn.create_data_set(
                f"{_demo.DOMAIN}/LLN0.dsMissing",
                [
                    iec61850.DataSetMember(
                        object_ref=f"{_demo.DOMAIN}/GGIO1.NoSuchDO.stVal",
                        fc=iec61850.FC.ST,
                    )
                ],
            )
        err = info.value
        assert err.reason == "service-error"
        assert isinstance(err.error_class, int)
        assert isinstance(err.code, int)
        assert err.reject_type is None
        assert err.number_deleted is None
        assert err.pdu_size is None
        assert err.limit is None
    finally:
        await conn.disconnect()


def test_exception_classes_declare_their_attributes() -> None:
    """Every attribute reads ``None`` on an instance that did not set it."""
    assert issubclass(iec61850.IedTlsError, iec61850.IedConnectionError)
    assert iec61850.IedTimeoutError("x").timeout_ms is None
    assert iec61850.IedConnectionError("x").reason is None
    assert iec61850.IedTlsError("x").kind is None
    assert iec61850.IedServiceError("x").error_class is None
    assert iec61850.IedDataAccessError("x").code is None
    assert iec61850.IedServerError("x").reference is None


async def test_unanswered_request_raises_timeout_with_its_deadline() -> None:
    """A read the server holds past the request timeout raises IedTimeoutError.

    The read handler blocks until the test releases it, which happens only
    after the client has given up, so the order of events is fixed. The
    request timeout also bounds the session, ACSE and Initiate exchanges of
    the connect, so it is long enough for those to finish on a loaded host.
    """
    release = threading.Event()

    def stall(_path: str) -> None:
        release.wait(10)

    def configure(server: iec61850.IedServer) -> None:
        server.on_read(_demo.SRV_IND1, stall)

    async with _demo.hosted_demo(configure=configure) as server:
        conn = await iec61850.IedConnection.connect(
            server.bound_addr, request_timeout_ms=2000
        )
        try:
            with pytest.raises(iec61850.IedTimeoutError) as info:
                await conn.read_bool(_demo.IND1, iec61850.FC.ST)
            assert not isinstance(info.value, iec61850.IedServiceError)
            assert info.value.timeout_ms == 2000
        finally:
            release.set()
            await conn.abort()
