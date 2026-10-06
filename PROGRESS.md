# SIM7-200 — status

Scoped S7-200 SMART AWL (STL) simulator, originally built to test a two-PLC dryer plant's
ladder logic. Origin plan (all 4 phases, full rationale/research):
`C:\Users\seikosantana\.claude\plans\declarative-mixing-grove.md` (on the machine this
was built on, not in this repo — this file is the self-contained version).

**Status: Phase 1 + Phase 2 + Phase 3 (GUI) complete and tested. Phase 4
(diagnostics) not started.** 25/25 tests passing (`uv run pytest tests/ -q`).

## What exists

```
sim7200/
  memory.py          MemoryImage: I/Q/V/M/SM as bytearrays with overlap-correct
                      bit/byte/word/dword views (VWn = VBn:VBn+1, etc — this IS the
                      storage model, not a synthetic register set). L memory is a
                      stack of 64-byte call frames.
  awl_parser.py       Line tokenizer -> Subroutine(sbr_num, name, params, networks).
                      Hand-written on purpose — STL here is one flat instruction per
                      line, no nested expressions, so a grammar library (lark etc.)
                      would solve a problem this format doesn't have.
  interpreter.py      Scan-cycle logic-stack VM. Covers the exact opcode set the real
                      corpus uses: LD LDN A AN O ON ALD OLD LPS LPP = S R EU TON CALL
                      MOVB MOVW MOVD AB=. CALL implements S7-200's copy-in/copy-out
                      VAR_IN_OUT semantics (actuals copied into a fresh L frame, and
                      the possibly-modified values copied BACK to the actual addresses
                      on return) — this is not optional bookkeeping, see "non-obvious
                      things" below.
  project.py          Loads a project JSON (the "missing OB1" — see below), resolves
                      CALL SBRn targets across every loaded file, builds an Interpreter.
  cli.py / __main__.py  `python -m sim7200 run <project.json> --scans N --dump A,B`
  library.py          MBUS_CTRL/MBUS_MSG stand-ins for T_MONITOR (PLC2's Modbus RTU
                      master routine) — reads come from a project-configured
                      `fake_slaves` table (`"{slave}@{addr}": value`) instead of
                      polling real hardware. Reproduces Siemens' "error 6" (two
                      enabled MBUS_MSG in one scan) so a regression gets caught here,
                      not on a bench.
  modbus_server.py    Modbus TCP server (pymodbus) bound directly to a MemoryImage —
                      no second copy to keep in sync. Discrete Inputs/Coils use
                      byte*8+bit (I/Q); Holding Registers use V(reg*2+1).0 for
                      bit-flag registers or VW(reg*2) for word registers (which
                      registers are which is passed in as a `word_registers` set —
                      the server doesn't hardcode PLC1-specific business knowledge).
  labels.py           Read-only openpyxl loader over Memory Map.xlsx/Memory Map
                      PLC2.xlsx: scans every sheet with a "No." header row generically
                      (no hardcoded sheet list) for Tag/Description, keyed by
                      normalized address string (I/Q/V bit/word) or timer number.
                      Independent of BprDryerSCADA's own catalog/export pipeline.
  gui.py              NiceGUI app, single asyncio process: SimState holds the loaded
                      Project/Interpreter/ModbusServer and drives them from a
                      repeating `_scan_loop` task registered via `app.on_startup` —
                      no threads, matching NiceGUI's own guidance. Project picker,
                      run/step/tick-rate controls, labeled I toggle panel + read-only
                      Q panel, editable fake_slaves table (edits apply live, no
                      library reinstall needed — library.py reads the dict fresh
                      each call), and a filterable V/M/T table (labeled V entries +
                      full M byte range + labeled timers) with a "Δ" prefix marking
                      addresses that changed on the last scan.

projects/
  plc1-full.sim.json              All 30 real PLC1 AWL files loaded, all 27 top-level
                                   subroutines in a plausible (not authoritative)
                                   scan_order. Used by the corpus smoke test.
  plc1-buffer-silo-test.sim.json  Focused 4-file project (motor+slidegate+
                                   buffer-silo-motors+buffer-silo-ilocks) for the
                                   cascade test.
  plc2-t-monitor-test.sim.json    T_MONITOR.awl alone, with a fake_slaves table.

tests/   24 tests, all against the REAL AWL files in ../PLC (via tests/conftest.py's
         PLC_DIR), not synthetic fixtures — except test_error6_detection.py, which is
         a deliberately-broken fixture by design (see below).
```

