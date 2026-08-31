"""Import check for an installed `iec61850` wheel, runnable without pytest.

Not collected by the suite: it takes no fixture and needs no server, so it can
run inside a clean environment that has only the wheel installed — which is
what `scripts/release.py` does before publishing, and what CI does after
installing the wheel it just built.

Every check raises on failure and the script exits non-zero, so a regression
cannot pass silently.
"""

import asyncio

import iec61850


def check(label: str, condition: bool, detail: object = "") -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"ok  {label}")


check("version is a non-empty string", bool(iec61850.__version__), iec61850.__version__)
check(
    "__all__ is fully importable",
    all(hasattr(iec61850, name) for name in iec61850.__all__),
    [n for n in iec61850.__all__ if not hasattr(iec61850, n)],
)
check("FC.MX spelling", iec61850.FC.MX.value == "MX", iec61850.FC.MX.value)
check(
    "AcsiClass.BUFFERED_REPORT spelling",
    iec61850.AcsiClass.BUFFERED_REPORT.value == "BR",
    iec61850.AcsiClass.BUFFERED_REPORT.value,
)
check(
    "exception hierarchy",
    issubclass(iec61850.IedConnectionError, iec61850.IedError)
    and issubclass(iec61850.IedTimeoutError, iec61850.IedError),
)

cfg = iec61850.Iec61850ClientConfig(address="127.0.0.1")
check("client config defaults", (cfg.port, cfg.timeout_ms) == (102, 5000), cfg)

trg = iec61850.TriggerOptions(data_change=True, integrity=True)
check(
    "trigger option bits round-trip",
    iec61850.TriggerOptions.from_bits(trg.to_bits()) == trg,
    trg.to_bits(),
)
check("write mask is non-empty", iec61850.RcbWriteMask.all().bits != 0)

# The native extension has to be loaded, not merely shadowed by the pure-Python
# facade: an sdist that failed to compile would still satisfy every check above.
check("native extension loaded", iec61850._native.__version__ == iec61850.__version__)


async def _refused_connect() -> str:
    """Connecting where nothing listens must fail inside the error hierarchy."""
    try:
        await iec61850.IedConnection.connect("127.0.0.1:1", timeout_ms=500)
    except iec61850.IedError as exc:
        return type(exc).__name__
    raise AssertionError("connect to a dead port unexpectedly succeeded")


check("refused connect raises IedError", bool(asyncio.run(_refused_connect())))

print("=== ALL OK ===")
