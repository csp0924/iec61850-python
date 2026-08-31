# Cookbook

Self-contained recipes for every service the `iec61850` Python binding
exposes. Each script is small enough to read end-to-end and is structured
so the interesting bits stand out.

## Running

Run them from this directory, which is what every module docstring assumes:

```bash
pip install iec61850
cd examples
python 03_host_server_from_scl.py        # one terminal
python 01_typed_io.py 127.0.0.1:PORT     # another terminal
```

Scripts that host their own server bind to a free port and print it on
startup — most of them also run the client-side flow inline, so a single
`python …py` is enough to see both sides. `01` and `02` are pure clients:
point them at the address `03` prints, or at any other IEC 61850 server.

The TLS example (`12`) needs `cryptography`; the SNTP example (`13`) needs
an SNTP server it can reach. Everything else runs on the default install
over loopback.

## Shared building blocks

| Path | Role |
|---|---|
| `_shared.py` | `DEMO` — frozen `DemoPaths` dataclass with every reference. `spawn_demo()` — async context manager that loads the CID, wires the control blocks it declares, binds, and yields a running `IedServer`. |
| `../models/demo.cid` | The demonstration IED: measurands, four status points, one controllable point, two data sets, an unbuffered and a buffered report control block, a GOOSE control block. |
| `../models/README.md` | Field map for `demo.cid`. |

## Index

| File | Demonstrates |
|---|---|
| `01_typed_io.py` | typed reads, quality, timestamp, `asyncio.gather` for parallel reads |
| `02_browse_data_model.py` | `get_server_directory` → LD → LN → DO walk, `AcsiClass` directory queries |
| `03_host_server_from_scl.py` | smallest hosting recipe; `async with spawn_demo()` |
| `04_host_server_programmatic.py` | `IedServer.from_model_spec(dict)`, composable spec builders, `on_write` plus a client write round-trip |
| `05_control_direct_operate.py` | direct-normal control, `OriginValue`, server-side handler updates cache |
| `06_control_select_operate.py` | SBO with togglable interlock and cancel; `Interlock` dataclass owns the flag |
| `07_reporting_unbuffered.py` | `urcbMeas`, `asyncio.Queue` for callback delivery, background pump task |
| `08_reporting_buffered.py` | `brcbMeas` + `Iec61850Client(report_dispatcher_interval_ms=…)` |
| `09_dataset_dynamic.py` | data set CRUD wrapped in `@asynccontextmanager` for lifetime safety |
| `10_setting_groups.py` | SGCB ActSG / EditSG / CnfEdit; `Auditor` dataclass records every transition |
| `11_journal_log.py` | LCB writes + `query_journal_by_time`, ring-buffer eviction |
| `12_tls_secured_session.py` | mutual TLS with throw-away self-signed identities; `Identity` dataclass |
| `13_sntp_time_sync.py` | `query_sntp`, typed `SntpResponse`, formatted readout |

Two recipes build their own model rather than loading `demo.cid`, because
the CID does not declare what they need: `04` needs a data attribute under a
functional constraint a client may write (`SP`), and `10` needs a
`SettingControl`. Both say so in their module docstring.

## House style

- **Async-first.** Every entrypoint is `asyncio.run(main(...))`.
- **Type hints everywhere**, including helpers, so dataclasses and IDE
  completion work without inference gymnastics.
- **Frozen dataclasses for fixtures** (`DemoPaths`, `Identity`, `Interlock`,
  `Auditor`) — no global module constants for things that belong together.
- **Server-side paths use `<LD>/<LN>.<DO>.<DA>`**, client-side prepend the
  IED name (`<IED><LD>/...`). Build them with `DEMO.*` rather than f-strings.
- **`async with` for resource lifetime** — server, data set, client. No
  manual start/stop pairs.
- **No noisy framing.** Docstrings explain *why* a recipe matters and what
  it prints; the code does the rest.