## Non-obvious things worth knowing before touching this code

- **VAR_IN_OUT copy-back is load-bearing, not cosmetic.** `slidegate.awl`'s own
  Networks 3-5 `R`eset `L0.0`/`L0.1` (cmdOpen/cmdClose) to enforce open/close mutual
  exclusion. Without copying the final L-frame values back to the actual call-site
  addresses on return, that reset only ever touches the throwaway local frame and the
  real `cmdOpen`/`cmdClose` addresses never see it. `interpreter.py`'s `_call()` does
  this; don't remove it without re-reading `test_slidegate.py`.
- **`PLC1.smart`/`PLC2.smart` are opaque zlib-compressed binaries** (verified by hex
  dump), not parseable — OB1 (the real main/organization block) exists nowhere as AWL
  text. `project.py`'s `scan_order` list stands in for it. `plc1-full.sim.json`'s
  order (interlocks → trip/fail checks → motors → acks) is a plausible reconstruction,
  **not a recovered fact** — don't treat it as authoritative if it ever matters which
  order things actually run in on the real PLC.
- **pymodbus is pinned to `==3.9.2`, not left open.** 3.14 (current latest as of this
  writing) deprecated the classic `ModbusSlaveContext`/`getValues`/`setValues`
  datastore API in favor of a config-driven `SimData`/`SimDevice` simulator whose
  `ModbusServerContext.async_getValues`/`async_setValues` literally return
  `ExcCodes.DEVICE_BUSY` for anything that isn't a `ModbusSimulatorContext` — i.e. the
  old datastore path doesn't actually service requests in 3.14. 3.9.2 is the last
  known-stable line with the live-callback datastore this server needs. Don't bump
  this dependency without re-verifying against pymodbus's current docs.
