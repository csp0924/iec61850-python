"""Synchronous validation tests for ``IedConnection.connect_tls``."""

from __future__ import annotations

import pytest

import iec61850


def test_partial_client_auth_rejected() -> None:
    cfg = iec61850.TlsConfig(
        ca_pem=b"placeholder",
        client_cert_pem=b"placeholder",
        client_key_pem=None,
    )
    coro = iec61850.IedConnection.connect_tls("127.0.0.1:3782", cfg, "demo-server")
    with pytest.raises(ValueError, match="must be provided together"):
        coro.send(None)


def test_invalid_server_name_rejected_synchronously() -> None:
    cfg = iec61850.TlsConfig(ca_pem=b"placeholder")
    coro = iec61850.IedConnection.connect_tls(
        "127.0.0.1:3782", cfg, "not a valid dns name"
    )
    with pytest.raises(ValueError, match="invalid server_name"):
        coro.send(None)


def test_address_bad_format() -> None:
    cfg = iec61850.TlsConfig(ca_pem=b"placeholder")
    coro = iec61850.IedConnection.connect_tls("no-port-here", cfg, "demo-server")
    with pytest.raises(ValueError, match="must be 'host:port'"):
        coro.send(None)


def test_invalid_pem_rejected_synchronously() -> None:
    cfg = iec61850.TlsConfig(ca_pem=b"not a pem at all")
    coro = iec61850.IedConnection.connect_tls("127.0.0.1:3782", cfg, "demo-server")
    with pytest.raises(ValueError, match="CA PEM|no certificates"):
        coro.send(None)


def test_invalid_min_version_rejected_synchronously() -> None:
    # Drive the native classmethod to bypass the dataclass enum guard — the
    # native layer must validate the token before scheduling the future.
    with pytest.raises(ValueError, match="invalid TLS version"):
        iec61850._native.IedConnection.connect_tls(
            "127.0.0.1:3782",
            server_name="demo-server",
            ca_pem=b"placeholder",
            min_tls_version="tls1.0",
        )


def test_known_peer_bad_pem_rejected() -> None:
    cfg = iec61850.TlsConfig(
        ca_pem=b"placeholder",
        known_peer_pems=(b"not a real pem",),
        allow_only_known_peers=True,
    )
    coro = iec61850.IedConnection.connect_tls("127.0.0.1:3782", cfg, "demo-server")
    # The bad CA PEM is parsed first, so the user-visible error pins on either
    # the CA path or the known-peer path depending on order — both are valid.
    with pytest.raises(ValueError, match="CA PEM|known peer PEM"):
        coro.send(None)
