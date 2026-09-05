# iec61850

Async-first, type-hinted IEC 61850 client for Python.

## Scope and protocol boundaries

This package is an **MMS-only facade** over
[`iec61850-rust`](https://github.com/csp0924/iec61850-rust) — an independent
Rust implementation of IEC 61850 by the same author, itself open source under
the same `MIT OR Apache-2.0` terms and published on crates.io as the
`iec61850-*` crates. This facade covers
the connection-oriented, application-level services that fit Python's runtime
characteristics naturally:

- **Client**: read / write / control / dataset / reporting (URCB + BRCB) /
  log query / SCL parsing / directory queries / TLS
- **Server hosting**: MMS server with URCB / BRCB / LCB / SGCB / control
  handlers, declared from SCL **or** from a Python `dict` spec

**Out of scope by design**: GOOSE (IEC 61850-8-1) and Sampled Values
(IEC 61850-9-2) are hard-real-time L2 protocols (GOOSE T1 = 4 ms, SV =
250 us / 4 kHz). Their timing budgets fit Rust + SCHED_FIFO, not a GIL-bound
runtime with non-deterministic GC pauses. Misrepresenting them as "supported"
in Python would invite production protection-scheme failures.

If you need GOOSE / SV publish or subscribe, use the upstream Rust crates
directly (same author, same wire-level implementation):

- [`iec61850-goose`](https://crates.io/crates/iec61850-goose) —
  GOOSE publisher / subscriber / receiver (Ethernet L2 raw socket, frame
  template, retransmission state machine)
- [`iec61850-sv`](https://crates.io/crates/iec61850-sv) —
  Sampled Values 4 kHz publisher + subscriber (Linux SCHED_FIFO publish loop,
  p99 jitter target < 200 us)

The PICS table below uses a four-column `Py-C / Py-S / Rust-C / Rust-S`
breakdown precisely so an unsupported Python row points you at the right
Rust crate.

## Features

- TCP connect / disconnect with timeout
- TLS connect (IEC 62351-3 cipher whitelist, mutual TLS, TLS 1.2 + 1.3,
  known-peer pinning, CRL, configurable validation knobs)
- Per-connection tuning: request timeout, max outstanding invocations, local
  max PDU size
- High-level `Iec61850Client` async context manager wrapping the lifecycle of
  an `IedConnection` plus an optional background `ReportDispatcher`
- Typed scalar read / write: `bool`, `int32`, `int64`, `uint32`, `float`,
  `float64`, `string`, `timestamp` (decoded to `datetime`), `quality`
  (decoded to a `Quality` dataclass)
- Generic `read` / `write` with array-element and sub-component selection
- Directory queries: `get_server_directory`, `get_logical_device_directory`,
  `get_logical_node_directory(AcsiClass)`, `get_data_directory`
- Schema introspection: `get_variable_specification(ref, fc)` (recursive MMS
  type tree) and `get_device_model()` (per-LD named-variable index)
- Dataset admin: `create_data_set`, `delete_data_set`,
  `get_data_set_values`, `set_data_set_values`
- Connection control: `disconnect` (graceful) and `abort` (rude close —
  drop TCP without sending MMS Conclude)
- URCB / BRCB reporting: `get_rcb_values`, `set_rcb_values`,
  `install_report_handler`, `poll_reports`, background `ReportDispatcher`
- Log service: `query_journal_by_time` and `query_journal_after_entry` for
  paginating Log Control Block contents
- SCL / ICD / CID document parser: `load_scl(path)` / `parse_scl(xml)` →
  `Scl` handle exposing IED inventory, the full document as a nested dict,
  and a canonical text summary per IED
- Server hosting: `IedServer.from_scl(path, ied_name=...)` instantiates an
  MMS server from an SCL document, or `IedServer.from_model_spec(spec)` from
  a declarative Python dict when the IED is generated in code rather than
  authored as XML. Bind, configure (vendor / model name /
  max connections), enter via `async with`, push value updates with typed
  `update_*` methods, intercept reads / writes with `on_read` / `on_write`
  callbacks, serve control commands (SBO / direct, normal / enhanced)
  with `on_control` — `operate` and `wait` callbacks may be either sync
  or `async` — expose URCB / BRCB reporting via `add_dataset` /
  `register_urcb` / `register_brcb`, host Log Control Blocks via
  `register_log_control` + `log_value` (in-memory journal backend,
  `ReadJournal` over MMS), wire Setting Group callbacks per LD with
  `register_setting_group_handler` + `force_active_setting_group`;
  atomic multi-attribute updates with `with server.batch():`; terminate
  TLS at the server with `with_tls()` (same IEC 62351-3 profile as the
  client, plus client-cert pinning)
- Control: `select`, `select_with_value`, `operate`, `cancel` across the four
  IEC 61850 control models (`direct-normal` / `direct-enhanced` /
  `sbo-normal` / `sbo-enhanced`)
- Typed exception hierarchy: `IedError`, `IedConnectionError`,
  `IedTimeoutError`, `IedDataAccessError`, `IedServiceError`,
  `IedControlError`, `IedServerError`
- SNTP / NTP client: `query_sntp(addr, timeout_s)` returns an `SntpResponse`
  with server time, clock offset, and round-trip estimate computed per
  RFC 4330 §5
- Type stubs (`_native.pyi`) and a `py.typed` marker for full mypy / pyright
  coverage of the public surface
- Cookbook of runnable examples under `examples/` (quickstart client, server
  from SCL, server from model dict, BRCB reporting, control SBO, SNTP query)

## Install

```bash
pip install iec61850
```

Requires Python 3.11+. Wheels are published for Windows x86_64 and Linux x86_64
(manylinux 2014); macOS arm64 is built and tested in CI but not yet published.
They are `abi3` wheels: one build serves every supported Python 3.11 or later.

## Building from source

Only needed to work on the package itself; installing from PyPI needs no Rust
toolchain. You will need a stable Rust toolchain (edition 2021, MSRV 1.88) and
[`uv`](https://docs.astral.sh/uv/).

The Rust side is not vendored. `Cargo.toml` takes each crate from the
implementation repository at a release tag:

```toml
iec61850-client = { git = "https://github.com/csp0924/iec61850-rust", tag = "v0.2.0", features = ["tls"] }
```

Once that tag exists, a build is just:

```bash
uv sync                       # dev environment, including maturin and pytest
uv run maturin develop --release
uv run pytest -q
```

### Building against a local checkout

To build against an unreleased `iec61850-rust` — before the tag exists, or while
changing both repositories together — clone it next to this one so the two share
a parent directory:

```
<parent>/
  iec61850-rust/
  iec61850-python/
```

Then create `.cargo/config.toml`, which is git-ignored precisely so this
override never ships:

```toml
[patch."https://github.com/csp0924/iec61850-rust"]
iec61850-client = { path = "../iec61850-rust/crates/iec61850-client" }
iec61850-model  = { path = "../iec61850-rust/crates/iec61850-model" }
iec61850-mms    = { path = "../iec61850-rust/crates/iec61850-mms" }
iec61850-scl    = { path = "../iec61850-rust/crates/iec61850-scl" }
iec61850-server = { path = "../iec61850-rust/crates/iec61850-server" }
iec61850-sntp   = { path = "../iec61850-rust/crates/iec61850-sntp" }
iec61850-tls    = { path = "../iec61850-rust/crates/iec61850-tls" }
```

A patch redirects a package, but Cargo still loads the git source it is
patching before applying the patch. So while the tag does not exist yet, that
config alone is not enough — resolution fails with:

```
failed to load source for dependency `iec61850-client`
  Unable to update https://github.com/csp0924/iec61850-rust?tag=v0.2.0
```

Seed a lock file once and the patch takes over. Cargo's git backend needs an
absolute URL here, so derive one rather than hard-coding it. From the
repository root, with the sibling checkout in place:

```bash
# Absolute path to the sibling checkout. `pwd -W` is the Git-for-Windows
# spelling that yields `D:/...`; plain `pwd` covers Linux and macOS.
RUST_REPO=$(cd ../iec61850-rust && { pwd -W 2>/dev/null || pwd; })

# 1. Point the manifest at that checkout, just long enough to resolve.
sed -i "s|https://github.com/csp0924/iec61850-rust\", tag = \"v0.2.0|file://${RUST_REPO}\", branch = \"main|g" Cargo.toml
cargo generate-lockfile

# 2. Restore the manifest and relabel the source lines the lock recorded.
#    Cargo canonicalises the URL it stores, so match on the branch suffix
#    rather than on the exact spelling passed in above.
git checkout Cargo.toml
sed -i -E "s|git\+file://[^\"]*\?branch=main|git+https://github.com/csp0924/iec61850-rust?tag=v0.2.0|g" Cargo.lock
```

`Cargo.lock` is git-ignored for the same reason: it pins a revision the
published repository does not carry yet. After this, `cargo metadata` resolves
offline and reports the patched packages coming from
`../iec61850-rust/crates/*`, and `uv run maturin develop --release` builds
against your working tree — edits on the Rust side are picked up without a
commit.

Once `v0.2.0` is tagged, delete `.cargo/config.toml` and `Cargo.lock` and build
normally; none of this is needed any more.

## Conformance (PICS)

ACSI service support per IEC 61850-7-2 Edition 2.1.

- **Py-C / Py-S** — exposed through *this* Python package (client / server).
- **Rust-C / Rust-S** — supported somewhere in the upstream
  [`iec61850-rust`](https://github.com/csp0924/iec61850-rust) workspace.

Legend: `yes` supported; `—` not yet implemented; `partial` a stated subset
(see the footnote); `no, by design` deliberately not in Python (use the named
Rust crate instead); `n/a` not applicable; `(n)` footnote. The Rust columns
follow the upstream conformance statement,
[`docs/PICS.md`](https://github.com/csp0924/iec61850-rust/blob/main/docs/PICS.md),
which carries the full per-service detail.

### Application Association (§7)

| Service                        |  Py-C   |  Py-S   | Rust-C | Rust-S |
|--------------------------------|:-------:|:-------:|:------:|:------:|
| Associate (Two-Party, MMS/TCP) |   yes   |   yes   |  yes   |  yes   |
| Abort (graceful Conclude)      |   yes   |   yes   |  yes   |  yes   |
| Abort (rude — TCP drop)        |   yes   |   yes   |  yes   |  yes   |
| Release                        |   yes   |   yes   |  yes   |  yes   |
| TLS 1.2 / 1.3 (IEC 62351-3)    | yes (1) | yes (1) |  yes   |  yes   |
| Mutual TLS / pinned peer / CRL |   yes   |   yes   |  yes   |  yes   |
| Authentication (ACSE password) |    —    |    —    |   —    |   —    |

1. Includes 62351-3 cipher whitelist, `verify_hostname` knob, version
   pinning, and known-peer profile. Backed by `iec61850-tls`.

### Server class (§8)

| Service                      | Py-C | Py-S | Rust-C | Rust-S |
|------------------------------|:----:|:----:|:------:|:------:|
| GetServerDirectory           | yes  | yes  |  yes   |  yes   |
| GetServerCapabilities (Ed.2) |  —   |  —   |   —    |   —    |

### Logical Device class (§9)

| Service                   | Py-C | Py-S | Rust-C | Rust-S |
|---------------------------|:----:|:----:|:------:|:------:|
| GetLogicalDeviceDirectory | yes  | yes  |  yes   |  yes   |

### Logical Node class (§10)

| Service                 | Py-C | Py-S | Rust-C | Rust-S |
|-------------------------|:----:|:----:|:------:|:------:|
| GetLogicalNodeDirectory | yes  | yes  |  yes   |  yes   |
| GetAllDataValues        |  —   |  —   |   —    |  yes   |

### Data class (§11)

| Service                             | Py-C |  Py-S   | Rust-C | Rust-S |
|-------------------------------------|:----:|:-------:|:------:|:------:|
| GetDataValues                       | yes  | yes (2) |  yes   |  yes   |
| SetDataValues                       | yes  | yes (2) |  yes   |  yes   |
| GetDataDirectory                    | yes  |   yes   |  yes   |  yes   |
| GetDataDefinition / GetVariableSpec | yes  |   yes   |  yes   |  yes   |

2. Server applies the configured `WriteAccessPolicies` (default
   `SP | SV | SE`) and any registered `on_read` / `on_write` callbacks.

`GetDataValues` covers both the single read (`read`) and the batched form
(`read_multiple`), which carries any set of references in one MMS Read.

### Data Set class (§12)

| Service                       | Py-C |  Py-S   | Rust-C | Rust-S |
|-------------------------------|:----:|:-------:|:------:|:------:|
| GetDataSetValues              | yes  |   yes   |  yes   |  yes   |
| SetDataSetValues              | yes  |   yes   |  yes   |  yes   |
| CreateDataSet (dynamic)       | yes  |   yes   |  yes   |  yes   |
| DeleteDataSet                 | yes  |   yes   |  yes   |  yes   |
| GetDataSetDirectory (members) | yes  |   yes   |  yes   |  yes   |
| List data set names (GetLogicalNodeDirectory DS) | yes | yes | yes | yes |
| Static (SCL-defined) datasets | yes  | yes (3) |  yes   |  yes   |

3. Server registers static datasets through `add_dataset()`; either bound
   to a URCB or exposed standalone for `GetDataSetValues`.

### Substitution (§13)

| Service                              | Py-C | Py-S | Rust-C | Rust-S |
|--------------------------------------|:----:|:----:|:------:|:------:|
| Set substituted value (FC=SV writes) | yes  | yes  |  yes   |  yes   |
| Dedicated Substitution service API   |  —   |  —   |   —    |   —    |

### Setting Group Control Block — SGCB (§14)

| Service                               |  Py-C   |  Py-S   | Rust-C | Rust-S |
|---------------------------------------|:-------:|:-------:|:------:|:------:|
| SelectActiveSG / SelectEditSG         | yes (6) | yes (6) |  yes   |  yes   |
| GetSGCBValues / SetSGCBValues         | yes (6) | yes (6) |  yes   |  yes   |
| ConfirmEditSGValues                   | yes (6) | yes (6) |  yes   |  yes   |
| Setting access (FC=SG / FC=SE writes) |   yes   |   yes   |  yes   |  yes   |

6. Server-side SGCB is declared via SCL `<SettingControl numOfSGs="N"/>`
   on LN0; runtime ActSG / EditSG / ConfirmEditSG state machine plus
   reservation timeout are owned by the server. Python applications
   install per-LD callbacks (`on_act_sg` / `on_edit_sg` /
   `on_confirm`) through `register_setting_group_handler()`, and
   can `force_active_setting_group()` on startup. Clients drive SGCB
   through the existing `write()` API on the special MMS path
   (e.g. ``write("IED1LD0/LLN0.SGCB.ActSG", FC.SP, 2)``).

### Reporting — URCB / BRCB (§17)

| Service                                        | Py-C |  Py-S   | Rust-C | Rust-S |
|------------------------------------------------|:----:|:-------:|:------:|:------:|
| URCB — GetURCBValues / SetURCBValues           | yes  |   yes   |  yes   |  yes   |
| URCB — Report (TrgOps, OptFlds, BufTm, IntgPd) | yes  |   yes   |  yes   |  yes   |
| URCB — General Interrogation                   | yes  |   yes   |  yes   |  yes   |
| BRCB — GetBRCBValues / SetBRCBValues / Report  | yes  | yes (4) |  yes   |  yes   |
| Background report dispatcher                   | yes  |   n/a   |  yes   |  n/a   |

4. Server BRCB hosting via `register_brcb()` with in-memory ring buffer
   (entry-count semantics), reconnect resync, and the `update_*` trigger
   path.

### Logging — LCB / Log (§15)

| Service                           | Py-C |  Py-S   | Rust-C | Rust-S |
|-----------------------------------|:----:|:-------:|:------:|:------:|
| ReadJournal (by time / by entry)  | yes  | yes (5) |  yes   |  yes   |
| LCB — GetLCBValues                |  —   |    —    |   —    | partial (12) |
| LCB — SetLCBValues                |  —   |    —    |   —    |   —    |
| QueryLogByTime / QueryLogAfter    | yes  | yes (5) |  yes   |  yes   |
| Log purging                       |  —   |    —    |   —    |   —    |

5. Server LCB hosting via `register_log_control()` with an in-memory
   journal backend (optionally capacity-bounded, evicts oldest on
   overflow). Triggers are explicit (`log_value`); auto-trigger on
   `update_*` is not yet wired. `LogEna` toggles at runtime via
   `set_log_ena`.

<!-- -->

12. The Rust server serves `LogEna`, `LogRef`, `DatSet`, `TrgOps` and
    `IntgPd`, individually or as one structure; the four buffer-cursor
    attributes (`OldEntrTm` / `NewEntrTm` / `OldEntr` / `NewEntr`) answer
    `object-access-unsupported`.

### Generic Substation Event — GOOSE / GSE (§18) (7)

| Service                 |     Py-C      |     Py-S      | Rust-C | Rust-S |
|-------------------------|:-------------:|:-------------:|:------:|:------:|
| GoCB — Get / Set values | no, by design | no, by design |  yes   |  yes   |
| GOOSE publish           | no, by design | no, by design |  n/a   |  yes   |
| GOOSE subscribe         | no, by design | no, by design |  yes   |  n/a   |

7. **By design, not on the Python roadmap.** GOOSE is a hard-real-time L2
   protocol (IEC 61850-8-1; T1 retransmission floor = 4 ms; protection
   schemes require deterministic dispatch). Python's GIL + non-deterministic
   GC make it unsuitable for the GOOSE hot path. Use
   [`iec61850-goose`](https://github.com/csp0924/iec61850-rust/tree/main/crates/iec61850-goose)
   directly — it provides `GoosePublisher` (frame template + retransmission
   state machine), `GooseSubscriber` (smpCnt continuity + Q4), and a
   typestate `GooseReceiver`. MMS-level `GoCB` administration is also
   handled there (via `GoCBRegistry` in `iec61850-server`).

### Transmission of Sampled Values — SVCB (§19) (8)

| Service                         |     Py-C      |     Py-S      | Rust-C | Rust-S |
|---------------------------------|:-------------:|:-------------:|:------:|:------:|
| MSVCB / SVCB — Get / Set values | no, by design | no, by design |   —    |   —    |
| Sampled-value publish           | no, by design | no, by design |  n/a   |  yes   |
| Sampled-value subscribe         | no, by design | no, by design |  yes   |  n/a   |

8. **By design, not on the Python roadmap.** SV runs at 4 kHz with a
   p99 jitter budget < 200 us (protection profile 256 samples/cycle is
   even tighter). This is achievable on Linux with SCHED_FIFO + raw
   socket — not from a GIL-bound runtime. Use
   [`iec61850-sv`](https://github.com/csp0924/iec61850-rust/tree/main/crates/iec61850-sv)
   directly — it provides `SvPublisher` with frame-template + hot-path
   setters, a Linux `publish_thread` (`clock_nanosleep`-based), and
   `SvSubscriber` with smpCnt continuity tracking.

### Control (§20)

| Service                   | Py-C | Py-S | Rust-C | Rust-S |
|---------------------------|:----:|:----:|:------:|:------:|
| status-only               | yes  | yes  |  yes   |  yes   |
| direct-normal             | yes  | yes  |  yes   |  yes   |
| sbo-normal                | yes  | yes  |  yes   |  yes   |
| direct-enhanced           | yes  | yes  |  yes   |  yes   |
| sbo-enhanced              | yes  | yes  |  yes   |  yes   |
| Select / SelectWithValue  | yes  | yes  |  yes   |  yes   |
| Operate / Cancel          | yes  | yes  |  yes   |  yes   |
| Test mode, ctlNum, origin | yes  | yes  |  yes   |  yes   |
| TimeActivatedOperate      |  —   |  —   |   —    | partial (13) |
| AddCause feedback         | yes  | yes  |  yes   |  yes   |

13. The Rust server decodes the timed `Oper` structure and hands `operTm`
    to the application handler, but does not itself defer execution to the
    activation time.

### Time and Time Synchronization (§21)

| Service                      |   Py-C   | Py-S |  Rust-C  | Rust-S  |
|------------------------------|:--------:|:----:|:--------:|:-------:|
| UTC time read (Timestamp DA) |   yes    | yes  |   yes    |   yes   |
| Time-quality flags on update |   n/a    | yes  |   n/a    |   yes   |
| SNTP / NTP responder         |    —     |  —   |   n/a    | yes (9) |
| SNTP / NTP client            | yes (10) | n/a  | yes (10) |   n/a   |

9. SNTPv4 unicast server in `iec61850-sntp` (mode 3 → mode 4 reply).
   Not yet exposed through this Python package; the package targets the
   client side for time sync.
10. SNTPv4 unicast client. From Python use `iec61850.query_sntp(addr,
    timeout_s)`; from Rust use `iec61850_sntp::SntpClient`. Single-sample
    offset / round-trip via RFC 4330 §5 four-timestamp formula.

### File Transfer (§23)

| Service                        |  Py-C  |  Py-S  | Rust-C | Rust-S |
|--------------------------------|:------:|:------:|:------:|:------:|
| GetFile / SetFile / DeleteFile | — (11) | — (11) | — (11) | — (11) |
| GetFileAttributeValues         | — (11) | — (11) | — (11) | — (11) |
| GetServerDirectory(FILE)       | — (11) | — (11) | — (11) | — (11) |

11. File services are deferred until the upstream Rust workspace ships PDU
    encode / decode, a client API, and server-side dispatch. The Python
    package will pick them up once that Rust client surface lands.

### Tooling (out-of-band)

| Capability                                              | Python | Rust |
|---------------------------------------------------------|:------:|:----:|
| SCL / ICD / CID parser                                  |  yes   | yes  |
| Two-stage SCL pipeline (XML + cross-element resolution) |  yes   | yes  |
| Typed-spec introspection (`TypeSpec`)                   |  yes   | yes  |
| Code-driven `IedModel` construction                     |  yes   | yes  |
| Async SNTP / NTP client                                 |  yes   | yes  |

## Quick start

```python
import asyncio
import iec61850

async def main():
    conn = await iec61850.IedConnection.connect("127.0.0.1:102", timeout_ms=5000)
    try:
        status = await conn.read_int32("DemoIEDLD0/LLN0.Mod.stVal", iec61850.FC.ST)
        vendor = await conn.read_string("DemoIEDLD0/LLN0.NamPlt.vendor", iec61850.FC.DC)
        quality = await conn.read_quality("DemoIEDLD0/GGIO1.Ind1.q", iec61850.FC.ST)
        print(status, vendor, quality.validity)
    finally:
        await conn.disconnect()

asyncio.run(main())
```

## TLS

```python
ca_pem = open("ca.pem", "rb").read()
tls = iec61850.TlsConfig(ca_pem=ca_pem)

conn = await iec61850.IedConnection.connect_tls(
    "ied.example.com:3782",
    tls,
    server_name="ied.example.com",
    timeout_ms=5000,
)
```

Mutual TLS adds a client cert and key:

```python
tls = iec61850.TlsConfig(
    ca_pem=open("ca.pem", "rb").read(),
    client_cert_pem=open("client.crt", "rb").read(),
    client_key_pem=open("client.key", "rb").read(),
)
```

Defaults: TLS 1.2-1.3, IEC 62351-3 cipher whitelist, chain and time
validation on, session resumption on. Set `verify_hostname=False` on
`TlsConfig` to skip SNI / SAN hostname matching for closed-network
commissioning (other validation still applies).

### Pinned peers, CRL, version pinning

```python
tls = iec61850.TlsConfig(
    ca_pem=open("ca.pem", "rb").read(),
    # Restrict accepted server certificates to a fixed allow-list
    # (IEC 62351-3 known-peer profile).
    allow_only_known_peers=True,
    known_peer_pems=(open("ied1.crt", "rb").read(),),
    # Pin a single TLS version.
    min_version=iec61850.TlsVersion.TLS_1_3,
    max_version=iec61850.TlsVersion.TLS_1_3,
    # Revocation checks.
    crl_pems=(open("ca.crl.pem", "rb").read(),),
)
```

## High-level client

`Iec61850Client` wraps `IedConnection` as an async context manager and
optionally runs a background report dispatcher:

```python
cfg = iec61850.Iec61850ClientConfig(
    address="ied.example.com",
    port=102,
    timeout_ms=5000,
    # Tuning that flows down into the underlying MMS client.
    request_timeout_ms=3000,
    max_outstanding=4,
    local_max_pdu_size=16384,
    # Background dispatcher; None to disable.
    report_dispatcher_interval_ms=100,
)

async with iec61850.Iec61850Client(cfg) as cli:
    val = await cli.connection.read_float(
        "DemoIEDLD0/MMXU1.TotW.mag.f", iec61850.FC.MX
    )
```

For TLS, pass a `TlsConfig` on the config and (optionally) override the SNI:

```python
cfg = iec61850.Iec61850ClientConfig(
    address="10.0.0.1",            # network address
    port=3782,
    tls=iec61850.TlsConfig(ca_pem=ca_pem),
    tls_server_name="ied.example.com",  # SNI; defaults to `address`
)
```

The same `request_timeout_ms` / `max_outstanding` / `local_max_pdu_size`
keyword arguments are also accepted on `IedConnection.connect` and
`IedConnection.connect_tls` for callers that prefer to manage the connection
lifecycle directly.

## Generic read / write

```python
# Native Python types: scalars surface as bool / int / float / str;
# bytes-like kinds as bytes; arrays and structures as list.
value = await conn.read("DemoIEDLD0/MMXU1.TotW.mag.f", iec61850.FC.MX)

# A remote Write is accepted for FC=SP, SV, and SE by default; the
# server-side write access policy decides the rest.
await conn.write(
    "DemoIEDLD0/GGIO1.SetPt1.setVal", iec61850.FC.SP, 42
)
```

Array elements and sub-components are addressed with keyword arguments:

```python
# Reads the third element of an array DA.
elem = await conn.read("LD/LN.Arr", iec61850.FC.ST, array_index=2)

# Reads `stVal` inside the third element.
sub = await conn.read(
    "LD/LN.Arr", iec61850.FC.ST, array_index=2, component="stVal"
)
```

## Schema introspection

```python
# Per-variable MMS TypeSpecification, returned as a nested dict.
ts = await conn.get_variable_specification(
    "DemoIEDLD0/LLN0.Mod", iec61850.FC.ST
)
# ts == {"kind": "structure", "components": [
#   {"name": "stVal", "type": {"kind": "integer", "width_bits": 32}},
#   {"name": "q",     "type": {"kind": "bit_string", "bits": 13}},
#   ...
# ]}

# Whole device-model index — list of logical devices with their MMS
# NamedVariable names. First call fetches; subsequent calls hit a cache.
model = await conn.get_device_model()
for ld in model["logical_devices"]:
    print(ld["name"], len(ld["variables"]))

# Force a re-fetch if the server model may have changed.
fresh = await conn.get_device_model(refresh=True)
```

Every type-spec node carries a `"kind"` discriminator. Scalar kinds add
payload fields appropriate for the type (`width_bits`, `format_width` /
`exponent_width`, `max_chars`, `bits`, ...). `"array"` adds `element_count`
plus a recursive `element_type`. `"structure"` adds `components` — a list of
`{"name", "type"}` entries. `"unknown"` surfaces the raw ASN.1 tag for
forward compatibility.

## Datasets

```python
await conn.create_data_set(
    "DemoIEDLD0/LLN0.ds1",
    [
        iec61850.DataSetMember("DemoIEDLD0/MMXU1.TotW.mag.f", iec61850.FC.MX),
        iec61850.DataSetMember("DemoIEDLD0/GGIO1.Ind1.stVal", iec61850.FC.ST),
    ],
)

values = await conn.get_data_set_values("DemoIEDLD0/LLN0.ds1")
# values is a list ordered to match the data set members.

await conn.set_data_set_values("DemoIEDLD0/LLN0.ds1", [3.14, True])

deleted = await conn.delete_data_set("DemoIEDLD0/LLN0.ds1")
```

`DataSetMember` accepts optional `array_index` / `component` to target an
array element or a sub-component (`component` requires `array_index`); the
facade composes the alternate-access reference for you.

`get_data_set_values` and `set_data_set_values` raise `IedDataAccessError`
when any single entry's access or write fails on the server, with the entry
index in the error message.

### What a data set holds

```python
directory = await conn.get_data_set_directory("DemoIEDLD0/LLN0.dsStatus")

directory.deletable          # False for an SCL-declared set, True for a
                             # set built with create_data_set()
for member in directory.members:
    print(member.fc.value, member.object_ref)
```

Members come back in the order the server holds them, which is the order
`get_data_set_values` returns its entries in, and in the reference form
`create_data_set` accepts — so a directory read clones a data set:

```python
await conn.create_data_set("DemoIEDLD0/LLN0.ds_clone", directory.members)
```

Listing the data set *names* of a logical node is a different service:
`get_logical_node_directory(ln_ref, iec61850.AcsiClass.DATASET)`.

### Reading several objects in one round trip

```python
values = await conn.read_multiple(
    [
        ("DemoIEDLD0/GGIO1.Ind1.stVal", iec61850.FC.ST),
        ("DemoIEDLD0/MMXU1.TotW.mag.f", iec61850.FC.MX),
    ],
)
```

The result follows the request order and has the same length. No named
variable list has to exist on the server, so any set of attributes can be
polled with a single MMS Read.

### Per-entry failures

By default one failing entry raises `IedDataAccessError`. Pass
`strict=False` to `read_multiple` or `get_data_set_values` to keep the
successful values and receive a `DataAccessFailure` in the failing position:

```python
values = await conn.read_multiple(targets, strict=False)
for target, value in zip(targets, values, strict=True):
    if isinstance(value, iec61850.DataAccessFailure):
        print(f"{target[0]} unavailable: {value.error}")
```

`DataAccessFailure.error` is the symbolic MMS `DataAccessError` name (for
example `"ObjectNonExistent"`) and `.code` the value carried on the wire.

## Connection control

```python
# Normal close — MMS Conclude exchange, then TCP shutdown.
await conn.disconnect()

# Rude close — drop the TCP socket without negotiation. Use when the peer
# stops responding or a normal disconnect would block.
await conn.abort()
```

## Log service

```python
LOG_REF = "IED1LD0/LLN0$LG$evlog"

# First page — by time range. ``more_follows`` signals that the server
# truncated the response and the caller should resume.
entries, more = await conn.query_journal_by_time(LOG_REF, start_ms, end_ms)
for e in entries:
    print(e.time_ms, e.entry_id.hex(), len(e.variables))

# Resume from the last seen entry. Both arguments — the entry's ``time_ms``
# and 8-byte ``entry_id`` — are applied as filters server-side.
cursor = entries[-1]
more_entries, _ = await conn.query_journal_after_entry(
    LOG_REF, cursor.time_ms, cursor.entry_id
)
```

`JournalEntry.variables` is a tuple of `JournalEntryVariable(data_ref,
value, reason_code)`. Values follow the same conversion rules as
`IedConnection.read` — scalars surface natively; bytes-like kinds
(`BIT_STRING` / `OCTET_STRING` / `UTC_TIME` / `BINARY_TIME`) as `bytes`;
composites as `list`.

## Reporting

```python
def on_report(report: iec61850.ClientReport) -> None:
    print(report.rcb_reference, len(report.entries))

rcb = await conn.get_rcb_values("DemoIEDLD0/LLN0$RP$urcbMeas")
rcb.resv = True
rcb.rpt_ena = True
await conn.set_rcb_values(rcb, iec61850.RcbWriteMask.fields("resv", "rpt_ena"))
await conn.install_report_handler(rcb.object_reference, on_report)

dispatcher = conn.spawn_report_dispatcher(interval_ms=100)
try:
    await asyncio.sleep(10)
finally:
    await dispatcher.aclose()
```

## Control

```python
spc = conn.create_control_object(
    "IED1LD0/GGIO1.SPCSO1",
    iec61850.ControlModel.SBO_ENHANCED,
)
spc.set_origin(iec61850.OriginValue(or_cat=3, or_ident=b"py-client"))

if (await spc.select_with_value(True)).success:
    outcome = await spc.operate(True)
    if not outcome.success:
        print("operate failed:", outcome.add_cause)
```

## SCL / ICD / CID parser

Load an IED configuration document and inspect it as plain Python data:

```python
scl = iec61850.load_scl("MyDevice.icd")

scl.ieds()                          # ['IED1']

doc = scl.to_dict()
doc["ieds"][0]["name"]              # 'IED1'
doc["ieds"][0]["manufacturer"]      # 'ACME'

ld = doc["ieds"][0]["access_points"][0]["server"]["logical_devices"][0]
ld["inst"]                          # 'GenericIO'
[ln["ln_class"] for ln in ld["logical_nodes"]]   # ['LLN0', 'GGIO', ...]

# Resolve a logical-node's data objects via the DataTypeTemplates section:
ln_type_id = ld["logical_nodes"][0]["ln_type"]
ln_type = doc["data_type_templates"]["ln_node_types"][ln_type_id]
[do["name"] for do in ln_type["dos"]]            # ['Mod', 'Beh', ...]
```

Both `load_scl` and `parse_scl` run the full two-stage pipeline (XML syntax
→ cross-element type-reference resolution). The returned dict mirrors the
SCL XML structure; type references stay as strings so callers can index
into `doc["data_type_templates"]` (`ln_node_types` / `do_types` /
`da_types` / `enum_types`) themselves.

For a stable text representation — useful as a regression / diff oracle —
ask for the canonical summary of a single IED:

```python
print(scl.summary("IED1"))
# IED name=IED1
#   lds count=1
#     LD inst=GenericIO ld_name=<None> lns=2
#       LN class=LLN0 inst= prefix= dos=2 ...
#       ...
```

Parse failures surface as `SclError`, a subclass of `IedError`, with
`line`, `column`, `element_path`, `attribute`, `kind`, and `message`
attributes set so the offending location is directly reachable:

```python
try:
    iec61850.load_scl("broken.icd")
except iec61850.SclError as e:
    print(e.kind, "at", e.line, ":", e.column, "→", e.element_path, "@", e.attribute)
    # e.g. UnresolvedTypeReference at 42 : 7 → SCL/IED[name="IED1"]/.../LN[...] @ lnType
```

## Server hosting

Host an IED defined by an SCL / ICD / CID document as an MMS server.
`IedServer.from_scl()` builds the runtime model, `bind()` selects the
TCP address (port `0` requests an OS-assigned port), and `async with`
manages the lifecycle. While running, push value updates with the typed
`update_*` methods, addressing data attributes by
`"<LD>/<LN>.<DO>.<DA>[.<sub>]*"`.

```python
import asyncio
import iec61850

async def main():
    server = iec61850.IedServer.from_scl("plant.icd", ied_name="IED1")
    server.bind("0.0.0.0:0")
    server.vendor = "ACME"
    server.model_name = "Generic-IO"
    server.max_connections = 5

    async with server:
        print("listening on", server.bound_addr)
        while True:
            server.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
            server.update_float32("GenericIO/MMXU1.TotW.mag.f", measure_power())
            await asyncio.sleep(0.1)

asyncio.run(main())
```

Configuration setters (`vendor`, `model_name`, `revision`, `max_connections`)
must be called before `start()`. Updating an unknown path raises `KeyError`;
a type mismatch (e.g. pushing `update_float32` to a `BOOLEAN` attribute)
raises `IedDataAccessError`. Bind failures and other lifecycle errors
surface as `IedServerError`.

The supported typed updates are `update_bool`, `update_int32`,
`update_int64`, `update_uint32`, `update_float32`, `update_float64`, and
`update_string`.

### Server-side TLS

`with_tls()` wraps the listener in an IEC 62351-3 TLS acceptor. Only
valid before `start()`; calling it more than once raises `RuntimeError`.

```python
server.with_tls(
    server_cert_pem=open("server.crt", "rb").read(),
    server_key_pem=open("server.key", "rb").read(),
)
```

Mutual TLS adds a CA bundle for client-chain validation and, optionally,
a pinned peer list (only certificates whose SPKI matches one of the
known peers are accepted):

```python
server.with_tls(
    server_cert_pem=server_cert,
    server_key_pem=server_key,
    client_ca_pem=ca_pem,
    allow_only_known_peers=True,
    known_peer_pems=[peer1_pem, peer2_pem],
    crl_pems=[crl_pem],
)
```

Defaults: TLS 1.2 - 1.3, IEC 62351-3 cipher whitelist, chain validation
on, time validation on, session resumption on, no client-cert pinning.
`min_tls_version` and `max_tls_version` accept `"tls1.2"` / `"tls1.3"`.

### Read / write callbacks

Register per-attribute callbacks to override cached reads or intercept
incoming writes. Both `on_read` and `on_write` may be called before
`start()` (queued and installed at startup) or while the server is running
(installed immediately). Re-registering the same path replaces the
previous callback.

```python
def measure_indication(path: str) -> bool:
    # Sampled from physical I/O on every client read.
    return read_io(path)

def validate_setpoint(path: str, value: int) -> bool:
    if value < 0 or value > 100:
        err = iec61850.IedDataAccessError("setpoint out of range")
        err.code = "ObjectValueInvalid"
        raise err
    apply_setpoint(value)
    return True   # also store value in the server-side cache

server.on_read("GenericIO/GGIO1.Ind1.stVal", measure_indication)
server.on_write("GenericIO/GGIO1.SetPt1.setVal", validate_setpoint)
```

`on_read` return values:

| Return     | Behavior                            |
|------------|--------------------------------------|
| any scalar | the value is returned to the client  |
| `None`     | fall through to the cached value     |
| raises     | read fails with `IedDataAccessError` |

`on_write` return values:

| Return           | Behavior                                        |
|------------------|--------------------------------------------------|
| `True`           | accept; cache is updated with the incoming value |
| `False` / `None` | accept; cache is **not** updated (you manage it) |
| raises           | reject; client sees `IedDataAccessError`         |

Set a `code` attribute on the raised exception to control the reported
`DataAccessError` variant — `"HardwareFault"`, `"TemporarilyUnavailable"`,
`"ObjectAccessDenied"`, `"ObjectValueInvalid"`, etc. Without `code` the
server reports `ObjectAccessDenied`.

### Control callbacks

`on_control` binds the server-side execution of a control object. Address
it at the DO (`"<LD>/<LN>.<DO>"`) and declare which IEC 61850 control
model the DO uses. Up to three callbacks may be supplied:

- `check` — sync. Static validation before the operate phase fires
  (interlocks, mode, permissions). Raise to reject; the return value is
  ignored.
- `operate` — sync or `async`. The actual command execution. Raise on
  failure; the return value is ignored.
- `wait` — sync or `async`. Dynamic check during the operate phase for
  `sbo-enhanced` controls (e.g. wait for synchro-check confirmation).

Each callback receives `(path, ctl_val, action)`. `action` is a dict with
`ctl_num`, `test`, `synchro_check`, `interlock_check`, `is_select`,
`ctl_time_ms`, and `origin` (a sub-dict with `or_cat` and `or_ident`).

```python
async def operate(path: str, value: bool, action: dict) -> None:
    if action["test"]:
        return                                  # test command — no I/O
    await drive_breaker(path, value)

def check(_path: str, _value: bool, _action: dict) -> None:
    if interlock_blocked():
        err = iec61850.IedControlError("interlocked")
        err.add_cause = "BlockedByInterlocking"
        raise err

server.on_control(
    "GenericIO/GGIO1.SPCSO1",
    ctl_model="direct-normal",
    check=check,
    operate=operate,
)
```

`ctl_model` is one of `"status-only"`, `"direct-normal"`, `"sbo-normal"`,
`"direct-enhanced"`, `"sbo-enhanced"`. For SBO models the optional
`sbo_timeout_ms` (default 30000) and `sbo_class` (`"operate-once"` or
`"operate-many"`, default `"operate-once"`) configure the select-phase
behavior.

Raise an exception from any callback to reject the command. Set
`add_cause` on the exception to the variant name (e.g.
`"BlockedByInterlocking"`, `"BlockedByProcess"`, `"NotSupported"`) or
its numeric MMS code; absence falls back to `"Unknown"`.

### Datasets and unbuffered reporting

Declare a server-side dataset and bind it to an Unbuffered Report Control
Block (URCB) before `start()`. The same client APIs (`get_rcb_values`,
`set_rcb_values`, `install_report_handler`) consume reports from the URCB
once the server is running.

```python
server.add_dataset(
    "GGIO1$ds1",
    [
        "GenericIO/GGIO1.Ind1.stVal",
        "GenericIO/GGIO1.AnIn1.mag.f",
    ],
)

server.register_urcb(
    "GenericIO/LLN0.urcb01",
    dataset="GGIO1$ds1",
    trg_ops=["data_changed", "gi"],
    opt_flds=["seq_num", "time_stamp", "reason", "data_set"],
    buf_tm_ms=50,
)
```

Dataset names follow the IEC 61850 convention `"<LN>$<dsName>"`. Every
entry in a dataset must belong to the same logical device. `register_urcb`
accepts `rpt_id` (defaults to `"<domain>/<LN>$RP$<rcb_name>"`),
`conf_rev`, `trg_ops`, `opt_flds`, `buf_tm_ms`, and `intg_pd_ms`.

Trigger options: `"data_changed"`, `"quality_changed"`, `"data_update"`,
`"integrity"`, `"gi"`, plus the aliases `"all"` and `"none"`.

Optional fields: `"seq_num"`, `"time_stamp"`, `"reason"`, `"data_set"`,
`"data_reference"`, `"conf_rev"`, `"buffer_overflow"`, `"entry_id"`. Per
IEC 61850-7-2 §15, `buffer_overflow` and `entry_id` are masked out on the
wire for unbuffered reports.

Datasets without a URCB are still reachable via `get_data_set_values`.

### Buffered reporting (BRCB)

`register_brcb` mirrors `register_urcb` but binds the dataset to a
Buffered Report Control Block (`$BR$` MMS path). Reports are held in a
per-RCB ring buffer until a client connects, so transient disconnects
do not lose updates.

```python
server.register_brcb(
    "GenericIO/LLN0.brcb01",
    dataset="GGIO1$ds1",
    trg_ops=["data_changed", "gi"],
    opt_flds=[
        "seq_num", "time_stamp", "reason", "data_set",
        "buffer_overflow", "entry_id",
    ],
    buf_tm_ms=50,
    buffer_capacity=128,
)
```

Per IEC 61850-7-2 §15, `buffer_overflow` and `entry_id` are honored on
the wire for BRCBs (URCBs mask them out). Additional knobs:

- `buffer_capacity` (default `64`) — entry-count ring size; the buffer
  evicts oldest entries when full and surfaces overflow through the
  `buffer_overflow` field on the next report.
- `with_resv_tms` (default `True`) — expose the Edition 2+ `ResvTms`
  field for client reservation.
- `with_owner` (default `False`) — expose the Edition 2+ `Owner` field.

The same dataset can back both a URCB and a BRCB; client-side APIs
(`get_rcb_values`, `set_rcb_values`, `install_report_handler`,
`ReportDispatcher`) handle both transparently — `RcbHandle.is_buffered`
discriminates them.

### Logging (LCB)

`register_log_control` declares a Log Control Block (`$LG$` MMS path).
Each block backs onto an in-memory journal (`InMemoryLogStorage`), which
can be unbounded (default) or capped to a fixed entry count (oldest
entries are evicted on overflow). Clients pull the journal contents over
MMS `ReadJournal` via `query_journal_by_time` / `query_journal_after_entry`.

```python
server.register_log_control(
    "GenericIO/LLN0.lcb01",
    dataset="LLN0$evlogds",
    trg_ops=["data_changed"],
    storage_capacity=1000,
)

async with server:
    server.log_value(
        "GenericIO/LLN0.lcb01",
        data_ref="IED1GenericIO/GGIO1$ST$Ind1$stVal",
        value=True,
        reason_code=0x02,            # bit 1 = data_changed
    )
```

Triggers are explicit: `log_value` writes one entry per call rather than
auto-tracking `update_*`. `log_value` returns the 8-byte entry id (as an
`int`) on success, or `None` when the block's `LogEna` is disabled and the
trigger was silently skipped. Toggle the enable state at runtime with
`set_log_ena(path, on)`.

### Setting groups (SGCB)

A Setting Group Control Block is declared in SCL on `<LN0>`:

```xml
<LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
  <SettingControl numOfSGs="3" actSG="1" resvTms="60"/>
</LN0>
```

The server tracks ActSG / EditSG / ConfirmEditSG state, enforces the
single-client edit-session lock, and times out abandoned reservations.
Python applications opt into the three veto / commit points per LD:

```python
def on_act_sg(new_sg: int, conn_id: int) -> bool:
    return new_sg in allowed_sgs        # return False → ObjectAccessDenied

def on_confirm(edit_sg: int, conn_id: int) -> None:
    persist_pending_settings(edit_sg)   # commit FC=SE staging buffer

server.register_setting_group_handler(
    "GenericIO",
    on_act_sg=on_act_sg,
    on_confirm=on_confirm,
)
```

`get_setting_group_info(ld_inst)` returns the live snapshot
(`num_of_sg` / `act_sg` / `edit_sg` / `cnf_edit` / `last_act_tm_ms` /
`resv_tms_s`). `force_active_setting_group(ld_inst, sg)` switches the
active group without consulting the callback — intended for startup
state restoration. Calling `register_setting_group_handler` again at
runtime atomically replaces the previous handler.

Clients drive SGCB through the regular write API on the special MMS
path:

```python
await conn.write("IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, 2)    # SelectActiveSG
await conn.write("IED1GenericIO/LLN0.SGCB.EditSG", FC.SP, 2)   # open edit
await conn.write("IED1GenericIO/LLN0.SGCB.CnfEdit", FC.SP, True)  # commit
```

### Building a server from a model dict (no SCL)

When the IED schema is generated in Python — code-driven test rigs,
dynamic device skeletons, or runtimes that prefer dict-driven
configuration over XML — `from_model_spec` consumes a declarative
spec dict that maps onto the same model the SCL parser produces. The
RCB / LCB / SGCB declarations land in the model and are picked up by
`start()` exactly as SCL-derived ones would be; every callback and
`register_*` method works against the same `"<LD>/<LN>.<DO>[.<DA>]*"`
paths.

```python
spec = {
    "ied_name": "IED1",
    "lds": [{
        "inst": "GenericIO",
        "lns": [
            {
                "lln0": True,
                "dos": [{
                    "name": "Mod",
                    "das": [
                        {"name": "stVal", "fc": "ST", "type": "Enumerated",
                         "trg_ops": ["data_changed"],
                         "value": {"type": "int", "value": 1}},
                        {"name": "q", "fc": "ST", "type": "Quality"},
                        {"name": "t", "fc": "ST", "type": "Timestamp"},
                    ],
                }],
                "sgcb": {"num_of_sg": 3, "act_sg": 1},
            },
            {
                "class": "GGIO", "inst": "1",
                "dos": [{
                    "name": "Ind1",
                    "das": [
                        {"name": "stVal", "fc": "ST", "type": "Boolean"},
                        {"name": "q", "fc": "ST", "type": "Quality"},
                        {"name": "t", "fc": "ST", "type": "Timestamp"},
                    ],
                }],
                "datasets": [{
                    "name": "Events", "entries": [
                        {"ln_name": "GGIO1", "fc": "ST",
                         "do_path": ["Ind1", "stVal"]},
                    ],
                }],
                "rcbs": [{
                    "name": "Events01", "buffered": False,
                    "dataset_ref": "Events", "conf_rev": 1,
                    "trg_ops": ["data_changed", "integrity"],
                    "opt_flds": ["seq_num", "time_stamp", "reason"],
                    "buf_tm_ms": 100,
                }],
            },
        ],
    }],
}

server = iec61850.IedServer.from_model_spec(spec)
server.bind("127.0.0.1:0")
async with server:
    ...
```

`type` accepts every IEC 61850-7-3 spelling (`"Boolean"`, `"Int32"`,
`"Float32"`, `"Enumerated"`, `"Timestamp"`, `"Quality"`, …); sized
variants use the object form `{"type": "OctetString", "max_len": 64}`.
`value` is a tagged dict (`{"type": "int", "value": 1}`, `{"type":
"bit_string", "padding": 3, "data": b"\x00\x00"}`, …) or
`{"type": "default"}` for the type's zero. Constructed (SDA-bearing)
DAs go under `"constructed_das"` with a `"children"` list; nested DOs
go under `"sub_dos"`. The underlying `IedModelBuilder` enforces all
invariants (LLN0 first, SGCB only on LLN0, dataset entries resolve to
real LN/DO) and surfaces violations as `ValueError`.

### Atomic batch updates

`server.batch()` returns a synchronous context manager that holds the
server's data-model lock for the duration of the `with` block. Concurrent
batches raise `RuntimeError` rather than deadlocking, so callers can
choose to retry or fail fast.

```python
with server.batch():
    server.update_bool("GenericIO/GGIO1.Ind1.stVal", True)
    server.update_float32("GenericIO/GGIO1.AnIn1.mag.f", 12.5)
    server.update_int32("GenericIO/GGIO1.SetPt1.setVal", 7)
```

## Error handling

```python
try:
    conn = await iec61850.IedConnection.connect("10.0.0.1:102", timeout_ms=2000)
except iec61850.IedTimeoutError:
    ...   # connection timed out
except iec61850.IedConnectionError:
    ...   # TCP / OSI stack failure
except iec61850.IedError:
    ...   # catch-all base for any IEC 61850 error
```

## Release notes

### 0.14.0

- `get_data_set_directory` — the ACSI GetDataSetDirectory service, returning
  a `DataSetDirectory` with each data set member as an IEC reference and its
  functional constraint.
- `read_multiple` — reads several object references with their functional
  constraints in a single MMS Read, in request order.
- `get_data_set_values` and `read_multiple` accept `strict=False`, returning
  a `DataAccessFailure` for a failing entry in place of raising
  `IedDataAccessError`.
- Corrected the documented `iec61850.AcsiClass` member for listing a logical
  node's data set names: `AcsiClass.DATASET`.
- The `ServiceError` `errorClass` subcodes now follow the ISO 9506-2
  `ServiceError` ASN.1 named numbers, and a Confirmed-ErrorPDU decodes per its
  `[0] IMPLICIT` invokeID tag. A server refusal now surfaces through
  `IedServiceError` carrying the standard `ErrorClass` rather than a generic
  parse failure.

## Who is using this?

The project is free to use in any setting, commercial deployments included, and
no registration is required. If you do run it somewhere, saying so helps other
readers judge where the implementation has been exercised. Add an entry to
[`ADOPTERS.md`](https://github.com/csp0924/iec61850-rust/blob/main/ADOPTERS.md)
by opening an issue with the
[adopter template](https://github.com/csp0924/iec61850-rust/blob/main/.github/ISSUE_TEMPLATE/adopter.md),
or open a GitHub Discussion if you would rather ask something first. Naming an
organization is optional; a description of the use case on its own is welcome.

## License

`MIT OR Apache-2.0`, at your option. See
[LICENSE-MIT](https://github.com/csp0924/iec61850-python/blob/main/LICENSE-MIT)
and
[LICENSE-APACHE](https://github.com/csp0924/iec61850-python/blob/main/LICENSE-APACHE).
The upstream [`iec61850-rust`](https://github.com/csp0924/iec61850-rust)
workspace is licensed under the same terms.
