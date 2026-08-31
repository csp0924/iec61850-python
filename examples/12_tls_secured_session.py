"""Mutual TLS handshake against the demonstration IED.

Generates throw-away self-signed identities for server and client, then
hands them to `IedServer.with_tls` and `IedConnection.connect_tls`. For
production, replace `mint_identity` with PEMs loaded from your trust store.

Requires `cryptography` (`pip install cryptography`).

    python 12_tls_secured_session.py

Expected stdout:

    over TLS: Ind1 is set
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import sys
from dataclasses import dataclass

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit("This example requires `pip install cryptography`.")

import iec61850
from _shared import DEMO, spawn_demo


@dataclass(frozen=True, slots=True)
class Identity:
    cert: bytes
    key: bytes


def mint_identity(common_name: str, *, days: int = 1) -> Identity:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _dt.timedelta(days=days))
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


def attach_tls(server_id: Identity, client_id: Identity):
    """Return a callback that wires the server with the given identities."""

    def configure(server: iec61850.IedServer) -> None:
        server.with_tls(
            server_cert_pem=server_id.cert,
            server_key_pem=server_id.key,
            client_ca_pem=client_id.cert,  # trust the client identity directly
            min_tls_version="tls1.2",
        )

    return configure


async def main() -> None:
    server_cn = "demo-ied"
    server_id = mint_identity(server_cn)
    client_id = mint_identity("scada-client")

    async with spawn_demo(configure=attach_tls(server_id, client_id)) as server:
        tls = iec61850.TlsConfig(
            ca_pem=server_id.cert,
            client_cert_pem=client_id.cert,
            client_key_pem=client_id.key,
            min_version=iec61850.TlsVersion.TLS_1_2,
        )
        conn = await iec61850.IedConnection.connect_tls(
            server.bound_addr, tls, server_cn
        )
        try:
            state = await conn.read_bool(DEMO.ind1, iec61850.FC.ST)
            print(f"over TLS: Ind1 is {'set' if state else 'clear'}")
        finally:
            await conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
