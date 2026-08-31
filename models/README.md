# Shared SCL fixture

## `demo.cid`

The demonstration IED every server-hosting example loads, copied verbatim
from the Rust repository so both trees describe the same device. It is a
configured IED description (CID): the `Communication` section binds the
single access point to `127.0.0.1:102`, so a client needs no further
engineering step.

IED `DemoIED`, logical device `LD0` — MMS domain name `DemoIEDLD0`.

| Point | Type | Functional constraint |
|---|---|---|
| `LLN0.Mod.stVal`, `LLN0.Beh.stVal` | Enum | ST — mode and behavior (status only) |
| `LLN0.Health.stVal` | Enum | ST — health indication |
| `LLN0.NamPlt.vendor`, `.swRev`, `.d`, `.configRev` | VisString255 | DC — logical node name plate |
| `LPHD1.PhyNam.*` | VisString255 | DC — physical device name plate |
| `LPHD1.PhyHealth.stVal`, `LPHD1.Proxy.stVal` | Enum / Boolean | ST |
| `MMXU1.TotW.mag.f` | Float32 | MX — total active power |
| `MMXU1.Hz.mag.f` | Float32 | MX — frequency |
| `MMXU1.PhV.phsA/B/C.cVal.mag.f` | Float32 | MX — phase-to-ground voltages |
| `GGIO1.Ind1.stVal` … `Ind4.stVal` | Boolean | ST — single point status |
| `GGIO1.SPCSO1` | SPC | controllable; carries SBO, SBOw, Oper and Cancel, so every control model is reachable. `ctlModel` is `direct-with-normal-security` in the CID and is chosen again at runtime by `IedServer.on_control` |

Control blocks declared in the CID: data sets `dsMeas` (the three
measurands) and `dsStatus` (the four indications), unbuffered report control
block `urcbMeas`, buffered report control block `brcbMeas`, GOOSE control
block `gcbStatus`.

Parsing the CID populates the model. Binding a control block to a live data
set is a separate runtime step — `examples/_shared.py::wire_control_blocks`
shows it for `dsMeas` / `dsStatus` / `urcbMeas` / `brcbMeas`.

## Reference shape

Server-side paths (what `IedServer.update_*`, `on_read`, `on_write` accept):

```
<LD>/<LN>.<DO>.<DA>     e.g.  LD0/GGIO1.Ind1.stVal
```

Client-side paths (what `IedConnection.read_*`, `write_*` accept):

```
<IED><LD>/<LN>.<DO>.<DA>   e.g.  DemoIEDLD0/GGIO1.Ind1.stVal
```

Control block references stitch the LN with a `$<type>$<name>` suffix:

```
DemoIEDLD0/LLN0$RP$urcbMeas    # unbuffered report
DemoIEDLD0/LLN0$BR$brcbMeas    # buffered report
DemoIEDLD0/LLN0$LG$lcb01       # log control
```

`DemoPaths` in `examples/_shared.py` keeps all of these as named attributes
so you never have to assemble the strings by hand.
