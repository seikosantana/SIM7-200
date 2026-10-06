# SIM7-200

A scoped simulator for Siemens S7-200 SMART ladder logic, driven by MicroWIN SMART's plain-text
AWL (STL) exports. It runs a scan-cycle interpreter over the exported subroutines, exposes memory
as a Modbus TCP slave (so a SCADA can connect to it as if it were the real PLC), and has a web GUI
for stepping scans and watching/forcing I/Q/V bits.

It covers the opcode subset a real project actually used (LD/LDN/A/AN/O/ON/ALD/OLD/LPS/LPP/=/S/R/EU/
TON/CALL/MOVB/MOVW/MOVD/AB=), not the full S7-200 instruction set. See `PROGRESS.md` for design notes.

## Run

```
uv sync
uv run python -m sim7200 gui --port 8080      # or run-gui.bat
uv run python -m sim7200 run <project.sim.json> --scans 10 --dump Q0.0,VW20
```

## Project files

A `*.sim.json` stands in for the missing OB1: which AWL files to load and in which order to scan them.

```json
{
  "plc_id": 1,
  "awl_dir": "..",
  "files": ["awl/plc1/MOTOR.awl", "awl/plc1/GUDANG_ILOCKS.awl"],
  "scan_order": ["GUDANG_ILOCKS"],
  "initial_memory": {"V31.0": 1},
  "modbus_tcp_port": 5020
}
```

`awl_dir` is relative to the project file. Point the GUI at your PLC project with environment variables:

| Variable | Meaning |
|---|---|
| `SIM7200_PROJECTS_DIR` | Folder with `*.sim.json` files (default `./projects`) |
| `SIM7200_PLC_DIR` | Folder with the memory-map xlsx files used for GUI labels; tests also read AWL fixtures from here and are skipped when it isn't set |
