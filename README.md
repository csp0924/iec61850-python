# iec61850

Async-first, type-hinted IEC 61850 MMS client and server for Python.

## Scope and protocol boundaries

This package is an **MMS-only facade** over
[`iec61850-rust`](https://github.com/csp0924/iec61850-rust) — an independent
Rust implementation of IEC 61850 by the same author, itself open source under
the same `MIT OR Apache-2.0` terms and published on crates.io as the
`iec61850-*` crates. This facade covers
the connection-oriented, application-level services that fit Python's runtime
characteristics naturally:

- **Client**: read / write / control / dataset / reporting (URCB + BRCB) /
  log query / GOOSE control block administration / SCL parsing / directory
  queries / server identity and status / TLS
- **Server hosting**: MMS server with URCB / BRCB / LCB / GoCB / SGCB /
  control handlers, declared from SCL **or** from a Python `dict` spec

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
  known-peer pinning, CRL, hostname and time validation knobs; the
  certificate chain is always validated, and a failed handshake raises
  `IedTlsError` naming its kind)
- Per-connection tuning: request timeout, max outstanding invocations, local
  max PDU size
- High-level `Iec61850Client` async context manager wrapping the lifecycle of
  an `IedConnection` plus an optional background `ReportDispatcher`
- Typed scalar read / write: `bool`, `int32`, `int64`, `uint32`, `float`,
  `float64`, `string`, `timestamp` (decoded to `datetime`), `quality`
  (decoded to a `Quality` dataclass), and `bit_string` as `(data, padding)`
- Generic `read` / `write` with array-element and sub-component selection;
  `write` also takes a tagged value dict for types no Python type selects
- Directory queries: `get_server_directory`, `get_logical_device_directory`,
  `get_logical_node_directory(AcsiClass)`, `get_data_directory`; a walk
  stops past `max_name_list_names` names
- Server identity and state: `get_server_identity` (MMS Identify) and
  `get_server_status` (MMS Status)
- Schema introspection: `get_variable_specification(ref, fc)` (recursive MMS
  type tree) and `get_device_model()` (per-LD named-variable index)
- Dataset admin: `create_data_set`, `delete_data_set`,
  `get_data_set_values`, `set_data_set_values`
- Connection control: `disconnect` (graceful) and `abort` (rude close —
  drop TCP without sending MMS Conclude)
- URCB / BRCB reporting: `get_rcb_values`, `set_rcb_values`,
  `install_report_handler`, `poll_reports`, background `ReportDispatcher`;
  each report says which members it carries (`included`) and which segment
  of a segmented report it is
- Log service: `query_journal(log_ref, JournalQuery)` for one ReadJournal
  request with any selection, `query_journal_all` to read every page, and
  the `query_journal_by_time` / `query_journal_after_entry` shorthands; a
  journal is named by its LogRef
- GOOSE control block administration over MMS: `get_gocb_values` and
  `set_gocb_values` (GOOSE frames themselves stay out of scope)
- SCL / ICD / CID document parser: `load_scl(path)` / `parse_scl(xml)` →
  `Scl` handle exposing IED inventory, the full document as a nested dict,
  and a canonical text summary per IED
- Server hosting: `IedServer.from_scl(path, ied_name=...)` instantiates an
  MMS server from an SCL document, or `IedServer.from_model_spec(spec)` from
  a declarative Python dict when the IED is generated in code rather than
  authored as XML. Bind, configure (vendor / model name /
  max connections / association timeouts / run-time data set limits), enter
  via `async with`, push value updates with typed
  `update_*` methods and whole arrays with `update_array`, intercept reads /
  writes with `on_read` / `on_write` callbacks (optionally with the request
  context), serve control commands (SBO / direct, normal / enhanced)
  with `on_control` — `operate` and `wait` callbacks may be either sync
  or `async` — register every data set, report, GOOSE and log control block
  and control object the model declares with `register_from_model()`, or
  expose URCB / BRCB reporting via `add_dataset` / `register_urcb` /
  `register_brcb` and Log Control Blocks via `register_log_control`
  (in-memory journal recorded from the data set members, `ReadJournal` over
  MMS, `log_value` for further entries), wire Setting Group callbacks per LD
  with `register_setting_group_handler` + `force_active_setting_group` and
  read or replace the values of each group; choose the edition
  (`set_edition`) and the functional constraints clients may write
  (`set_write_access_policy`); group updates with `with server.batch():` so
  they report and log as one batch; terminate TLS at the server with
  `with_tls()` (same IEC 62351-3 profile as the client, plus client-cert
  pinning)
- Control: `select`, `select_with_value`, `operate`, `cancel` across the four
  IEC 61850 control models (`direct-normal` / `direct-enhanced` /
  `sbo-normal` / `sbo-enhanced`), each returning a `ControlOutcome` that
  carries the add cause and the `LastApplError` the server reported
- Typed exception hierarchy: `IedError`, `IedConnectionError`,
  `IedSessionRefusedError`, `IedTlsError`, `IedTimeoutError`,
  `IedDataAccessError`, `IedServiceError`, `IedControlError`,
  `IedServerError`; each carries attributes naming the cause
- SNTP / NTP client: `query_sntp(addr, timeout_s)` returns an `SntpResponse`
  with server time, clock offset, and round-trip estimate computed per
  RFC 4330 §5; `addr` is an IP address and port such as `"192.0.2.10:123"`
  or `"[2001:db8::1]:123"`
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
implementation repository at the release tag this version tracks:

```toml
iec61850-client = { git = "https://github.com/csp0924/iec61850-rust", tag = "v0.4.0", features = ["tls"] }
```

With that tag published, a build is just:

```bash
uv sync                       # dev environment, including maturin and pytest
uv run maturin develop --release
uv run pytest -q
```

### Building against a local checkout

To build against an unreleased `iec61850-rust` — a tag not yet published, or
changes made to both repositories together — clone it next to this one so the two
share a parent directory:

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
patching before applying the patch. So while the tag is not published, that
config alone is not enough — resolution fails with:

```
failed to load source for dependency `iec61850-client`
  Unable to update https://github.com/csp0924/iec61850-rust?tag=v0.4.0
```

Seed a lock file once and the patch takes over. Cargo's git backend needs an
absolute URL here, so derive one rather than hard-coding it. From the
repository root, with the sibling checkout in place:

```bash
# Absolute path to the sibling checkout. `pwd -W` is the Git-for-Windows
# spelling that yields `D:/...`; plain `pwd` covers Linux and macOS.
RUST_REPO=$(cd ../iec61850-rust && { pwd -W 2>/dev/null || pwd; })

# 1. Point the manifest at that checkout, just long enough to resolve.
sed -i "s|https://github.com/csp0924/iec61850-rust\", tag = \"v0.4.0|file://${RUST_REPO}\", branch = \"main|g" Cargo.toml
cargo generate-lockfile

# 2. Restore the manifest and relabel the source lines the lock recorded.
#    Cargo canonicalises the URL it stores, so match on the branch suffix
#    rather than on the exact spelling passed in above.
git checkout Cargo.toml
sed -i -E "s|git\+file://[^\"]*\?branch=main|git+https://github.com/csp0924/iec61850-rust?tag=v0.4.0|g" Cargo.lock
```

`Cargo.lock` is git-ignored for the same reason: it pins a revision the
published repository does not carry yet. After this, `cargo metadata` resolves
offline and reports the patched packages coming from
`../iec61850-rust/crates/*`, and `uv run maturin develop --release` builds
against your working tree — edits on the Rust side are picked up without a
commit.

Once the tag `Cargo.toml` names is published, delete `.cargo/config.toml` and
`Cargo.lock` and build normally; none of this is needed any more.

## Conformance (PICS)

ACSI service support per IEC 61850-7-2 Edition 2; the section numbers
below follow that edition.

