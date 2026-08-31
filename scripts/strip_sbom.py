r"""Strip the CycloneDX SBOM from a built wheel.

maturin generates `<dist-info>/sboms/<crate>.cyclonedx.json`, which lists
every dependency crate together with the local path cargo resolved it from:

    pkg:cargo/iec61850-client@0.1.0?download_url=file://...

Those paths belong to the machine that built the wheel, so this removes the
whole `sboms/` directory before publishing and rewrites RECORD to match.

Usage::

    uv run python scripts/strip_sbom.py target/wheels/iec61850-0.13.0-cp311-abi3-win_amd64.whl

Idempotent: a wheel that carries no SBOM is left alone.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def strip_sbom(wheel_path: Path) -> int:
    if not wheel_path.is_file():
        print(f"error: wheel not found: {wheel_path}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(wheel_path, "r") as z:
        names = z.namelist()
        sbom_entries = [n for n in names if "/sboms/" in n]
        record_path = next((n for n in names if n.endswith(".dist-info/RECORD")), None)

    if not sbom_entries:
        print(f"noop: no SBOM entries in {wheel_path.name}")
        return 0
    if record_path is None:
        print(f"error: no RECORD file in {wheel_path}", file=sys.stderr)
        return 2

    print(f"stripping {len(sbom_entries)} SBOM entr{'y' if len(sbom_entries) == 1 else 'ies'}:")
    for n in sbom_entries:
        print(f"  - {n}")

    with tempfile.TemporaryDirectory() as tmpdir:
        new_wheel = Path(tmpdir) / wheel_path.name
        with zipfile.ZipFile(wheel_path, "r") as src, zipfile.ZipFile(
            new_wheel, "w", compression=zipfile.ZIP_DEFLATED
        ) as dst:
            for item in src.infolist():
                if "/sboms/" in item.filename:
                    continue
                data = src.read(item.filename)
                if item.filename == record_path:
                    # Each RECORD line is "path,sha256=...,size"; drop the
                    # lines whose path sits under sboms/.
                    kept = [
                        line
                        for line in data.decode("utf-8").splitlines()
                        if "/sboms/" not in line.split(",", 1)[0]
                    ]
                    data = ("\n".join(kept) + "\n").encode("utf-8")
                dst.writestr(item, data)

        shutil.move(str(new_wheel), str(wheel_path))

    print(f"ok: rewrote {wheel_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(strip_sbom(Path(sys.argv[1])))