- **`ms_per_scan` must be a multiple of 100** — only the 100ms-resolution timer class
  (T37-T63, T101-T255) is implemented, matching every `TON` in this project's corpus
  (see `PLC/CLAUDE.md`'s timer resource map). It's also a legitimate way to speed up
  slow-timer tests (e.g. `plc2-t-monitor-test.sim.json` uses `ms_per_scan: 1000` so a
  30-tick PT elapses in 3 scans instead of 30).
- **A real bug was found running `t-monitor.awl`, not planted.** Its Network 9 ("leave
  poll state, enter wait state") runs *before* Network 10 (the T55 turnaround-delay
  timer) in the file. Because Network 9 re-sets the wait bit the instant a poll
  completes — same scan, before Network 10 ever reads it — T55's EN input is never
  actually observed false after the first wait period, so the timer never resets. The
  documented "3.0s turnaround between requests" (`PLC/awl-exports-plc2/t-monitor-README.md`)
  holds for the first cycle only; every later step transition then completes in a
  single scan, forever. Data ends up correct, only the pacing silently collapses.
  See `tests/test_t_monitor.py`'s module docstring. **This hasn't been reported/fixed
  upstream yet** — flag it to whoever owns `t-monitor.awl` before assuming it's fine.
- **`SLIDEGATE`/`CONVEYOR2WAY` exist in both `awl-original/` and `awl-exports/`**
  with identical `SBR` numbers — `plc1-full.sim.json` only loads the `awl-exports/`
  copies (loading both raises project.py's duplicate-SBR error, which is intentional —
  a real MicroWIN import would reject it too).

## How to run

```
cd SIM7-200
uv sync                                            # first time / after pulling
uv run pytest tests/ -q                            # 25 tests, all green
uv run python -m sim7200 run projects/plc1-full.sim.json --scans 50 --dump "V1083.0,Q12.1"
uv run python -m sim7200 gui --port 8080           # web GUI; pick a project in the browser
```

## Non-obvious things about the GUI (Phase 3) worth knowing before touching it

- **The scan loop is one `app.on_startup` task, not a `ui.timer`.** `_scan_loop()` runs
  forever, checking `state.running`/`state.tick_seconds` each iteration — pause and
  tick-rate changes take effect on the next loop iteration without recreating a timer.
  UI refresh is a *separate* `ui.timer(0.5, refresh)` per page, decoupled from the scan
  rate on purpose (a fast scan rate shouldn't force a fast re-render).
- **`SimState` is module-level/shared, not per-browser-tab.** Every connected client
  renders the same simulation; there's no per-session isolation. Fine for a local dev
  tool with one user, would need revisiting for concurrent multi-user use.
- **The I/Q toggle panels only show *labeled* bits**, not the full generic 32-byte
  `AREA_SIZES["I"/"Q"]` range (256 possible bits each) — `_labeled_io_addrs()` filters
  to what `labels.py` found in `Memory Map.xlsx`'s `Inputs`/`Outputs` sheets. An address
  used in AWL but missing from the sheet (a real inconsistency Phase 4 could also flag)
  won't appear in the toggle panel.
- **The V/M/T table shows labeled `V` entries (from every sheet with a `V Address`
  column) + the *entire* `M` byte range + labeled `T` timers — not a raw `V` dump.**
  `AREA_SIZES["V"]` is 10000 bytes; showing it unfiltered would be useless. Use the
  filter box (matches address or label substring) to narrow it; anything not in
  `Memory Map.xlsx` won't have a label but `V`-labeled + all-of-`M` already covers
  everything this project's corpus actually touches per the memory-collision notes
  above.
- **Editing a `fake_slaves` value in the GUI takes effect immediately, no reload
  needed** — `library.py`'s `mbus_msg` closure reads `interp`'s installed `fake_slaves`
  dict fresh on every call, and the GUI edits `state.proj.fake_slaves` in place (the
  same dict object), not a copy.
- **`modbus_word_registers` is a new optional project-JSON field** (`list[int]`,
  defaults to `[]`), threaded through to `Sim7200ModbusServer`'s `word_registers` param
  when the GUI starts the Modbus server. None of the three checked-in `projects/*.json`
  set it yet — add it to a project's JSON if you need `Config`/`Global Config`/`Data`
  sheet `VW` registers to read back as words over Modbus instead of the bit-flag
  default.
- Verified live in a real browser (Playwright): project load, labeled `I17.4` toggle
  correctly reflecting `initial_memory`, Step advancing the scan counter, the V/M/T
  table filtering correctly, and the PLC2 project's `fake_slaves` editor + Modbus TCP
  bind on port 5020 all worked with no console/server errors.

## Remaining work (not started)

### Phase 4 — Diagnostics
- `sim7200/diagnostics.py`: static pass over every loaded `SUBROUTINE_BLOCK` recording
  each write's full byte range (expanding `VW`/`VD`/`S ..,count` writes), flagging any
  two writes from *different* subroutines whose ranges intersect — this is the
  generic version of the exact "global M-memory collision" risk class
  `t-monitor-README.md`'s own post-mortem called out (it was fixed there by moving
  bits off `M` onto centrally-tracked `V` addresses; this diagnostic would catch a
  regression of that class automatically).
- Cross-reference touched `V`/`I`/`Q` addresses against `Memory Map.xlsx` rows to flag
  gaps (address touched at runtime with no matching row = typo or undocumented use).
