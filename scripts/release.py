r"""Release pipeline for the `iec61850` PyPI package.

Chains build, sanitize, audit, smoke test, and upload into one script. Every
stage fails hard: any sign of a build-host path leak, a leftover SBOM, or a
file outside the wheel whitelist aborts before anything is published.

The token comes from the `TWINE_PASSWORD` environment variable (username
defaults to `__token__`); it is never written to this script or to disk.
Dry-run mode builds and audits but skips the upload.

Usage
=====

    # Full run, Windows plus Linux wheels; upload asks for typed confirmation
    $env:TWINE_PASSWORD = "pypi-AgEI..."
    uv run python scripts/release.py

    # Build, audit, and smoke test only
    uv run python scripts/release.py --dry-run

    # Windows wheel alone, for a host without docker
    uv run python scripts/release.py --platform windows

    # Publish to TestPyPI first
    uv run python scripts/release.py --repository testpypi

Stages (see main() for the exact order):
  L1   pre-flight         refuse an sdist, clear stale wheels, check docker
  L1.5 PyPI duplicate     the same version already published aborts the run
  L1.7 pytest             the whole suite against the development install
  L2   build per platform RUSTFLAGS path remaps plus --strip
  L2.5 binary leak audit  audit_binary.scan over the .pyd / .so
  L3   strip SBOM         remove dist-info/sboms/ and rewrite RECORD
  L4   wheel content      whitelist of files; substring scan of the text ones
  L5   smoke test         clean venv (Windows) or docker python image (Linux)
  L6   upload             twine upload after typed confirmation
  L7   post-verify        the PyPI JSON API lists the new version

A failing stage exits non-zero and leaves the wheel in target/wheels/ for
inspection.
"""

from __future__ import annotations

import sys

# The default Windows console encoding cannot render the non-ASCII
# characters this script prints, and PYTHONUTF8 has to be set before the
# interpreter starts, which a script cannot do for itself.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Literal, NoReturn

PROJECT = Path(__file__).resolve().parents[1]
WHEELS_DIR = PROJECT / "target" / "wheels"
CARGO_TOML = PROJECT / "Cargo.toml"

# Import sibling scripts as modules (not subprocess) for fast in-process calls
# + real exceptions instead of exit-code sniffing.
sys.path.insert(0, str(PROJECT / "scripts"))
import audit_binary  # noqa: E402
import strip_sbom  # noqa: E402

PACKAGE_NAME = "iec61850"
Platform = Literal["windows", "linux"]

MATURIN_IMAGE = "ghcr.io/pyo3/maturin:latest"
SMOKE_IMAGE = "python:3.13-slim"

# Wheel content whitelist — exactly one native + the 5 facade files + 3 dist-info.
ALLOWED_PAYLOAD_BASE = frozenset(
    [
        "iec61850/__init__.py",
        "iec61850/_dataclasses.py",
        "iec61850/_enums.py",
        "iec61850/_facade.py",
        "iec61850/_native.pyi",
        "iec61850/py.typed",
    ]
)
ALLOWED_NATIVES = frozenset(
    [
        "iec61850/_native.pyd",        # Windows
        "iec61850/_native.abi3.so",    # Linux abi3
        "iec61850/_native.so",         # Linux non-abi3 fallback
    ]
)
# `licenses/` carries the PEP 639 license files declared by
# `[project] license-files` in pyproject.toml; maturin places them under
# dist-info and they are required wheel content, not a leak.
ALLOWED_DIST_INFO = frozenset(
    [
        "METADATA",
        "WHEEL",
        "RECORD",
        "licenses/LICENSE-MIT",
        "licenses/LICENSE-APACHE",
    ]
)

# Forbidden substrings for the text-file scan in the wheel content audit.
# It catches build-host path fragments only: `crates/` is deliberately absent
# because the README links to https://github.com/.../tree/main/crates/... on
# purpose, and the binary leak audit (L2.5) covers real build-time paths.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    ".cargo/git/checkouts",
    ".cargo\\git\\checkouts",
)


# ─── shared helpers ──────────────────────────────────────────────────────


