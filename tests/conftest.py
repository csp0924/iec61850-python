"""Session fixtures for the end-to-end tests.

Each fixture starts a server hosted by this package on an OS-assigned
loopback port and yields its `host:port`. Servers are session-scoped because
starting one costs a bind and an accept loop, not a process. See `_demo` for
the model constants and the context managers these wrap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

import _demo


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def demo_server() -> AsyncIterator[str]:
    """The CID model with its data sets and unbuffered RCB bound."""
    async with _demo.hosted_demo() as server:
        yield server.bound_addr


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def journal_server() -> AsyncIterator[str]:
    """The CID model with an LCB holding the seeded journal entries."""
    async with _demo.hosted_journal() as server:
        yield server.bound_addr


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def writable_server() -> AsyncIterator[str]:
    """A programmatic model exposing setpoints under FC=SP."""
    async with _demo.hosted_writable() as server:
        yield server.bound_addr
