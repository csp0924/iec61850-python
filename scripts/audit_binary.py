"""Scan a compiled wheel native module for build-host path leaks.

Run after building wheels but before publishing, to confirm `--release`
plus `--remap-path-prefix` actually stripped the panic-location source paths
so the shipped binary does not carry paths from the machine that built it.

Crate names and repository-relative source paths (`crates/iec61850-mms/...`)
are not scanned: both repositories are public and the README links to those
paths on purpose.

Module-level API (called by release.py):
- `scan(path) -> int`: returns count of leak hits (0 = clean)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Each pattern hunts for one class of build-host path leak in the binary's
# bytes. Patterns scan the raw byte buffer directly: they all require
# contiguous printable ASCII, so non-printable separators in the binary
# segment matches without needing a pre-extracted string list.
#
# `/rustc/<commit-hash>/library/...` is deliberately NOT a pattern: that is
# the compiler's own source-path virtualization for the precompiled standard
# library, identical for every user of the same toolchain and outside the
# reach of `--remap-path-prefix`. It names a public rustc commit, never the
# build host. Host-specific toolchain paths are what the profile, home
# directory and rustup patterns below catch.
LEAK_PATTERNS: list[tuple[str, bytes]] = [
    ("windows user profile path", rb"[A-Za-z]:[\/][Uu]sers[\/][A-Za-z0-9._-]+"),
    ("unix home directory path", rb"/home/[a-z0-9._-]+/"),
    # A container-root leak is an absolute path, so `/root/` or `/io/` must
    # not be preceded by a path character: without the lookbehind, crate
    # module paths such as `tokio-*/src/io/util/` match on the `/io/` in the
    # middle and produce false hits.
    ("container build root", rb"(?<![A-Za-z0-9._\-/])/(?:root|io)/\.?[a-z]+/"),
    ("cargo registry checkout", rb"[\/]\.cargo[\/](?:registry|git)[\/]"),
    ("rustup toolchain path", rb"[\/]\.rustup[\/]toolchains[\/]"),
]


def scan(path: Path) -> int:
    """Scan `path` (a compiled .pyd / .so) for embedded leak strings.

    Returns the total unique-hit count across all patterns; 0 means clean.
    Prints a per-pattern ok / LEAK line for human review.
    """
    data = path.read_bytes()
    total_hits = 0
    for label, pat in LEAK_PATTERNS:
        hits = set(re.findall(pat, data))
        if hits:
            total_hits += len(hits)
            print(f"  LEAK [{label}]:")
            for h in sorted(hits)[:20]:
                print(f"    {h.decode('utf-8', errors='replace')}")
            if len(hits) > 20:
                print(f"    ... ({len(hits) - 20} more)")
        else:
            print(f"  ok   [{label}]: 0 hits")
    return total_hits


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: audit_binary.py <native.pyd|native.so> ...", file=sys.stderr)
        return 2
    grand_total = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        print(f"\n=== {path} ===")
        grand_total += scan(path)
    print()
    if grand_total == 0:
        print("OK: no leaks detected")
        return 0
    print(f"FAIL: {grand_total} leak hit(s) total")
    return 1


if __name__ == "__main__":
    sys.exit(main())