class ReleaseError(RuntimeError):
    """Hard-fail in any release stage. Caught at __main__ to print + exit non-zero."""


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def info(msg: str) -> None:
    print(f"  {msg}")


def fail(msg: str) -> NoReturn:
    raise ReleaseError(msg)


def run_cmd(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run subprocess; raise ReleaseError on non-zero, streaming output."""
    proc = subprocess.run(argv, cwd=cwd, env=env, text=True)
    if proc.returncode != 0:
        fail(f"command failed (exit {proc.returncode}): {' '.join(argv)}")


def read_cargo_version() -> str:
    text = CARGO_TOML.read_text(encoding="utf-8")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not m:
        fail(f"could not find version in {CARGO_TOML}")
    return m.group(1)


def _find_wheels_for(version: str, platform: Platform) -> list[Path]:
    if platform == "windows":
        pattern = f"{PACKAGE_NAME}-{version}-cp311-abi3-win_amd64.whl"
    else:
        # manylinux tag varies (manylinux_2_17_x86_64, manylinux2014_x86_64, ...)
        pattern = f"{PACKAGE_NAME}-{version}-cp311-abi3-manylinux*.whl"
    return list(WHEELS_DIR.glob(pattern))


# ─── L1: pre-flight ──────────────────────────────────────────────────────


def stage_preflight(version: str, *, platforms: list[Platform]) -> None:
    step("L1 pre-flight")
    info(f"package   = {PACKAGE_NAME}")
    info(f"version   = {version} (from {CARGO_TOML.relative_to(PROJECT)})")
    info(f"platforms = {', '.join(platforms)}")

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)

    sdists = list(WHEELS_DIR.glob("*.tar.gz"))
    if sdists:
        listing = "\n    ".join(str(p.relative_to(PROJECT)) for p in sdists)
        fail(
            "refusing to proceed: sdist(s) present in target/wheels/. This "
            "pipeline publishes wheels only; an sdist cannot be built by a "
            "consumer without a Rust toolchain and the tagged git dependency, "
            f"so delete them first:\n    {listing}"
        )
    info("no sdist artifacts present")

    for plat in platforms:
        for stale in _find_wheels_for(version, plat):
            stale.unlink()
            info(f"removed stale {stale.name}")

    if "linux" in platforms:
        _ensure_docker_running()


def _ensure_docker_running() -> None:
    proc = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(
            "linux build requires docker but `docker version` failed:\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    info(f"docker server {proc.stdout.strip()} ready")


# ─── L1.5: PyPI duplicate check ──────────────────────────────────────────


def stage_check_not_published(version: str, repository: str) -> None:
    step("L1.5 PyPI duplicate version check")
    base = "https://pypi.org" if repository == "pypi" else "https://test.pypi.org"
    url = f"{base}/pypi/{PACKAGE_NAME}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            info(f"{PACKAGE_NAME} not yet on {repository} (first release) — ok")
            return
        fail(f"PyPI lookup failed: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        fail(f"PyPI lookup failed: {e}")

    releases = list(data.get("releases", {}).keys())
    if version in releases:
        fail(
            f"version {version} already published on {repository} — "
            f"bump Cargo.toml version before re-running"
        )
    # Semantic sort — string sort gives '0.7.0' > '0.10.0'. packaging is a
    # transitive dep via twine, so it's already in the dev env.
    from packaging.version import InvalidVersion, Version

    def _key(v: str) -> Version:
        try:
            return Version(v)
        except InvalidVersion:
            return Version("0!0")  # sort malformed entries to the bottom

    latest = sorted(releases, key=_key)[-3:] if releases else []
    info(f"{version} not yet on {repository}; latest published: {latest}")


# ─── L1.7: pytest ────────────────────────────────────────────────────────


def stage_pytest() -> None:
    """Run the whole suite against the development install.

    This is far stricter than the L5 smoke test: it drives the
    end-to-end scenarios against the servers the fixtures host. It runs
    before the build so a failure costs no wheel or container time.
    """
    step("L1.7 pytest")
    run_cmd(["uv", "run", "pytest", "tests/", "-q"], cwd=PROJECT)
    info("pytest passed")


# ─── L2: build ───────────────────────────────────────────────────────────


def _build_rustflags(platform: Platform) -> str:
    """Build RUSTFLAGS carrying one `--remap-path-prefix` per host directory.

    Dependency sources live in the cargo git checkout and registry caches, so
    remapping those two roots plus the package directory, the rustup sysroot,
    and the rustc source prefix covers every absolute path rustc bakes into
    debug info and panic locations. Repository-relative paths such as
    `crates/iec61850-mms/src/...` survive on purpose: both repositories are
    public, and a panic message that names the file is useful.
    """
    remaps: list[str] = []

    if platform == "windows":
        cargo_home = Path(os.environ.get("CARGO_HOME", os.path.expanduser("~/.cargo")))
        rustup_home = Path(
            os.environ.get("RUSTUP_HOME", os.path.expanduser("~/.rustup"))
        )
        remaps.append(f"--remap-path-prefix={cargo_home / 'git' / 'checkouts'}=git")
        remaps.append(f"--remap-path-prefix={cargo_home / 'registry' / 'src'}=cargo")
        remaps.append(f"--remap-path-prefix={rustup_home / 'toolchains'}=rust")
        remaps.append(f"--remap-path-prefix={PROJECT}=pkg")
    else:  # linux, inside the manylinux container
        remaps.append("--remap-path-prefix=/root/.cargo/git/checkouts=git")
        remaps.append("--remap-path-prefix=/root/.cargo/registry/src=cargo")
        remaps.append("--remap-path-prefix=/root/.rustup/toolchains=rust")
        remaps.append("--remap-path-prefix=/io=pkg")

    remaps.append("--remap-path-prefix=/rustc=rust")
    return " ".join(remaps)


def stage_build(version: str, platform: Platform) -> Path:
    step(f"L2 build {platform} wheel")
    rustflags = _build_rustflags(platform)
    info(f"RUSTFLAGS: {rustflags.count('--remap-path-prefix=')} path remaps applied")

    if platform == "windows":
        env = os.environ.copy()
        env["RUSTFLAGS"] = rustflags
        run_cmd(
            ["uv", "run", "maturin", "build", "--release", "--strip"],
            cwd=PROJECT,
            env=env,
        )
    else:  # linux, docker manylinux
        run_cmd(
            [
                "docker", "run", "--rm",
                "-e", f"RUSTFLAGS={rustflags}",
                "-v", f"{PROJECT}:/io",
                "-w", "/io",
                MATURIN_IMAGE,
                "build", "--release", "--strip",
            ]
        )

    wheels = _find_wheels_for(version, platform)
    if not wheels:
        fail(f"no {platform} wheel produced")
    if len(wheels) > 1:
        # Multiple wheels with same version implies tag change; keep newest.
        wheels.sort(key=lambda p: p.stat().st_mtime)
        for old in wheels[:-1]:
            old.unlink()
            info(f"removed duplicate {old.name}")
        wheels = [wheels[-1]]
    info(
        f"built {wheels[0].relative_to(PROJECT)} "
        f"({wheels[0].stat().st_size:,} bytes)"
    )
    return wheels[0]


# ─── L2.5: binary leak audit ─────────────────────────────────────────────


def stage_binary_leak_audit(wheel: Path) -> None:
    """Extract the native module from the wheel and scan via `audit_binary.scan`."""
    step(f"L2.5 binary leak audit ({wheel.name})")
    with tempfile.TemporaryDirectory() as tmpdir, zipfile.ZipFile(wheel, "r") as z:
        natives = [n for n in z.namelist() if n in ALLOWED_NATIVES]
        if len(natives) != 1:
            fail(f"expected exactly 1 native module, found: {natives}")
        extracted = Path(z.extract(natives[0], tmpdir))
        print(f"  scanning {natives[0]} ...")
        hits = audit_binary.scan(extracted)
    if hits:
        fail(f"binary leak audit failed for {wheel.name}: {hits} hit(s)")
    info("binary leak audit clean")


# ─── L3: sanitize (strip SBOM) ───────────────────────────────────────────


def stage_sanitize(wheel: Path) -> None:
    step(f"L3 strip SBOM ({wheel.name})")
    rc = strip_sbom.strip_sbom(wheel)
    if rc != 0:
        fail(f"strip_sbom returned {rc} for {wheel.name}")
    with zipfile.ZipFile(wheel, "r") as z:
        sboms = [n for n in z.namelist() if "/sboms/" in n]
    if sboms:
        fail(f"strip_sbom failed — still found: {sboms}")
    info("no SBOM entries remain")


# ─── L4: wheel content audit ─────────────────────────────────────────────


def stage_audit(wheel: Path, version: str) -> None:
    step(f"L4 audit wheel contents ({wheel.name})")
    dist_info_prefix = f"{PACKAGE_NAME}-{version}.dist-info/"

    with zipfile.ZipFile(wheel, "r") as z:
        names = z.namelist()

        natives = [n for n in names if n in ALLOWED_NATIVES]
        if len(natives) != 1:
            fail(
                f"expected exactly 1 native module from {sorted(ALLOWED_NATIVES)}; "
                f"found: {natives}"
            )
        allowed = ALLOWED_PAYLOAD_BASE | {natives[0]}

        unexpected = [
            n for n in names
            if n not in allowed
            and not (
                n.startswith(dist_info_prefix)
                and n[len(dist_info_prefix):] in ALLOWED_DIST_INFO
            )
        ]
        if unexpected:
            listing = "\n    ".join(unexpected)
            fail(f"wheel contains files NOT in whitelist (potential leak):\n    {listing}")
        info(f"all {len(names)} files match whitelist (native: {natives[0]})")

        missing = sorted(allowed - set(names))
        if missing:
            fail(f"wheel missing expected files: {missing}")

        # Scan METADATA + RECORD + every .py for forbidden substrings.
        text_files = [
            f"{dist_info_prefix}METADATA",
            f"{dist_info_prefix}RECORD",
            *(n for n in names if n.endswith(".py")),
        ]
        for fname in text_files:
            content = z.read(fname).decode("utf-8", errors="replace")
            hits = [s for s in FORBIDDEN_SUBSTRINGS if s in content]
            if hits:
                fail(f"forbidden substrings found in {fname}: {hits}")
        info(f"text scan clean across {len(text_files)} file(s)")


# ─── L5: smoke test ──────────────────────────────────────────────────────


def _smoke_script(version: str) -> str:
    """Single-line Python source that imports the installed iec61850 wheel
    and asserts version + key public surface. `version` is from Cargo.toml
    (regex-validated, can't contain quotes), so f-string embed is safe.
    """
    return (
        "import iec61850; "
        f"assert iec61850.__version__ == '{version}', "
        f"f'version mismatch: {{iec61850.__version__}} != {version}'; "
        "assert iec61850.FC.ST == 'ST'; "
        "assert iec61850.IedConnection is not None; "
        "assert iec61850._native is not None; "
        f"print('smoke ok:', iec61850.__version__)"
    )


def stage_smoke_test(wheel: Path, version: str, platform: Platform) -> None:
    step(f"L5 smoke test {platform} wheel ({wheel.name})")
    script = _smoke_script(version)
    if platform == "windows":
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / "venv"
            run_cmd(["uv", "venv", str(venv), "--python", "3.13", "-q"])
            env = os.environ.copy()
            env["VIRTUAL_ENV"] = str(venv)
            run_cmd(["uv", "pip", "install", "--quiet", str(wheel)], env=env)
            py = venv / "Scripts" / "python.exe"
            if not py.is_file():
                py = venv / "bin" / "python"
            if not py.is_file():
                fail(f"no python in tmp venv: {venv}")
            proc = subprocess.run(
                [str(py), "-c", script], capture_output=True, text=True
            )
            if proc.returncode != 0:
                fail(f"smoke test failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            info(proc.stdout.strip())
    else:  # linux via docker
        run_cmd(
            [
                "docker", "run", "--rm",
                "-v", f"{WHEELS_DIR}:/wheels:ro",
                SMOKE_IMAGE,
                "bash", "-c",
                f"pip install --quiet --no-cache-dir /wheels/{wheel.name} && "
                f"python -c {shlex.quote(script)}",
            ]
        )


# ─── L6: upload ──────────────────────────────────────────────────────────


def stage_confirm_and_upload(
    wheels: list[Path],
    version: str,
    *,
    repository: str,
    dry_run: bool,
) -> None:
    step("L6 upload" + (" (dry-run)" if dry_run else ""))
    info(f"target : {repository}")
    info("files  :")
    for w in wheels:
        sha = hashlib.sha256(w.read_bytes()).hexdigest()
        print(f"    - {w.name}")
        print(f"        size   : {w.stat().st_size:,} bytes")
        print(f"        sha256 : {sha}")

    if dry_run:
        info("dry-run: skipping upload")
        return

    token = os.environ.get("TWINE_PASSWORD")
    if not token:
        fail("TWINE_PASSWORD env var not set — refusing to prompt for token interactively")
    if not token.startswith("pypi-"):
        fail("TWINE_PASSWORD does not look like a PyPI token (expected 'pypi-...')")

    print(
        f"\n  about to upload {len(wheels)} wheel(s) for version {version} to {repository}.\n"
        f"  type the version literally ('{version}') to confirm, anything else aborts:",
        end=" ",
        flush=True,
    )
    typed = input().strip()
    if typed != version:
        fail(f"confirmation failed (typed {typed!r}, expected {version!r})")

    env = os.environ.copy()
    env["TWINE_USERNAME"] = "__token__"
    cmd = (
        ["uv", "run", "twine", "upload", "--repository", repository]
        + [str(w) for w in wheels]
    )
    info(f"running: twine upload --repository {repository} ({len(wheels)} file(s))")
    run_cmd(cmd, cwd=PROJECT, env=env)


# ─── L7: post-verify ─────────────────────────────────────────────────────


def stage_post_verify(version: str, *, repository: str, dry_run: bool) -> None:
    step("L7 post-verify")
    if dry_run:
        info("dry-run: skipping post-verify")
        return
    if repository != "pypi":
        info(f"non-PyPI repository ({repository}); skipping JSON API check")
        return

    url = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
    deadline = time.monotonic() + 30
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.load(resp)
            releases = data.get("releases", {}).keys()
            if version in releases:
                info(f"version {version} visible on PyPI")
                return
            last_err = f"version {version} not yet listed (have {sorted(releases)[-3:]})"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = str(e)
        time.sleep(2)
    fail(f"version {version} did not appear on PyPI within 30s: {last_err}")


# ─── main ────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Build + audit + upload iec61850 wheels")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run all stages except upload + post-verify",
    )
    parser.add_argument(
        "--repository",
        default="pypi",
        choices=["pypi", "testpypi"],
        help="twine repository (default: pypi)",
    )
    parser.add_argument(
        "--platform",
        default="all",
        choices=["windows", "linux", "all"],
        help="which wheel(s) to build/upload (default: all)",
    )
    args = parser.parse_args()

    platforms: list[Platform] = (
        ["windows", "linux"] if args.platform == "all" else [args.platform]
    )

    try:
        version = read_cargo_version()
        stage_preflight(version, platforms=platforms)
        stage_check_not_published(version, args.repository)
        stage_pytest()

        wheels: list[Path] = []
        for plat in platforms:
            w = stage_build(version, plat)
            stage_binary_leak_audit(w)
            stage_sanitize(w)
            stage_audit(w, version)
            stage_smoke_test(w, version, plat)
            wheels.append(w)

        stage_confirm_and_upload(
            wheels, version, repository=args.repository, dry_run=args.dry_run
        )
        stage_post_verify(version, repository=args.repository, dry_run=args.dry_run)
    except ReleaseError as e:
        print(f"\n[ABORT] {e}", file=sys.stderr)
        return 1

    step("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