- **Py-C / Py-S** — exposed through *this* Python package (client / server).
- **Rust-C / Rust-S** — supported somewhere in the upstream
  [`iec61850-rust`](https://github.com/csp0924/iec61850-rust) workspace.

Legend: `yes` supported; `—` not yet implemented; `partial` a stated subset
(see the footnote); `no, by design` deliberately not in Python (use the named
Rust crate instead); `n/a` not applicable; `(n)` footnote. The Rust columns
follow the upstream conformance statement,
[`docs/PICS.md`](https://github.com/csp0924/iec61850-rust/blob/main/docs/PICS.md),
which carries the full per-service detail.

### Application Association (§8)

| Service                        |  Py-C   |  Py-S   | Rust-C | Rust-S |
|--------------------------------|:-------:|:-------:|:------:|:------:|
| Associate (Two-Party, MMS/TCP) |   yes   |   yes   |  yes   |  yes   |
| Abort (graceful Conclude)      |   yes   |   yes   |  yes   |  yes   |
| Abort (rude — TCP drop)        |   yes   |   yes   |  yes   |  yes   |
| Release                        |   yes   |   yes   |  yes   |  yes   |
| Configurable ISO addressing    |   yes   |    —    |  yes   |   —    |
| TLS 1.2 / 1.3 (IEC 62351-3)    | yes (1) | yes (1) |  yes   |  yes   |
| Mutual TLS / pinned peer / CRL |   yes   |   yes   |  yes   |  yes   |
| Authentication (ACSE password) | yes (15)| yes (15)|  yes   |  yes   |
| Association gating (AP-title)  |    —    | yes (15)|   —    |  yes   |

1. Includes 62351-3 cipher whitelist, `verify_hostname` and
   `time_validation` knobs, version pinning, and known-peer profile; the
   certificate chain is always validated. A failed client handshake raises
   `IedTlsError` with its `kind`. Backed by `iec61850-tls`.

15. The client sends `IsoConnectionParameters(password=...)` as the ACSE
    password mechanism; a refusal naming the calling identity or the
    credentials raises `IedAuthenticationError`. The server decides on each
    association through `IedServer.set_authenticator`. The IEC 62351-4
    certificate mechanism is not implemented; peer authentication by
    certificate is done by TLS.

### Server class (§7)

| Service                      | Py-C | Py-S | Rust-C | Rust-S |
|------------------------------|:----:|:----:|:------:|:------:|
| GetServerDirectory           | yes  | yes  |  yes   |  yes   |
| GetServerCapabilities (Ed.2) |  —   |  —   |   —    |   —    |
| MMS Identify                 | yes  | yes  |  yes   |  yes   |
| MMS Status                   | yes  | yes  |  yes   |  yes   |

The hosted server answers Identify with the `vendor`, `model_name` and
`revision` it is configured with.

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
| Array element access (read / write) | yes  |   yes   |  yes   |  yes   |

2. Server applies the write access policy (default `SP | SV | SE`;
   `set_write_access_policy` also opens `DC`, `CF` and `BL`, before
   `start()` or while running) and any registered `on_read` / `on_write` callbacks, which
   are asked for reads and writes above and below their own path and can
   receive the request context. `update_array` updates a whole array
   attribute as one batch.

`GetDataValues` covers both the single read (`read`) and the batched form
(`read_multiple`), which carries any set of references in one MMS Read.

### Data Set class (§13)

| Service                       | Py-C |  Py-S   | Rust-C | Rust-S |
|-------------------------------|:----:|:-------:|:------:|:------:|
| GetDataSetValues              | yes  |   yes   |  yes   |  yes   |
| SetDataSetValues              | yes  |   yes   |  yes   |  yes   |
| CreateDataSet (dynamic)       | yes  |   yes   |  yes   |  yes   |
| DeleteDataSet                 | yes  |   yes   |  yes   |  yes   |
| GetDataSetDirectory (members) | yes  |   yes   |  yes   |  yes   |
| List data set names (GetLogicalNodeDirectory DS) | yes | yes | yes | yes |
| Static (SCL-defined) datasets | yes  | yes (3) |  yes   |  yes   |

3. Server registers static datasets through `add_dataset()` or, for every
   data set the model declares, `register_from_model()`; either bound to a
   control block or exposed standalone for `GetDataSetValues`.

### Substitution (§5.3.3)

| Service                              |    Py-C     |    Py-S     |   Rust-C    |   Rust-S    |
|--------------------------------------|:-----------:|:-----------:|:-----------:|:-----------:|
| Set substituted value (FC=SV writes) | partial (12) | partial (12) | partial (12) | partial (12) |
| Dedicated Substitution service API   |      —      |      —      |      —      |      —      |

12. The `SV` functional constraint is writable by the default write access
    policy, and `MV`, `CMV` and `SAV` carry the substitution attributes, but
    a read does not itself switch to the substituted value.

### Setting Group Control Block — SGCB (§16)

| Service                               |    Py-C     |  Py-S   |   Rust-C    | Rust-S |
|---------------------------------------|:-----------:|:-------:|:-----------:|:------:|
| SelectActiveSG / SelectEditSG         | partial (6) | yes (6) | partial (6) |  yes   |
| GetSGCBValues / SetSGCBValues         | partial (6) | yes (6) | partial (6) |  yes   |
| ConfirmEditSGValues                   | partial (6) | yes (6) | partial (6) |  yes   |
| Setting access (FC=SG / FC=SE writes) |     yes     |   yes   |     yes     |  yes   |
| Values of every setting group         |     n/a     |   yes   |     n/a     |  yes   |

6. Server-side SGCB is declared via SCL `<SettingControl numOfSGs="N"/>`
   on LN0; runtime ActSG / EditSG / ConfirmEditSG state machine plus
   reservation timeout are owned by the server, which also holds the
   values of every group: SelectActiveSG loads the group into the FC=SG
   attributes and ConfirmEditSGValues stores the FC=SE buffer into it.
   Python applications install per-LD callbacks (`on_act_sg` /
   `on_edit_sg` / `on_confirm`) through
   `register_setting_group_handler()`, can `force_active_setting_group()`
   on startup, and read or replace the value a group holds with
   `get_setting_group_value()` / `set_setting_group_value()`. Clients,
   in Python and in Rust alike, drive SGCB through the generic write API
   on the special MMS path (e.g.
   ``write("IED1LD0/LLN0.SGCB.ActSG", FC.SP, {"type": "uint", "value": 2})``);
   neither has a dedicated setting-group API.

### Reporting — URCB / BRCB (§17.2)

| Service                                        | Py-C |  Py-S   | Rust-C | Rust-S |
|------------------------------------------------|:----:|:-------:|:------:|:------:|
| URCB — GetURCBValues / SetURCBValues           | yes  |   yes   |  yes   |  yes   |
| URCB — Report (TrgOps, OptFlds, BufTm, IntgPd) | yes  |   yes   |  yes   |  yes   |
| URCB — General Interrogation                   | yes  |   yes   |  yes   |  yes   |
| BRCB — GetBRCBValues / SetBRCBValues / Report  | yes  | yes (4) |  yes   |  yes   |
| Segmentation (`SubSeqNum` / `MoreFollows`)     | yes  |   yes   |  yes   |  yes   |
| Per-member inclusion of a report               | yes  |   yes   |  yes   |  yes   |
| Instances from the model                       | n/a  |   yes   |  n/a   |  yes   |
| Background report dispatcher                   | yes  |   n/a   |  yes   |  n/a   |

4. Server BRCB hosting via `register_brcb()` or `register_from_model()`
   with in-memory ring buffer (entry-count semantics), EntryID resync, and
   the `update_*` trigger path. The first report after each enable carries
   `BufOvfl` set.

### Logging — LCB / Log (§17.3)

| Service                                | Py-C |  Py-S   | Rust-C | Rust-S |
|----------------------------------------|:----:|:-------:|:------:|:------:|
| Log (recording on trigger)             | n/a  | yes (5) |  n/a   |  yes   |
| ReadJournal (every optional field)     | yes  | yes (5) |  yes   |  yes   |
| ReadJournal pagination (read to end)   | yes  |   yes   |  yes   |  yes   |
| LCB — GetLCBValues                     |  —   |   yes   |   —    |  yes   |
| LCB — SetLCBValues                     |  —   |    —    |   —    |   —    |
| GetLogStatusValues                     |  —   |   yes   |   —    |  yes   |
| QueryLogByTime / QueryLogAfter         | yes  | yes (5) |  yes   |  yes   |
| Log purging                            |  —   |    —    |   —    |   —    |

5. Server LCB hosting via `register_log_control()` or
   `register_from_model()` with an in-memory journal backend (optionally
   capacity-bounded, evicts oldest on overflow). The server writes an
   entry whenever a member of the block's data set changes value or
   quality or is updated, as its `TrgOps` enable, and one entry holding
   the whole data set every `IntgPd`; general interrogation writes none.
   `log_value` adds entries of the application's own. `LogEna` toggles at
   runtime via `set_log_ena`. A journal is named by the block's `LogRef`.

### Generic Substation Event — GOOSE / GSE (§18)

| Service                 |       Py-C        |       Py-S        |    Rust-C    |    Rust-S    |
|-------------------------|:-----------------:|:-----------------:|:------------:|:------------:|
| GoCB — Get / Set values |   partial (14)    |   partial (14)    | partial (14) | partial (14) |
| GOOSE publish           | no, by design (7) | no, by design (7) |     n/a      |     yes      |
| GOOSE subscribe         | no, by design (7) | no, by design (7) |     yes      |     n/a      |

7. **By design, not on the Python roadmap.** GOOSE is a hard-real-time L2
   protocol (IEC 61850-8-1; T1 retransmission floor = 4 ms; protection
   schemes require deterministic dispatch). Python's GIL + non-deterministic
   GC make it unsuitable for the GOOSE hot path. Use
   [`iec61850-goose`](https://github.com/csp0924/iec61850-rust/tree/main/crates/iec61850-goose)
   directly — it provides `GoosePublisher` (frame template + retransmission
   state machine), `GooseSubscriber` (smpCnt continuity + Q4), and a
   typestate `GooseReceiver`.
14. MMS administration of a GOOSE control block, which involves no
    Ethernet frame. The client reads the nine components with
    `get_gocb_values` and writes `GoEna`, `GoID` and `DatSet` with
    `set_gocb_values`, one attribute per request. The hosted server serves
    the GoCBs `register_from_model()` registers; `GoID` and `DatSet` are
    writable while `GoEna` is false, and read-only under
    `set_edition("ed2.1")`. The other six components are read-only, and
    `FixedOffs` always reads false.

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
| TimeActivatedOperate      |  —   |  —   |   —    | — (13) |
| AddCause / LastApplError  | yes  | yes  |  yes   |  yes   |
| Control settings from the model | n/a | yes | n/a | yes |
| Operate timeout           | n/a  | yes  |  n/a   |  yes   |

13. The Rust server refuses an `Oper` carrying an `operTm` other than 0
    with the add cause Not-supported.

A `ctlModel`, `sboTimeout`, `sboClass` or `operTimeout` the model declares
takes precedence over the arguments of `on_control`; `register_from_model()`
registers every controllable data object of the model.

### Time and Time Synchronization (§21)

| Service                      |   Py-C   | Py-S |  Rust-C  | Rust-S  |
|------------------------------|:--------:|:----:|:--------:|:-------:|
| UTC time read (Timestamp DA) |   yes    | yes  |   yes    |   yes   |
| Time-quality flags on update |   n/a    | yes  |   n/a    |   yes   |
| SNTP / NTP responder         |    —     |  —   |   n/a    | yes (9) |
| SNTP / NTP client            | yes (10) | n/a  | yes (10) |   n/a   |

9. SNTPv4 unicast server in `iec61850-sntp` (mode 3 → mode 4 reply).
   Not exposed from Python by design: answering time requests with useful
   accuracy needs the host clock, timestamping close to the socket and a
   UDP port of its own, which an IED's operating system time service
   provides; the package covers the client side (footnote 10).
10. SNTPv4 unicast client. From Python use `iec61850.query_sntp(addr,
    timeout_s)`; from Rust use `iec61850_sntp::SntpClient`. Single-sample
    offset / round-trip via RFC 4330 §5 four-timestamp formula. `addr` is
    an IP address with its port (`"192.0.2.10:123"`); a host name is not
    resolved and raises `ValueError`, so resolve it first, for example with
    `loop.getaddrinfo`.

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

### Not exposed from Python

Beyond GOOSE and Sampled Values frames (footnotes 7 and 8), a few parts of
the Rust API stay out of this package on purpose:

- Raw Ethernet access and the choice of an L2 backend: they need platform
  privileges and driver-level setup.
- `IedServer::tick_log_integrity`: it takes a `std::time::Instant`, which
  Python cannot supply; the server drives integrity logging from its own
  timer.
- `IedServer::lock_data_model`: its guard holds the data-model lock across
  calls, which deadlocks easily from Python; `batch()` covers grouped
  updates.
- `BlockingMmsClient`: the package is async throughout.
- Building GOOSE or Sampled Values publisher settings from the model
  (`CommParameters::from_address`, `SvPublisherBuilder::with_address`): they
  configure frame publishing, which stays out of the package (footnotes 7
  and 8); `gse_address` and `smv_address` read the addresses.
- The internal registries, the reporting engine and the storage traits:
  they bypass the `update_*` path; the server methods above cover what
  they do.
- Request blocking on a server connection (`block_requests` /
  `set_block_requests`): the server uses it for maintenance and while it
  aborts a connection; aborting the connection drops a client outright.
- `IedServer::set_ignore_read_access`: it bypasses every read handler at
  once; register or omit `on_read` handlers instead.
- `IedConnection::get_logical_node_variables`: it returns the raw MMS
  variable names of one logical node, which `get_device_model()` already
  lists for the whole device.
- `IedConnection::get_data_directory_by_fc`: `get_variable_specification`
  with that functional constraint names the same children, with their
  types.
- `ControlObjectClient::wait_command_termination`: `operate()` under an
  enhanced-security model already waits for the CommandTermination and
  reports its outcome.
- The options of `IedServer::register_from_model_with` (log storage
  factory, GOOSE destination factory, wall clock): they supply Rust objects
  the server calls from its own threads on every log entry and
  publication, which from Python would take the GIL on those paths;
  `register_from_model()` uses in-memory logs and the system clock, and
  `register_log_control` configures one log explicitly.
- `DataAttribute::assign`: it writes the model without raising reports or
  log entries; `update_*`, `update_array` and `batch()` do the same with
  triggers.
- The SNTP server (footnote 9).
- `TlsStream::negotiated_cipher_suite`: the client keeps its TLS stream
  inside the connection and exposes none, so there is nothing to ask; the
  suite is always one of the fixed list under [TLS](#tls).
- Persistent report buffers (the SQLite backend): it needs the
  `sqlite-backend` feature, which the wheels do not build, plus a database
  path and a lifetime the application manages; `register_brcb` buffers in
  memory.
- The TLS 1.3 session ticket count of a hosted server
  (`tls13_session_tickets`): it stays at 0, as some TLS 1.3 clients close
  the connection when a ticket arrives; TLS 1.2 session resumption works.
- The IEC 62351-3 TLS event handler (`with_event_handler`): the TLS layer
  calls it synchronously on its own threads for every event, so a Python
  callback would take the GIL inside each handshake; handshake failures
  surface as `IedTlsError` on the client.

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

The certificate chain is always validated against `ca_pem`; there is no
switch to turn that off. Defaults: TLS 1.2-1.3, IEC 62351-3 cipher
whitelist, time validation on, session resumption on. Set
`verify_hostname=False` on `TlsConfig` to skip SNI / SAN hostname matching
for closed-network commissioning, or `time_validation=False` to accept a
certificate outside its validity period (chain validation still applies).

The cipher suites are fixed to the IEC 62351-3 list the underlying TLS
library implements, on the client and on the hosted server alike, and no
argument adds or removes one:

| Version | Cipher suites |
|---------|---------------|
| TLS 1.2 | `TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`, `TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384`, `TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256`, `TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384` |
| TLS 1.3 | `TLS13_AES_128_GCM_SHA256`, `TLS13_AES_256_GCM_SHA384` |

The `TLS_DHE_RSA_*` suites IEC 62351-3 also permits are not offered, so a
peer that supports only those fails the handshake.

### Pinned peers, CRL, version pinning

```python
tls = iec61850.TlsConfig(
    ca_pem=open("ca.pem", "rb").read(),
    # Restrict accepted server certificates to a fixed allow-list
    # (IEC 62351-3 known-peer profile): the server's leaf certificate must
    # equal one of these, compared as whole DER certificates.
    allow_only_known_peers=True,
    known_peer_pems=(open("ied1.crt", "rb").read(),),
    # Pin a single TLS version.
    min_version=iec61850.TlsVersion.TLS_1_3,
    max_version=iec61850.TlsVersion.TLS_1_3,
    # Revocation checks.
    crl_pems=(open("ca.crl.pem", "rb").read(),),
)
```

### Handshake failures

A handshake that fails while the connection is being established raises
`IedTlsError`, a subclass of `IedConnectionError`. Its `kind` says why:
`"verification"` (the peer certificate was not accepted),
`"alert_received"` (the peer refused, for example the client certificate),
`"version_unsupported"`, `"cipher_unsupported"`, `"timeout"`, `"io"` or
`"other"`; `message` holds the TLS error text.

```python
try:
    conn = await iec61850.IedConnection.connect_tls("10.0.0.1:3782", tls)
except iec61850.IedTlsError as e:
    print("TLS refused:", e.kind, e.message)
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
lifecycle directly. `max_outstanding` is limited to 65535.
`max_name_list_names` (on both, and on `Iec61850ClientConfig`) caps the
names one directory walk collects; past it the walk fails with
`IedServiceError` whose `reason` is `"name-list-limit"`.

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

A value no Python type selects is written as a tagged dict, the form
`IedServer.from_model_spec` takes. A BIT STRING carries its octets first bit
first, in the most significant bit of the first octet, and the number of
unused trailing bits in the last octet:

```python
# Dbpos "on" (binary 10): one octet, six unused trailing bits.
await conn.write(
    "LD/XCBR1.PosSet.setVal",
    iec61850.FC.SP,
    {"type": "bit_string", "padding": 6, "data": b"\x80"},
)

# The typed pair does the same and keeps the padding on the way back.
await conn.write_bit_string("LD/XCBR1.PosSet.setVal", iec61850.FC.SP, b"\x80", 6)
data, padding = await conn.read_bit_string("LD/XCBR1.Pos.stVal", iec61850.FC.ST)
```

A structure is written as `{"type": "structure", "value": [...]}`: its
components in order, each a plain value or a tagged dict, so structures nest.
A structure read back, or handed to a server callback, is a list of its
components.

```python
# AnalogueValue with its f component only.
await conn.write(
    "LD/GGIO1.AnOut1.setMag",
    iec61850.FC.SP,
    {"type": "structure", "value": [{"type": "float32", "value": 1.5}]},
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

### Server identity and status

```python
identity = await conn.get_server_identity()      # MMS Identify
print(identity.vendor, identity.model, identity.revision)

status = await conn.get_server_status()          # MMS Status
print(status.logical, status.physical)           # e.g. "state-changes-allowed", "operational"
```

`ServerIdentity.abstract_syntaxes` lists the abstract syntaxes the server
announces as dotted OIDs. `ServerStatus` names a value outside the
standard set `"other"` and keeps the wire value in `logical_code` /
`physical_code`; `local_detail` is the optional detail bit string as a
tuple of `bool`. `get_server_status(extended_derivation=True)` asks the
server to derive the state from a fresh self-check.

### ISO addressing

The association a client opens carries the transport, session and presentation
selectors and the ACSE application-entity titles. The default selectors are the
values IEC 61850-8-1 specifies for an MMS association, and the default AP-titles
and AE-qualifier are the values IEDs customarily expect, which the standard
leaves to the configuration; pass an `IsoConnectionParameters` when a device
expects something else.

| Field                  | Default                | Limit           |
|------------------------|------------------------|-----------------|
| `local_t_sel`          | `b"\x00\x01"`          | 4 bytes         |
| `remote_t_sel`         | `b"\x00\x01"`          | 4 bytes         |
| `local_s_sel`          | `b"\x00\x01"`          | 16 bytes        |
| `remote_s_sel`         | `b"\x00\x01"`          | 16 bytes        |
| `local_p_sel`          | `b"\x00\x00\x00\x01"`  | 16 bytes        |
| `remote_p_sel`         | `b"\x00\x00\x00\x01"`  | 16 bytes        |
| `local_ap_title`       | `"1.1.1.999"`          | dotted OID      |
| `remote_ap_title`      | `"1.1.1.999.1"`        | dotted OID      |
| `local_ae_qualifier`   | `12`                   | signed 32-bit   |
| `remote_ae_qualifier`  | `12`                   | signed 32-bit   |

```python
iso = iec61850.IsoConnectionParameters(
    remote_p_sel=b"\x00\x00\x00\x02",
    remote_ap_title="1.3.9999.13",
)

conn = await iec61850.IedConnection.connect("10.0.0.1:102", iso=iso)

# The same parameter is accepted by connect_tls() and by
# Iec61850ClientConfig(iso=...).
```

An over-long selector or a malformed AP-title raises `ValueError` when the
parameters are built, before any socket is opened. An AP-title of `None` leaves
that field out of the ACSE AARQ.

`password` sends the ACSE password mechanism (a `str` goes out as its UTF-8
octets). It travels in clear unless the association runs over TLS, and the
dataclass `repr` leaves it out. A server that refuses the calling identity or
the credentials raises `IedAuthenticationError`, a subclass of
`IedConnectionError`, whose `diagnostic` and `diagnostic_name` give the
acse-service-user diagnostic of the refusal:

```python
iso = iec61850.IsoConnectionParameters(password="s3cret")
try:
    conn = await iec61850.IedConnection.connect("10.0.0.1:102", iso=iso)
except iec61850.IedAuthenticationError as e:
    print(e.diagnostic, e.diagnostic_name)   # 13 authentication-failure
```

Releases before 0.14.0 always offered empty presentation selectors and no
AP-title or AE-qualifier at all. A peer configured to expect that addressing
can be given it back:

```python
iso = iec61850.IsoConnectionParameters(
    local_p_sel=b"",
    remote_p_sel=b"",
    local_ap_title=None,
    remote_ap_title=None,
    local_ae_qualifier=None,
    remote_ae_qualifier=None,
)
```

A peer that does not recognise the addressing it is offered answers the session
CONNECT with a REFUSE SPDU, which surfaces as `IedSessionRefusedError`:

```python
try:
    conn = await iec61850.IedConnection.connect("10.0.0.1:102", iso=iso)
except iec61850.IedSessionRefusedError as e:
    # ISO 8327-1 reason code, transport-disconnect parameter, and the
    # presentation provider-reason; each is None when the refusal omits it.
    print(e.reason_code, e.transport_disconnect, e.provider_reason)
```

## Log service

A journal is named by the `LogRef` of its log control block,
`"<domain>/<LN>$<name>"`, not by the control block's own reference.

```python
LOG_REF = "IED1LD0/LLN0$EventLog"

# One ReadJournal request. ``more_follows`` signals that the server stopped
# at the PDU size and the caller should resume.
query = iec61850.JournalQuery.by_time(start_ms, end_ms)
entries, more = await conn.query_journal(LOG_REF, query)
for e in entries:
    print(e.time_ms, e.entry_id.hex(), len(e.variables))

# Every entry the selection covers, following ``more_follows``.
entries = await conn.query_journal_all(LOG_REF, query)

# Resume after the last seen entry.
cursor = entries[-1]
later = await conn.query_journal_all(
    LOG_REF, iec61850.JournalQuery.starting_after(cursor.time_ms, cursor.entry_id)
)
```

`JournalQuery` carries every selection ReadJournal defines: a start
(`start_time_ms` or `start_entry`), a stop (`end_time_ms` or
`max_entries`), the `variables` to return, and `after_entry` as a
`(time_ms, entry_id)` pair; it raises `ValueError` for a combination the
service does not allow. `query_journal_all` keeps the selection on every
continuation, returns each entry once, none past `end_time_ms` and no more
than `max_entries`, and raises `IedServiceError` with
`reason == "journal-not-advancing"` when a server reports more entries but
sends none it has not sent before. `query_journal_by_time(log_ref,
start_ms, end_ms)` and `query_journal_after_entry(log_ref, time_ms,
entry_id)` remain as shorthands for one request.

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

`ClientReport.entries` holds one entry per data set member, keeping the last
value an earlier report carried for a member this one omits;
`ClientReport.included` holds one flag per member, set for the members this
report carries:

```python
def on_report(report: iec61850.ClientReport) -> None:
    for entry, carried in zip(report.entries, report.included, strict=True):
        if carried:
            print(entry.ref, entry.value, entry.reason)
```

A report larger than the PDU size the association negotiated arrives in
segments, each delivered to the handler as its own `ClientReport` with the
same `sequence_number`: `sub_seq_num` counts them from zero,
`more_segments_follow` is true on every segment but the last, and
`included` marks only the members of that segment. `sub_seq_num` is `None`
for an unsegmented report. The client does not reassemble segments.

Against a 0.4.0 server (including the one this package hosts):

- `RcbHandle.dataset` and `ClientReport.dataset_name` read back with the
  logical device, `"<LD>/<LN>$<name>"`; a write may omit it.
- Enabling an unreserved URCB reserves it for the writer; writing any other
  attribute of an unreserved URCB does not reserve it. It stays reserved
  after `rpt_ena=False` until the holder writes `resv=False` or its
  association ends. `resv` cannot be written while the URCB is enabled.
- `gi=True` on a disabled control block fails with `TemporarilyUnavailable`.
- Writing `rpt_id`, `dataset`, `opt_flds`, `buf_time`, `trigger_options`,
  `integrity_period`, `purge_buf`, `entry_id` or `resv_tms` of an enabled
  BRCB (as named in `RcbWriteMask`) fails with `TemporarilyUnavailable`.
  `conf_rev` counts `DatSet` changes only, and after a purge `entry_id`
  reads all zeros until the next report is sent.
- The first report after every enable of a BRCB carries `buffer_overflow`
  set, whichever `EntryID` the client resynchronised from.
- A member reports only the triggers its attribute declares (`dchg`,
  `qchg`, `dupd` in the SCL); an update to the value an attribute already
  holds sends no data change, only a data update where `dupd` is declared.

## Control

```python
spc = conn.create_control_object(
    "IED1LD0/GGIO1.SPCSO1",
    iec61850.ControlModel.SBO_ENHANCED,
)
spc.set_origin(iec61850.OriginValue(or_cat=3, or_ident=b"py-client"))

if await spc.select_with_value(True):
    outcome = await spc.operate(True)
    if not outcome:
        print("operate failed:", outcome.add_cause)
        if outcome.last_appl_error is not None:
            print(outcome.last_appl_error.error, outcome.last_appl_error.ctl_num)
```

`select`, `select_with_value`, `operate` and `cancel` all return a
`ControlOutcome`, which is truthy exactly when the command succeeded.
`add_cause` is the `ControlAddCause` of a refusal and `last_appl_error` the
`LastApplError` the server reported with it (`ctl_obj`, `error`, `or_cat`,
`or_ident`, `ctl_num`, `add_cause`), or `None` when it sent none. The
enhanced-security models always report one; a normal-security server may
not, and a refused normal-security `select` (a read of `SBO`) carries no
cause at all, so it reads `unknown`. Add cause 24 is
`ControlAddCause.BLOCKED_BY_COMMAND` (`"blocked-by-command"`);
`ABORTION_BY_COMMAND` remains an alias.

The value given to `select_with_value`, `operate` and `cancel` has to encode
as the `ctlVal` type of the object's common data class (IEC 61850-7-3
§7.5): a `bool` is sent as BOOLEAN, an `int` as a signed INTEGER and a
`float` as a single FLOAT32, and a tagged dict, the form `write` takes, as
the type it names.

| CDC        | `ctlVal`                         | Python value                                                    |
|------------|----------------------------------|-----------------------------------------------------------------|
| SPC, DPC   | BOOLEAN                          | `bool`                                                          |
| INC        | INT32                            | `int`                                                           |
| ENC        | ENUMERATED                       | `int`                                                           |
| ISC        | INT8                             | `int`                                                           |
| BSC, BAC   | CODED ENUM (stop, lower, higher) | `{"type": "bit_string", "padding": 6, "data": b"\x40"}` (lower) |
| APC        | AnalogueValue (a structure)      | `{"type": "structure", "value": [{"type": "float32", "value": 1.5}]}` |

A double point control takes a `bool`, like a single point one; an `int`
is sent as an INTEGER, which a server that checks the type refuses:

```python
dpc = conn.create_control_object(
    "IED1LD0/XCBR1.Pos", iec61850.ControlModel.SBO_ENHANCED
)
if await dpc.select_with_value(True):    # True closes, False opens
    outcome = await dpc.operate(True)
```

An APC's `ctlVal` is an AnalogueValue structure; send it as a tagged
structure whose elements follow the server's declaration, here `f` alone. A
bare `float` goes out as a lone FLOAT32, which a server that checks the type
refuses:

```python
apc = conn.create_control_object(
    "IED1LD0/GGIO1.AnOut1", iec61850.ControlModel.DIRECT_NORMAL
)
await apc.operate(
    {"type": "structure", "value": [{"type": "float32", "value": 1.5}]}
)
```

## GOOSE control blocks

A GOOSE control block is administered over MMS like any other control
block; publishing and subscribing to the frames themselves is out of scope
(see the PICS footnote 7).

```python
gocb = "IED1LD0/LLN0.gcbStatus"          # or "IED1LD0/LLN0$GO$gcbStatus"

values = await conn.get_gocb_values(gocb)
print(values.go_id, values.dat_set, values.conf_rev, values.dst_address)

await conn.set_gocb_values(gocb, go_id="BayStatus")   # while GoEna is false
await conn.set_gocb_values(gocb, go_ena=True)
```

`GoCBValues` carries `go_ena`, `go_id`, `dat_set` (with the logical device),
`conf_rev`, `nds_com`, `dst_address` (a `PhyComAddress` of `addr`,
`priority`, `vlan_id`, `app_id`), `min_time_ms`, `max_time_ms` and
`fixed_offs`; a component the server omits reads `None`.
`set_gocb_values` writes only the arguments given, one attribute per
request. A 0.4.0 server refuses `go_id` and `dat_set` with
`TemporarilyUnavailable` while the block is enabled, and with
`ObjectAccessDenied` when it presents Edition 2.1.

A `dst_address.addr` of six zero octets means the block has no destination.
When the SCL holds a connected access point of the IED (a system
configuration), a server registers a GOOSE control block that has no `<GSE>`
(or a `<GSE>` without a `MAC-Address`) disabled, with that destination, and
refuses to enable it with `TemporarilyUnavailable`. Without a
`<Communication>` section for the IED, as in an ICD or a model spec, the block
keeps the default destination `01-0C-CD-01-00-00`, APPID 0, and can be
enabled.

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

Configuration setters (`vendor`, `model_name`, `revision`, `max_connections`,
the association limits below and `set_edition`) must be called before
`start()`; `set_write_access_policy` also works on a running server. `vendor`, `model_name`, `revision`,
`max_connections` and the association limits are write-only: reading one
raises `AttributeError`.

A hosted server raises these exceptions:

| Exception            | When                                                                                       |
|----------------------|--------------------------------------------------------------------------------------------|
| `OSError`            | `start()` cannot listen on the bound address, for example because the port is in use; `from_scl` cannot read its file |
| `RuntimeError`       | a call in the wrong lifecycle state: a setter or `register_*` after `start()`, `start()` without `bind()` or twice, a running-only call (`bound_addr`, `connection_count`, `stop()`, `batch()`, `log_value`, …) while the server is not running, a second `with_tls()`, concurrent `batch()` blocks |
| `ValueError`         | an argument that does not parse: a `bind()` address, a model spec, a token such as `ctl_model` |
| `KeyError`           | a path, logical device or data set the model does not hold: an `update_*` path, a report control block whose data set is unknown at `start()`, a setting group handler for a logical device without an SGCB |
| `IedDataAccessError` | an `update_*` value that does not fit the attribute's type (e.g. `update_float32` on a `BOOLEAN`) |
| `IedServerError`     | a model or registration the server refuses at `start()` or `register_from_model()`, such as a duplicate data set or control block (`reference` names it) |
| `SclError`           | an SCL document `from_scl` cannot parse or resolve                                         |

While running, `connection_count` reports the open connections,
`abort_connection(conn_id)` aborts the one association a callback's `conn_id`
names and returns whether it found it, `abort_all_connections()` aborts every
association and returns how many it marked, and `dynamic_dataset_count()` counts the data sets clients have
created.

The server bounds what a client can hold open:

| Setter                                 | Default | Effect                                                                   |
|----------------------------------------|---------|--------------------------------------------------------------------------|
| `association_setup_timeout_ms`         | 10 000  | a connection that has not associated by then is closed                   |
| `association_idle_timeout_ms`          | 300 000 | an association with no traffic for this long is aborted; `None` keeps it |
| `association_send_timeout_ms`          | 10 000  | a client that does not accept a PDU within this time is disconnected     |
| `max_dynamic_datasets`                 | 64      | data sets clients may create at run time, across all associations       |
| `max_dynamic_datasets_per_association` | 16      | run-time data sets one association may have created                      |

A report the client accepts counts as traffic, so a client that only
receives reports stays associated.

The supported typed updates are `update_bool`, `update_int32`,
`update_int64`, `update_uint32`, `update_float32`, `update_float64`,
`update_string`, `update_bit_string`, and `update_bit_string_from_int`.

A BIT STRING such as a `Dbpos` or a `Quality` is updated from its octets and
padding, or from an int. The int form takes the declared size n of the
attribute and writes `value` as an n-bit number, most significant bit first:
bit 0 of the string, the first on the wire, carries 2**(n-1).

```python
# Dbpos: 0 intermediate, 1 off, 2 on, 3 bad. 2 is stored as 0x80, padding 6.
server.update_bit_string_from_int("Bay/XCBR1.Pos.stVal", 2)

# The same value from its octets; the unused trailing bits are stored as zero.
server.update_bit_string("Bay/XCBR1.Pos.stVal", bytes([0x80]), 6)
```

The bit count has to fit the declared size, as a remote Write must: exactly
two bits for a `Dbpos`, one to thirteen for a `Quality`. A mismatch raises
`IedDataAccessError` and leaves the attribute unchanged.

An update raises only the trigger options the attribute declares: a changed
value raises a data change for `dchg` and a quality change for `qchg`, and
every update raises a data update for `dupd`. Writing the value an attribute
already holds sends no data change. The sub-attributes of a constructed
attribute take its trigger options, so `mag.f` triggers on what `mag`
declares. A value a client writes raises the same triggers.

### Arrays

An attribute or sub-data object declared with `count` is an array. A client
reads and writes an element by index (`"LD/LN.DO.da(2)"`); the server
updates the whole array in one step:

```python
server.update_array("LD/GGIO1.Arr.stVal", [True, False, True])
```

`update_array` takes one item per element, in index order; an element of a
constructed type is a list in model order or a dict keyed by sub-attribute
name. The shape is checked before any element changes, and the changes go
out as one batch, so a report control block without a buffer time sends one
report for the whole array. An `on_read` handler on an array returns a list
of the same length, and an `on_write` handler on an array receives the whole
array when a client writes one element.

### Communication addressing

The SCL `<Communication>` section is read into the model and is available
before and after `start()`:

```python
[ap] = server.connected_access_point("AP1")
ap["sub_network"], ap["address"]["ip"], ap["address"]["parameters"]["MMS-Port"]

gse = server.gse_address("LD0", "gcbStatus")   # None when there is no <GSE>
gse["mac_address"], gse["app_id"], gse["vlan_id"], gse["vlan_priority"]
gse["min_time_ms"], gse["max_time_ms"]

server.smv_address("LD0", "MSVCB01")            # an <SMV> address, or None
```

Each address carries every `<P>` under `parameters` (type to text) and the
typed `mac_address` (6 bytes), `app_id`, `vlan_id`, `vlan_priority` and `ip`,
`None` when absent. `register_from_model()` sets the `DstAddress`, `MinTime`
and `MaxTime` of a GOOSE control block from its `<GSE>`; a server hosted from
Python sends no GOOSE frames (footnote 7). A malformed `<P>` value raises
`SclError` when the SCL is read, and so does a connected access point of the
loaded IED that names an access point or control block the IED does not
declare. A `<ConnectedAP>` of an IED the file does not declare, and the
other references of the section that name nothing, are skipped with a
warning.

### Registering what the model declares

Loading SCL or a model spec builds the model; the control blocks it declares
are served only once they are registered. `register_from_model()` registers
every data set, report control block instance, GOOSE control block, log
control block and controllable data object of the model:

```python
server = iec61850.IedServer.from_scl("plant.cid", ied_name="IED1")
server.bind("0.0.0.0:102")
server.register_from_model()        # queued; runs at start()
async with server:
    ...
```

Queued before `start()`, it runs after every explicit `add_dataset` /
`register_urcb` / `register_brcb` / `register_log_control` / `on_control`
registration and before the server accepts connections. Called while
running, it registers at once and returns the counts (`data_sets`, `urcbs`,
`brcbs`, `gocbs`, `lcbs`, `sgcbs`, `control_objects`). A report control
block declared with `RptEnabled max="N"` is registered once per instance,
named `<name>01` to `<name>NN` when indexed. A data set or control block
registered under the same reference already raises `IedServerError` with
`reference`; a control object registered by `on_control` keeps its handlers.
Setting group control blocks are served either way.

### Edition and write access

`set_edition("ed1" | "ed2" | "ed2.1")` selects the edition the server
presents; `"ed2"` is the default and `"ed1"` behaves as `"ed2"`. The only
difference is that under `"ed2.1"` a client may not write the `GoID` and
`DatSet` of a GOOSE control block.

`set_write_access_policy(fc, allow)` allows or refuses remote writes under
`"DC"`, `"CF"`, `"SP"`, `"SV"`, `"SE"` or `"BL"`; `SP`, `SV` and `SE` are
allowed by default. A refused write fails with `ObjectAccessDenied` before
any `on_write` handler runs. On a running server a change applies to every
write that arrives afterwards, on associations already open as well.
`write_access_allowed(fc)` reports the policy.

### Association authentication

`set_authenticator(callable)` installs a callable that decides on every
association request; `set_authenticator(None)` removes it, and without one
every association is accepted. It receives a mapping with `peer`,
`calling_ap_title`, `calling_ae_qualifier`, `called_ap_title`,
`called_ae_qualifier`, `mechanism` (`"none"`, `"password"`, `"certificate"`
or `"other"`) and `password` (`bytes` or `None`), and returns `None` or
`True` to accept, `False` to refuse with authentication-failure, or the
name or code of an acse-service-user diagnostic that refuses the calling
identity or the credentials (3 to 6: calling AP-title, AP-invocation
identifier, AE-qualifier or AE-invocation identifier not recognized; 11 to
14: authentication mechanism name not recognized or required,
authentication failure, authentication required) to refuse with that
diagnostic. Any other value, including any other diagnostic such as 0
(null), an unknown name, or a raised exception refuses with
authentication-failure. The refused client receives a Session REFUSE whose
AARE carries the diagnostic and an Initiate-ErrorPDU with access
object-access-denied; this package's client raises `IedAuthenticationError`
for diagnostics 3 to 6 and 11 to 14, so every refusal by an authenticator
reaches it as that exception.

```python
def authenticate(request: dict) -> bool | str | None:
    if request["calling_ap_title"] not in ALLOWED_AP_TITLES:
        return "calling-AP-title-not-recognized"
    return request["password"] == b"s3cret"

server.set_authenticator(authenticate)
```

The server calls the callable while the association is set up, on a thread
of its bounded blocking pool, not on a runtime worker (as it calls the other
Python callbacks, see [Read / write callbacks](#read--write-callbacks)). A
wait that releases the GIL (`threading.Event.wait`, socket I/O,
`time.sleep`) does not hold up other associations, though while the pool is
full further callbacks queue; computation that holds the GIL stalls every
Python callback of the process. Keep it short. Installed before
`start()`, it is in place before the first association; on a running server
it applies to the associations requested afterwards. A password reaches it in
clear unless the association runs over TLS.

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
a pinned peer list (only a client whose leaf certificate is byte for byte
the DER of one of the known peers is accepted; the whole certificate is
compared, not its public key, so a renewed certificate has to be added to
the list):

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

Defaults: TLS 1.2 - 1.3, the IEC 62351-3 cipher suites listed under
[TLS](#tls), time validation on, session resumption on (TLS 1.2 session
IDs; the server sends no TLS 1.3 session ticket, so a TLS 1.3 session does
not resume), no client-cert pinning; a client certificate
chain is always validated against `client_ca_pem`. `min_tls_version` and
`max_tls_version` accept `"tls1.2"` / `"tls1.3"`.

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

| Return      | Behavior                             |
|-------------|--------------------------------------|
| any scalar  | the value is returned to the client  |
| tagged dict | the value is returned to the client  |
| `None`      | fall through to the cached value     |
| raises      | read fails with `IedDataAccessError` |

A tagged dict takes the form `from_model_spec` accepts for `value`, so a
callback can serve a BIT STRING such as `Dbpos`:
`{"type": "bit_string", "padding": 6, "data": b"\x80"}` reads as the on
state. A dict that does not convert fails the read with `TypeInconsistent`
and prints the reason through the standard exception hook.

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

A handler decides every request that covers its attribute: a read of the
attribute itself, of a node above it (its data object, its logical node, a
functional-constraint group), of a node below it, and of a data set holding
it; a write to the attribute or to any node above or below it. A handler on
a data object or constructed attribute returns its value as a list in model
order. The server holds no lock while a handler runs, so it may call the
`update_*` methods.

The Python callbacks of a hosted server (read, write, control `check`, a
synchronous control `operate` or `wait`, setting group and authenticator)
run on a thread of the server's bounded blocking pool, not on a runtime
worker, and take the GIL there; an `async def` `operate` or `wait` is called
there too and its coroutine then runs on the event loop that started the
server. A blocking wait in one does not hold up other associations, though
while the pool is full further callbacks queue; computation that holds the
GIL stalls every Python callback. Successive calls may run on different threads, so
`threading.local` state does not carry over, and callbacks of different
associations may interleave.

Pass `with_context=True` to receive the request as well:

```python
def on_read(path: str, ctx: dict) -> object:
    # ctx: {"path": "GGIO1$ST$Ind1$stVal", "fc": "ST", "conn_id": 3,
    #       "requested": "GGIO1$ST$Ind1"}
    return read_io(path)

server.on_read("GenericIO/GGIO1.Ind1.stVal", on_read, with_context=True)
server.on_write("GenericIO/GGIO1.SetPt1.setVal",
                lambda path, value, ctx: True, with_context=True)
```

`path` is the canonical path the handler is registered against and
`requested` the path the client named.

### Control callbacks

`on_control` binds the server-side execution of a control object. Address
it at the DO (`"<LD>/<LN>.<DO>"`) and declare which IEC 61850 control
model the DO uses. Up to three callbacks may be supplied, and they differ in
how they refuse a command:

| Callback  | Kind           | Runs                                                                   | Refuses the command when it                       |
|-----------|----------------|------------------------------------------------------------------------|---------------------------------------------------|
| `check`   | sync only      | on Select (`ctl_val` is `None`), SelectWithValue and Operate, first     | raises; its return value is ignored               |
| `wait`    | sync or `async` | on Operate, after `check` and before the Operate response, under every control model | raises, or returns `False`                        |
| `operate` | sync or `async` | on Operate, after a positive Operate response                         | raises, or returns `False`                        |

- `check` is static validation: interlocks, mode, permissions. It is
  called as a plain function, so an `async def` check is never awaited and
  never refuses anything.
- `wait` is the dynamic check, the WaitForExecution state of IEC 61850-7-2
  (for example waiting for a synchro-check confirmation). Its outcome
  decides the Operate response.
- `operate` executes the command. An Operate on an object registered
  without one is refused with `NotSupported`.

A raised exception refuses with the add cause it carries (see below); a
`False` return refuses with `Unknown`. `None`, `True` and any other return
value accept.

`operate` runs only after the client has received a positive Operate
response. Under the enhanced-security models its failure reaches the client
as a negative CommandTermination carrying the add cause. Under the
normal-security models it is not reported to the client at all, so refuse
a command from `check` or `wait` instead.

Each callback receives `(path, ctl_val, action)`. `action` is a dict with
`ctl_num`, `test`, `synchro_check`, `interlock_check`, `is_select`, `t`
(the request timestamp as eight UTC_TIME octets), `conn_id` (the
association that issued it), and `origin` (a sub-dict with `or_cat` and
`or_ident`).

The server refuses a time-activated operate (an `Oper` with an `operTm`)
with `NotSupported`.

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
behavior. `oper_timeout_ms` (default 0, none) ends a command that executes
for longer with `TimeLimitOver`. A `ctlModel`, `sboTimeout`, `sboClass` or
`operTimeout` the model declares takes precedence over these arguments, so
declare the control model in the SCL rather than only passing it here. The
server maintains `stSeld` itself.

Raise an exception from any callback to reject the command. Set
`add_cause` on the exception to the variant name (e.g.
`"BlockedByInterlocking"`, `"BlockedByProcess"`, `"NotSupported"`) or
its numeric MMS code; absence falls back to `"Unknown"`.

### Datasets and unbuffered reporting

Declare a server-side dataset and bind it to an Unbuffered Report Control
Block (URCB) before `start()`. The same client APIs (`get_rcb_values`,
`set_rcb_values`, `install_report_handler`) consume reports from the URCB
once the server is running.

The server stores log entries and delivers reports without calling Python
code. An `update_*` that arrives while another thread's update is being
delivered waits for that delivery, so triggers are delivered in the order the
values were stored; a value overwritten within one client write request is
not delivered on its own.

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
`"data_reference"`, `"conf_rev"`, `"buffer_overflow"`, `"entry_id"`. An
unbuffered control block has no buffer and no EntryID (IEC 61850-7-2
§17.2.4), so the server clears `buffer_overflow` and `entry_id` from its
reports.

Datasets without a URCB are still reachable via `get_data_set_values`.

A report carries only the reasons the control block's `TrgOps` enable, for
the triggers each member's attribute declares: an SCL attribute without
`dchg="true"` (or `qchg`, `dupd`) sends no report on that trigger. A data
object or constructed attribute member is included whole when any attribute
beneath it triggers. A client reads `DatSet` back as
`"<LD>/<LN>$<name>"`.

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

A BRCB sends `buffer_overflow` and `entry_id` when they are selected
(IEC 61850-7-2 §17.2.2.8); a URCB clears them. Additional knobs:

- `buffer_capacity` (default `64`) — entry-count ring size; the buffer
  evicts oldest entries when full and surfaces overflow through the
  `buffer_overflow` field on the next report. The first report after every
  enable also carries `buffer_overflow` set.
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
entries are evicted on overflow). Clients name the journal by the block's
`log_ref` and pull it over MMS `ReadJournal` with `query_journal` /
`query_journal_all`.

```python
server.add_dataset("LLN0$evlogds", ["GenericIO/GGIO1.Ind1.stVal"])
server.register_log_control(
    "GenericIO/LLN0.lcb01",
    dataset="LLN0$evlogds",
    log_ref="IED1GenericIO/LLN0$EventLog",
    trg_ops=["data_changed", "integrity"],
    intg_pd_ms=60_000,
    storage_capacity=1000,
)

async with server:
    server.update_bool("GenericIO/GGIO1.Ind1.stVal", True)   # logged
    server.log_value(
        "GenericIO/LLN0.lcb01",
        data_ref="IED1GenericIO/GGIO1$ST$Ind2$stVal",
        value=True,
        reason_code=0x02,            # bit 1 = data_changed
    )
```

The server records the journal itself: an update of a member of the
block's data set (registered with `add_dataset` or `register_from_model`)
writes an entry when the block's `trg_ops` enable a reason the update
raises — data change, quality change or data update — and integrity writes
one entry holding the whole data set every `intg_pd_ms`. General
interrogation writes none. Updates made inside `batch()` write one entry per
block. `log_value` adds an entry of the application's own, for example for
a value outside the data set or with a chosen timestamp, and returns its
8-byte entry id (as an `int`), or `None` when the block's `LogEna` is
disabled. Toggle the enable state at runtime with `set_log_ena(path, on)`.

### Setting groups (SGCB)

A Setting Group Control Block is declared in SCL on `<LN0>`:

```xml
<LN0 lnClass="LLN0" inst="" lnType="LLN0_0">
  <SettingControl numOfSGs="3" actSG="1" resvTms="60"/>
</LN0>
```

The server tracks ActSG / EditSG / ConfirmEditSG state, enforces the
single-client edit-session lock, and times out abandoned reservations.
Python applications opt into the three veto / commit points per LD.
`on_act_sg(new_sg, conn_id)`, `on_edit_sg(new_sg, conn_id)` and
`on_confirm(edit_sg, conn_id)` all return `bool | None`. `None` or `True`
accepts the request. `False`, any other return value, or a raised exception
refuses it with ObjectAccessDenied: the client's write raises
`IedDataAccessError` with `code == 3`. A refused confirmation commits nothing
and leaves the edit session open. `on_confirm` runs before the staged FC=SE
values are committed, so it is where an application persists them.

```python
def on_act_sg(new_sg: int, conn_id: int) -> bool:
    return new_sg in allowed_sgs        # return False → ObjectAccessDenied

def on_confirm(edit_sg: int, conn_id: int) -> None:
    persist_pending_settings(edit_sg)   # None accepts; False refuses

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

The server holds the values of every group. SelectActiveSG loads the
values of the new group into the FC=SG attributes, SelectEditSG loads the
edited group into FC=SE, and ConfirmEditSGValues stores FC=SE back into
the group (and into FC=SG when it is the active one). Read or replace a
group's value from the application with:

```python
server.get_setting_group_value("GenericIO/PTOC1.StrVal.setMag.f", 2)
server.set_setting_group_value("GenericIO/PTOC1.StrVal.setMag.f", 2, 1.25)
```

Replacing a value of the active group updates the served attribute too; a
value of another group takes effect when that group is activated. In SCL a
setting carries one `<Val sGroup="n">` per group.

Clients drive SGCB through the regular write API on the special MMS
path. `ActSG` and `EditSG` are INT8U, so write them as a tagged unsigned
value (or with `write_uint32`); a plain Python `int` is sent as a signed
MMS INTEGER, which a server that checks the declared type refuses:

```python
group = {"type": "uint", "value": 2}
await conn.write("IED1GenericIO/LLN0.SGCB.ActSG", FC.SP, group)   # SelectActiveSG
await conn.write("IED1GenericIO/LLN0.SGCB.EditSG", FC.SP, group)  # open edit
await conn.write("IED1GenericIO/LLN0.SGCB.CnfEdit", FC.SP, True)  # commit
```

The server this package hosts also accepts a non-negative INTEGER there.

### Building a server from a model dict (no SCL)

When the IED schema is generated in Python — code-driven test rigs,
dynamic device skeletons, or runtimes that prefer dict-driven
configuration over XML — `from_model_spec` consumes a declarative
spec dict that maps onto the same model the SCL parser produces. The
RCB / LCB / SGCB declarations land in the model exactly as SCL-derived ones
would: the SGCB is served on its own, and `register_from_model()` registers
the data sets, RCBs and LCBs. Every callback and `register_*` method works
against the same `"<LD>/<LN>.<DO>[.<DA>]*"` paths.

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

`type` takes one of these tokens, spelled exactly so:

| Group            | Tokens |
|------------------|--------|
| Boolean          | `"Boolean"` |
| Signed integers  | `"Int8"`, `"Int16"`, `"Int24"`, `"Int32"`, `"Int64"`, `"Int128"` |
| Unsigned         | `"Int8U"`, `"Int16U"`, `"Int24U"`, `"Int32U"` |
| Floating point   | `"Float32"`, `"Float64"` |
| Enumerations     | `"Enumerated"`, `"CodedEnum"` (two bits, such as a `Dbpos`) |
| Strings          | `"VisibleString"`, `"OctetString"`, `"UnicodeString255"` |
| Bit strings      | `"GenericBitString"`, `"Quality"`, `"Check"`, `"OptFlds"`, `"TrgOpsBits"`, `"SvOptFlds"`, `"LogOptFlds"` |
| Time             | `"Timestamp"`, `"EntryTime"` |
| Other            | `"PhyComAddr"`, `"Currency"`, `"Constructed"` |

`"VisibleString"` and `"OctetString"` take a maximum length in bytes and
`"GenericBitString"` one in bits, through the object form
`{"type": "OctetString", "max_len": 64}`; without it they default to 64,
64 and 8. Any other spelling raises `ValueError`, and so does an SCL
`bType` name (`BOOLEAN`, `INT32`, `Dbpos`, `VisString64`, …), which is not a
token. The SCL types translate as follows:

| SCL `bType`                                  | `type`                                          |
|----------------------------------------------|-------------------------------------------------|
| `BOOLEAN`, `INT8` … `INT128`, `INT8U` … `INT32U`, `FLOAT32`, `FLOAT64` | the token of the same name in the case above (`"Boolean"`, `"Int8"`, `"Int8U"`, `"Float32"`, …) |
| `Enum`                                       | `"Enumerated"`                                  |
| `Dbpos`, `Tcmd`                              | `"CodedEnum"`                                   |
| `VisString32` … `VisString255`               | `{"type": "VisibleString", "max_len": n}`       |
| `ObjRef`                                     | `{"type": "VisibleString", "max_len": 129}`     |
| `Octet6`, `Octet16`, `Octet64`               | `{"type": "OctetString", "max_len": n}`         |
| `EntryID`                                    | `{"type": "OctetString", "max_len": 8}`         |
| `Unicode255`                                 | `"UnicodeString255"`                            |
| `TrgOps`                                     | `"TrgOpsBits"`                                  |
| `Struct`                                     | an entry under `"constructed_das"` (below)      |
| `Quality`, `Timestamp`, `Check`, `EntryTime`, `PhyComAddr`, `Currency`, `OptFlds`, `SvOptFlds`, `LogOptFlds` | the same name |
`value` is a tagged dict (`{"type": "int", "value": 1}`, `{"type":
"bit_string", "padding": 3, "data": b"\x00\x00"}`, …) or
`{"type": "default"}` for the type's zero. Constructed (SDA-bearing)
DAs go under `"constructed_das"` with a `"children"` list; nested DOs
go under `"sub_dos"`. The underlying `IedModelBuilder` enforces all
invariants (LLN0 first, SGCB only on LLN0, dataset entries resolve to
real LN/DO) and surfaces violations as `ValueError`.

### Batch updates

`server.batch()` returns a synchronous context manager that holds the
server's data-model lock for the duration of the `with` block. The
triggers of the updates made inside it are raised together when the block
exits: a report control block without a buffer time sends one report, a
buffered one stores one entry, and a log control block writes one entry.
Buffer-time expiry and integrity and general-interrogation reports wait
until the block exits, so keep it short. Client reads are not held back.
Concurrent batches raise `RuntimeError` rather than deadlocking, so callers
can choose to retry or fail fast.

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
except iec61850.IedTimeoutError as e:
    ...   # connection timed out after e.timeout_ms
except iec61850.IedSessionRefusedError:
    ...   # peer refused the association at the session layer
except iec61850.IedTlsError as e:
    ...   # TLS handshake failed; e.kind says why
except iec61850.IedConnectionError as e:
    ...   # TCP / OSI stack failure; e.reason says which
except iec61850.IedError:
    ...   # catch-all base for any IEC 61850 error
```

Each exception carries attributes a caller can branch on; an attribute that
does not apply reads `None`.

| Exception                | Attributes                                                                                     |
|--------------------------|------------------------------------------------------------------------------------------------|
| `IedConnectionError`     | `reason` (`"connection-lost"`, `"released-by-peer"`, `"transport"`, `"associate-failed"`, `"initiate-failed"`), `kind` and `detail` of a transport failure, `code` of a refused Initiate |
| `IedSessionRefusedError` | `reason_code`, `transport_disconnect`, `provider_reason`                                       |
| `IedAuthenticationError` | `diagnostic` and `diagnostic_name` of the refusing AARE; `reason` is `"authentication-rejected"` |
| `IedTlsError`            | `kind` (`"verification"`, `"alert_received"`, `"version_unsupported"`, `"cipher_unsupported"`, `"timeout"`, `"io"`, `"other"`), `message` |
| `IedTimeoutError`        | `timeout_ms`                                                                                   |
| `IedServiceError`        | `reason` (`"service-error"`, `"reject"`, `"conclude-rejected"`, `"outstanding-limit"`, `"pdu-too-large"`, `"name-list-limit"`, `"name-list-not-advancing"`, `"journal-not-advancing"`), `error_class`, `code`, `reject_type`, `number_deleted`, `pdu_size`, `max_size`, `limit` |
| `IedDataAccessError`     | `code`, the MMS DataAccessError value                                                          |
| `IedServerError`         | `reference` of a refused duplicate registration                                                |

`IedSessionRefusedError` and `IedTlsError` derive from
`IedConnectionError`. `IedTimeoutError` derives from `IedError` only: a
request, connect or command-termination timeout is not caught by
`except IedServiceError`. A connection that drops after it was established
raises `IedConnectionError` (`reason` `"connection-lost"`,
`"released-by-peer"` or `"transport"`), not `IedServiceError`.
`reason == "transport"` is an I/O failure other than a close, such as a
refused connection; a connection the peer resets or closes is
`"connection-lost"`.

`timeout_ms` of `connect` and `connect_tls` bounds the TCP connection, the TLS
handshake (`connect_tls`) and the COTP handshake together. Running out of it
raises `IedTimeoutError` whose `timeout_ms` is the value given; running out of
it during the TLS handshake raises `IedTlsError` with `kind == "timeout"`. The
Session, ACSE and MMS Initiate exchange that follows is bounded by
`request_timeout_ms` (5000 ms when not given), whose expiry raises
`IedTimeoutError` carrying that value.

## Release notes

### 0.16.0

Tracks iec61850-rust v0.4.0.

#### Breaking changes

- `TlsConfig`, `IedConnection.connect_tls` and `IedServer.with_tls` lose
  `chain_validation`: the certificate chain is always validated. Drop the
  argument.

  ```python
  # 0.15
  tls = iec61850.TlsConfig(ca_pem=ca, chain_validation=True)
  # 0.16
  tls = iec61850.TlsConfig(ca_pem=ca)
  ```

- `ControlObjectClient.select()` returns a `ControlOutcome` instead of a
  `bool`. An outcome is truthy exactly when it succeeded, so
  `if await ctl.select():` keeps working; compare `.success` where the code
  tested identity.

  ```python
  # 0.15
  assert await ctl.select() is True
  # 0.16
  assert (await ctl.select()).success
  ```

- A `ControlOutcome` is truthy exactly when the command succeeded. In 0.15
  it had no truth value of its own, so `if await ctl.operate(v):` and
  `if await ctl.cancel(v):` were always true; they now take the failure
  branch when the server refuses. Handle the refusal there, or drop the
  test where the code has to run either way.

  ```python
  # 0.15: always entered
  if await ctl.operate(True): ...
  # 0.16: entered only on success
  outcome = await ctl.operate(True)
  if outcome: ...
  else: print(outcome.add_cause)
  ```

- Add cause 24 reads as `"blocked-by-command"` instead of
  `"abortion-by-command"`. `ControlAddCause.ABORTION_BY_COMMAND` stays an
  alias of `BLOCKED_BY_COMMAND` and the old token still parses, but code
  that compares the string no longer matches. Compare the enum member.

  ```python
  # 0.15
  if outcome.add_cause == "abortion-by-command": ...
  # 0.16
  if outcome.add_cause is iec61850.ControlAddCause.BLOCKED_BY_COMMAND: ...
  ```

- The `action` dict passed to `on_control` callbacks carries `t` (the
  request timestamp as eight UTC_TIME octets) and `conn_id` instead of
  `ctl_time_ms`.

  ```python
  # 0.15
  when_ms = action["ctl_time_ms"]
  # 0.16
  t_octets, conn = action["t"], action["conn_id"]
  ```

- Client failures raise the exception class of their cause. The class
  hierarchy is unchanged (`IedTimeoutError` derives from `IedError` only, as
  before); what moves is which class a failure raises:

  | Failure                                              | 0.15                 | 0.16                                                    |
  |------------------------------------------------------|----------------------|---------------------------------------------------------|
  | A request times out                                  | `IedServiceError`    | `IedTimeoutError`                                       |
  | A connect times out in the TCP or COTP stage         | `IedConnectionError` | `IedTimeoutError`                                       |
  | An established connection is lost, released by the peer, or fails in transport | `IedServiceError` | `IedConnectionError` with `reason` `"connection-lost"`, `"released-by-peer"` or `"transport"` |
  | The peer refuses the association for the calling identity or the credentials | `IedSessionRefusedError` | `IedAuthenticationError` (a subclass of `IedConnectionError`, not of `IedSessionRefusedError`) |

  `except IedServiceError` no longer catches a request timeout or a lost
  connection, `except IedConnectionError` around `connect` no longer
  catches a connect timeout, and `except IedSessionRefusedError` no longer
  catches an authentication refusal. Catch the classes together, or branch on
  `reason`:

  ```python
  # 0.15
  except iec61850.IedServiceError: ...   # also caught timeouts and disconnects
  # 0.16
  except (iec61850.IedServiceError, iec61850.IedTimeoutError,
          iec61850.IedConnectionError): ...
  # or
  except iec61850.IedConnectionError as e:
      if e.reason in ("connection-lost", "released-by-peer", "transport"):
          ...   # reconnect
  ```

- The data sets, report, GOOSE and log control blocks and control objects a
  model declares are served only once registered. Call
  `register_from_model()`, or keep the explicit `add_dataset` /
  `register_*` / `on_control` calls. A report control block registered from
  the model is named per instance, `<name>01` when indexed.

  ```python
  # 0.15
  server = iec61850.IedServer.from_scl("plant.cid", ied_name="IED1")
  # 0.16
  server = iec61850.IedServer.from_scl("plant.cid", ied_name="IED1")
  server.register_from_model()
  ```

- A journal is named by the `LogRef` of its log control block, not by the
  control block reference.

  ```python
  # 0.15
  await conn.query_journal_by_time("IED1LD0/LLN0$LG$lcb01", t0, t1)
  # 0.16
  await conn.query_journal_by_time("IED1LD0/LLN0$EventLog", t0, t1)
  ```

- A hosted server writes the journal itself for every member of an LCB's
  data set (data change, quality change, data update and integrity, as the
  block's `TrgOps` select). Stop calling `log_value` for those members, or
  each change is logged twice; register the data set so the block can
  record it.

  ```python
  # 0.15
  server.update_bool("LD0/GGIO1.Ind1.stVal", True)
  server.log_value("LD0/LLN0.lcb01", data_ref=ref, value=True)
  # 0.16
  server.add_dataset("LLN0$dsStatus", ["LD0/GGIO1.Ind1.stVal"])
  ...
  server.update_bool("LD0/GGIO1.Ind1.stVal", True)    # logged
  ```

- Under the normal-security control models a failing `operate` callback is
  no longer reported to the client. Refuse the command from `check` or
  `wait`.

  ```python
  # 0.15
  server.on_control(path, ctl_model="direct-normal", operate=refuse_if_blocked)
  # 0.16
  server.on_control(path, ctl_model="direct-normal",
                    check=refuse_if_blocked, operate=operate)
  ```

- A `ctlModel`, `sboTimeout`, `sboClass` or `operTimeout` the model declares
  takes precedence over the arguments of `on_control`. Declare the control
  model in the SCL.

  ```xml
  <!-- 0.15: on_control(..., ctl_model="sbo-normal") overrode this -->
  <DAI name="ctlModel"><Val>direct-with-normal-security</Val></DAI>
  <!-- 0.16 -->
  <DAI name="ctlModel"><Val>sbo-with-normal-security</Val></DAI>
  ```

- An attribute reports only the trigger options it declares. Declare
  `dchg` (or `qchg`, `dupd`) on an SCL attribute that should report, and
  give a model-spec attribute that should report a `trg_ops` list; one
  without it raises none.

  ```xml
  <!-- 0.15 -->
  <DA name="stVal" fc="ST" bType="BOOLEAN"/>
  <!-- 0.16 -->
  <DA name="stVal" fc="ST" bType="BOOLEAN" dchg="true"/>
  ```

- `max_outstanding` is limited to 65535.

- `MmsValue` and `ControlParams` are removed. No method returned or took
  either: a generic `read()` returns native values (`list`, `bytes`, ...),
  and `ControlObjectClient` takes its settings through its setters. Drop the
  imports.

  A value written as an `MmsValue` is written as a native or tagged value:

  | 0.15                           | 0.16                                  |
  |--------------------------------|---------------------------------------|
  | `MmsValue.structure(...)`      | `{"type": "structure", "value": [...]}` |
  | `MmsValue.array(...)`          | a `list`                              |
  | `MmsValue.octet_string(b)`     | `bytes`                               |

  Each `ControlParams` field has a setter or an argument of its own:

  | `ControlParams` field           | 0.16                                                    |
  |---------------------------------|---------------------------------------------------------|
  | `ctl_val`                       | the argument of `operate(value)`                        |
  | `test`                          | `set_test(True)`                                        |
  | `check_synchrocheck`            | `set_synchro_check(True)`                               |
  | `check_interlock`               | `set_interlock_check(True)`                             |
  | `origin_orcat`, `origin_orident`| `set_origin(OriginValue(or_cat=..., or_ident=...))`     |
  | `operate_time`                  | no replacement: a 0.4.0 server refuses an `Oper` carrying an `operTm` with `NotSupported` |

  ```python
  # 0.15
  await ctl.operate(iec61850.ControlParams(ctl_val=True, test=True))  # never accepted
  # 0.16
  ctl.set_test(True)
  await ctl.operate(True)
  ```

- `InclusionReason.application_trigger` is removed. It was never set: the
  client decodes data change, quality change, data update, integrity and
  general interrogation. The ReasonCode bit it stood for, bit 6, is not used
  for reports by IEC 61850-8-1, so no report carries it and no capability is
  lost. Drop the reads.

- A TLS handshake that stalls past `timeout_ms` raises `IedTlsError` with
  `kind == "timeout"` (a subclass of `IedConnectionError`) instead of
  `IedTimeoutError`. Catch both around `connect_tls`:

  ```python
  try:
      conn = await IedConnection.connect_tls(addr, tls, "ied1", timeout_ms=3000)
  except (iec61850.IedTimeoutError, iec61850.IedTlsError) as e:
      ...
  ```

- `on_confirm` can refuse a confirmation. In 0.15 its return value was ignored
  and an exception was only reported, so the commit always went ahead. Now
  `False`, a return value other than `None` or a bool, or an exception refuses
  the ConfirmEditSGValues with ObjectAccessDenied. A callback that raised to
  report a problem but still wants the commit must catch it, and one that
  returned a truthy non-bool must return `True` or `None`:

  ```python
  def on_confirm(edit_sg: int, conn_id: int) -> None:
      try:
          persist(edit_sg)
      except OSError:
          log.exception("persisting setting group %d failed", edit_sg)
      # returning None accepts the confirmation
  ```

#### Added

- `IedConnection.get_server_identity()` (MMS Identify) returns a
  `ServerIdentity`; `get_server_status(extended_derivation=False)` (MMS
  Status) returns a `ServerStatus`.
- `JournalQuery` and `IedConnection.query_journal(log_ref, query)`, one
  ReadJournal request with any selection, and `query_journal_all`, which
  follows `more_follows` and returns each entry once.
- `max_name_list_names` on `connect`, `connect_tls` and
  `Iec61850ClientConfig` caps the names one directory walk collects.
- `ClientReport.included`, `sub_seq_num` and `more_segments_follow`.
- `IedConnection.get_gocb_values` and `set_gocb_values`, with the
  `GoCBValues` and `PhyComAddress` dataclasses.
- `ControlOutcome.last_appl_error` and the `LastApplError` dataclass;
  `ControlAddCause.BLOCKED_BY_COMMAND` (`"blocked-by-command"`), with
  `ABORTION_BY_COMMAND` kept as an alias and its token still parsed.
- `IedTlsError`, a subclass of `IedConnectionError`, for a failed TLS
  handshake, with `kind` and `message`; 0.15 raised a plain
  `IedConnectionError`, so `except IedConnectionError` still catches it. `IedConnectionError`,
  `IedServiceError`, `IedTimeoutError`, `IedDataAccessError` and
  `IedServerError` carry attributes naming the cause (see
  [Error handling](#error-handling)).
- `IedServer.register_from_model()`, `update_array(path, values)`,
  `set_edition(edition)`, `get_setting_group_value(path, group)`,
  `set_setting_group_value(path, group, value)`,
  `set_write_access_policy(fc, allow)`, `write_access_allowed(fc)`,
  `abort_all_connections()` and `dynamic_dataset_count()`.
- `on_read` and `on_write` take `with_context=True` to receive the request
  context (`path`, `fc`, `conn_id`, `requested`).
- `on_control` takes `oper_timeout_ms`.
- Raw SCL type dictionaries carry `count` and `count_ref` for DA, BDA and
  SDO declarations, and a report control dictionary carries `indexed`.
- `from_model_spec` accepts the attribute types `Int24`, `SvOptFlds` and
  `LogOptFlds`, and a report control block in a spec takes `indexed` and
  `max_instances` (not indexed unless asked, so its name is kept).
- `IedServer.update_quality(path, quality)` takes a `Quality` or the integer
  `Quality.to_bits()` returns, in the bit order `read_quality` decodes;
  `update_bit_string_from_int` keeps its most-significant-bit-first order.
- `spawn_report_dispatcher` takes `on_error`, called once with the exception
  that ends polling; `ReportDispatcher.done` and `exception()` report it.
- `IedConnection.read_timestamp_with_quality(ref, fc)` returns the time and
  its `TimeQuality` (`leap_second_known`, `clock_failure`,
  `clock_not_synchronized`, `time_accuracy`).
- `IedServer.with_tls` takes `require_client_auth`: with a `client_ca_pem`,
  `True` refuses a client that presents no certificate (by default such a
  client is admitted); without a `client_ca_pem`, `start()` raises
  `ValueError`.
- `AcsiClass.LOG_CONTROL` (`"LCB"`) lists the log control blocks of a logical
  node; `AcsiClass.LOG` lists its logs (journals).
- `IedServer.abort_connection(conn_id)` aborts the one association named by
  the `conn_id` the read, write and control callbacks receive. That connection
  sends an ACSE A-ABORT and closes, possibly after the call returns; every
  other association stays open. It returns `True` when it found the
  association and `False` for an id naming no open association. Raises
  `RuntimeError` when the server is not running.
- Tagged value `{"type": "structure", "value": [...]}`: the components in
  order, each a plain value or a tagged dict, so structures nest. `write`,
  `select_with_value`, `operate` and `cancel` accept it; an APC sends its
  AnalogueValue `ctlVal` as
  `{"type": "structure", "value": [{"type": "float32", "value": 1.5}]}`.
  A server control callback receives a structure as a list of its components.
- `SclError.kind` reads `"UnresolvedDataSetReference"` for a control block
  whose `datSet` names no data set of its logical node, which the SCL reader
  refuses.

- ACSE authentication: `IsoConnectionParameters.password` (the ACSE password
  mechanism), `IedAuthenticationError` with `diagnostic` and
  `diagnostic_name`, and `IedServer.set_authenticator(callable)`, which
  decides on every association request from its AP-titles, AE-qualifiers and
  credentials.
- `IedServer.set_write_access_policy` also applies on a running server, to the
  writes that arrive afterwards.

- `IedServer.connected_access_point(ap_name)`, `gse_address(ld_inst,
  cb_name)` and `smv_address(ld_inst, cb_name)` read the SCL
  `<Communication>` addressing from the model.

#### Fixed

- `connect` and `connect_tls` honour a `timeout_ms` above 10 s. The attempt
  used to end after 10 s with `timeout_ms == 10000`, and a shorter deadline
  raised `IedTimeoutError` with `timeout_ms` unset; it now carries the value
  given. `timeout_ms` now bounds the TCP connection, the TLS handshake and the
  COTP handshake only, no longer the Session, ACSE and MMS Initiate exchange
  that follows, which `request_timeout_ms` bounds (5000 ms when not given): a
  peer that answers COTP but not the session can hold
  `connect(timeout_ms=2000)` for about 5 s.

#### Changed behavior (from iec61850-rust 0.4.0)

- Reports: a report larger than the negotiated PDU size arrives in
  segments, each delivered separately; a report carries only the reasons
  its control block's `TrgOps` enable; rewriting a value with itself sends
  no data change; the sub-attributes of a constructed attribute take its
  trigger options, so `mag.f` triggers on what `mag` declares; a value a
  client writes raises the triggers its attribute declares.
- `batch()` raises the triggers of its updates as one batch on exit (one
  report without a buffer time, one log entry per log control block);
  periodic reports wait for it, and client reads are not held back.
- Logging: data update and integrity are recorded as well as data and
  quality change; general interrogation writes no entry.
- Report control blocks: `DatSet` reads back as `"<LD>/<LN>$<name>"`;
  enabling an unreserved URCB reserves it and disabling keeps the
  reservation until `resv=False`; writing any other attribute of an
  unreserved URCB does not reserve it; `gi=True` on a disabled block, and
  configuration, `purge_buf` and `entry_id` writes on an enabled BRCB,
  fail with `TemporarilyUnavailable`; `conf_rev` counts `DatSet` changes
  only; the first report after every BRCB enable carries `BufOvfl`; a
  full report channel no longer clears `RptEna` or `Resv`.
- `Owner` and `ResvTms` of an SCL-built control block exist only when its
  `<ReportSettings>` declares `owner="true"` / `resvTms="true"`; an SCL
  `<TrgOps>` that omits `gi` now enables it.
- Setting groups: the server holds the values of every group and
  SelectActiveSG loads them into FC=SG; reading or writing FC=SE outside an
  edit session answers `TemporarilyUnavailable`.
- Control: an `Oper` with an `operTm` is refused with `NotSupported`; a
  second select of a selected object fails; cancelling an idle direct
  control fails; a selection ends on its own `sboTimeout`; the server
  maintains `stSeld`; a refused operate reports the add cause the server
  sends instead of `unknown`.
- Arrays: an attribute or sub-data object declared with `count` is an MMS
  array whose elements a client reads and writes by index; a read handler
  on an array returns a list of the element count.
- Writes: a whole data object can be written; an integer out of range
  answers `ObjectValueInvalid` and a string or octet string of a wrong
  length `TypeInconsistent`; a Quality written with fewer bits reads back
  with 13.
- Read and write handlers are asked for reads and writes above and below
  their own path, and for data set reads; a handler on a data object or
  constructed attribute answers in model order.
- Data sets: CreateDataSet takes data objects, constructed attributes and
  array elements as members, up to 1024 members and 4096 attributes, and a
  Write up to 4096 items; beyond, the error is resource
  capability-unavailable instead of access other. GetDataSetDirectory
  lists an array element member as the array with an alternate access.
- GI and integrity reports read a data object or constructed attribute
  written concurrently whole or not at all.
- A GoCB `DatSet` reads as `"<LD>/LLN0$<name>"`.
- Python callbacks of a hosted server, a synchronous control `operate` or
  `wait` included, run on a thread of the server's blocking pool instead of
  a runtime worker: a blocking wait no longer holds up other associations,
  `oper_timeout_ms` ends a synchronous `operate` that runs past it (the
  callback finishes on its thread, its result ignored), successive calls may
  run on different threads, and callbacks of different associations may
  interleave.
- Report and log triggers are delivered in the order the values were
  stored; a value overwritten within one client write request is not
  delivered on its own.
- A GOOSE control block registered from the model takes `DstAddress`,
  `MinTime` and `MaxTime` from its SCL `<GSE>`. When the SCL holds a
  connected access point of the IED, one without a `<GSE>` (or without a
  `MAC-Address`) is disabled, reads a destination of six zero octets, and
  cannot be enabled; without a `<Communication>` section it keeps the
  default destination and can be enabled.
- The SCL `<Communication>` section is read into the model: a malformed
  `<P>` value, or a connected access point of the loaded IED naming what the
  IED does not declare, raises `SclError`; a `<ConnectedAP>` of an IED the
  file does not declare is skipped with a warning. `Scl.summary()` lists the
  connected access points and GSE / SMV addresses.
- A client refused at Initiate raises `IedSessionRefusedError`; one refused
  for its calling identity or credentials raises `IedAuthenticationError`; a
  client refused by TLS gets the alert reason.
- Directory walks keep the server's order without duplicates, and SCL
  errors start with `"SCL error at"`; data object members follow the DOType
  declaration order.

### 0.15.2

Tracks iec61850-rust v0.3.2. No API changes; the entries below are what a
hosted server and a client now do differently.

- `ClientReport.entries`: a member the report does not carry has every
  reason flag cleared. Its value is still the last one received, and an entry
  does not yet say whether the report carried it.
- A buffered report control block enabled over MMS sends its reports to the
  association that enabled it; enabled without an `EntryID` write, it starts
  from the oldest buffered entry. While it is enabled, a write from another
  association fails with `ObjectAccessDenied`. When the association ends the
  block is disabled but keeps buffering, and a client that enables it again
  resumes after the last entry sent.
- Integrity reports run for a report control block enabled over MMS whose
  `TrgOps` includes integrity and whose `IntgPd` is above zero, one every
  `IntgPd` milliseconds from the moment `RptEna` is written true.
- A buffered report control block reserved with a positive `ResvTms` is held
  for the peer address that reserved it, for `ResvTms` seconds after that
  association ends; a write from any other address fails with
  `ObjectAccessDenied`. `ResvTms` written as the 16-bit integer
  `set_rcb_values` sends is accepted, and a value outside 0..=32767, or an
  unsigned report control block value wider than 32 bits, fails with
  `object-value-invalid` instead of being truncated.
- A hosted server clears the unused trailing bits of a bit string it receives
  in a Write or SetDataSetValues, and of one it sends in a Read.

### 0.15.1

Tracks iec61850-rust v0.3.1.

- `IedServer.update_bit_string(path, data, padding)` and
  `IedServer.update_bit_string_from_int(path, value)` — update a BIT STRING
  attribute such as a `Dbpos` or a `Quality` on a running server. The bit
  count has to fit the declared size of the attribute, exactly 2 for a `Dbpos`
  and 1 to 13 for a `Quality`, or the call raises `IedDataAccessError` and the
  value stays. `update_bit_string_from_int` writes an unsigned value as many
  bits wide as the declared size, most significant bit first, so a `Dbpos` of
  2 is stored as `b"\x80"` with padding 6. Both trigger reports by the trigger
  options of the attribute.
- `write_bit_string` and the tagged `bit_string` value, wherever it is
  accepted, clear the unused trailing bits of the last octet before the value
  is sent or stored; they used to pass them through.
- A remote Write, a SetDataSetValues member or an `on_read` return value whose
  bit string does not fit the declared size of the attribute is refused: the
  Write fails with `IedDataAccessError` and leaves the value, and the read
  answers object-value-invalid. Only the type used to be checked, so an empty
  or 13-bit string could replace a two-bit `Dbpos`.
- A typed update of an attribute whose trigger options name `qchg` and not
  `dchg`, such as `q`, triggers a quality change, so a report control block
  enabled for `dchg` alone no longer reports it; every update used to count as
  a data change.

### 0.15.0

Tracks iec61850-rust v0.3.0, whose server limits and checks change what a
client and a hosted server observe; the entries below say how.

- `IedConnection.read_bit_string` and `write_bit_string` — typed access to a
  BIT STRING attribute as its data octets and padding count, most significant
  bit first: a `Dbpos` of on is `(b"\x80", 6)`.
- Tagged values — `on_read` callbacks, `IedConnection.write`,
  `set_data_set_values`, `log_value` and the control `select_with_value`,
  `operate` and `cancel` accept the `{"type": ...}` dictionaries
  `from_model_spec` takes, so a callback can answer a `Dbpos` read with
  `{"type": "bit_string", "padding": 6, "data": b"\x80"}`.
- `IedServer.association_setup_timeout_ms`, `association_idle_timeout_ms`,
  `association_send_timeout_ms`, `max_dynamic_datasets` and
  `max_dynamic_datasets_per_association` — settable before `start()`.
- A hosted server closes a connection that has not completed the MMS Initiate
  within 10 s of being accepted, aborts an association that neither sends nor
  receives a PDU for 300 s (a client that only receives reports stays
  connected), and closes a connection whose peer stops accepting PDUs for
  10 s.
- A hosted server bounds the data sets clients create at run time, 64 in total
  and 16 per association by default; past either limit CreateDataSet fails
  with `IedServiceError`.
- `max_connections` counts a connection from the moment it is accepted rather
  than from a completed Initiate.
- SetDataSetValues applies the checks of a single write to every member: a
  member under a read-only functional constraint, or one the `on_write`
  handler refuses, keeps its value and reports an error while the others are
  written.
- URCB reservations and control selections are released when a connection
  ends on a transport or protocol error as well as on a normal close, and a
  setting-group edit session is released whenever its connection closes. BRCB
  reservations still wait for `ResvTms`, so a client can resume after
  reconnecting.
- Read and Write requests are decoded under the nesting level the association
  negotiated. A reassembled TSDU past 73 192 bytes on the client, or past the
  negotiated PDU size plus 8 192 bytes on the server, closes the connection.
- `from_model_spec` refuses a tagged `bit_string` whose `padding` is outside
  0..=7, or non-zero with empty data. When an `on_read` return value cannot be
  converted, the reason is printed through the standard exception hook before
  the read fails with `TypeInconsistent`.

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
- `IsoConnectionParameters` — the ISO addressing an association offers is now
  settable per connection through `IedConnection.connect()`,
  `connect_tls()` and `Iec61850ClientConfig`. The default selectors follow
  IEC 61850-8-1 (P-SEL `00 00 00 01`); the default AP-titles `1.1.1.999` and
  `1.1.1.999.1` with AE-qualifier 12 on both sides are the values IEDs
  customarily expect. Previous releases sent empty presentation selectors
  and no AP-title.
- `IedSessionRefusedError` — a session REFUSE SPDU now raises a dedicated
  subclass of `IedConnectionError` carrying `reason_code`,
  `transport_disconnect` and `provider_reason`, instead of a generic
  connection failure.
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
