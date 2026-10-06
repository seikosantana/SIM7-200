"""Loads a SIM7-200 project file.

`PLC1.smart`/`PLC2.smart` are opaque zlib-compressed MicroWIN SMART binaries --
verified by hex dump, not parseable as text -- so OB1 (the main/organization
block that calls every top-level subroutine each scan) exists nowhere as AWL
text, only the individual `SUBROUTINE_BLOCK` exports do. The project file's
`scan_order` list stands in for that missing OB1: which top-level subroutines
get CALLed every scan, and in what order, is something the user configures
deliberately -- this repo has no record of the real order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .awl_parser import Subroutine, parse_file
from .interpreter import Interpreter
from .memory import MemoryImage, parse_address


@dataclass
class Project:
    plc_id: int
    awl_dir: Path
    files: list[str]
    scan_order: list[str]
    initial_memory: dict[str, int] = field(default_factory=dict)
    fake_slaves: dict[str, int] = field(default_factory=dict)
    modbus_tcp_port: int | None = None
    modbus_word_registers: list[int] = field(default_factory=list)
    ms_per_scan: int = 100


def load_project(path: str | Path) -> tuple[Project, dict[int, Subroutine], dict[str, Subroutine]]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    awl_dir = (path.parent / data["awl_dir"]).resolve()

    subs_by_num: dict[int, Subroutine] = {}
    subs_by_name: dict[str, Subroutine] = {}
    for rel in data["files"]:
        sub = parse_file(awl_dir / rel)
        if sub.sbr_num in subs_by_num:
            other = subs_by_num[sub.sbr_num]
            raise ValueError(
                f"duplicate SBR{sub.sbr_num}: {other.source_file!r} and {sub.source_file!r} "
                "both claim it -- a real MicroWIN import would reject this too"
            )
        subs_by_num[sub.sbr_num] = sub
        subs_by_name[sub.name] = sub

    proj = Project(
        plc_id=data["plc_id"],
        awl_dir=awl_dir,
        files=data["files"],
        scan_order=data["scan_order"],
        initial_memory=data.get("initial_memory", {}),
        fake_slaves=data.get("fake_slaves", {}),
        modbus_tcp_port=data.get("modbus_tcp_port"),
        modbus_word_registers=data.get("modbus_word_registers", []),
        ms_per_scan=data.get("ms_per_scan", 100),
    )
    return proj, subs_by_num, subs_by_name


def scan_order_sbr_nums(proj: Project, subs_by_name: dict[str, Subroutine]) -> list[int]:
    nums = []
    for name in proj.scan_order:
        sub = subs_by_name.get(name)
        if sub is None:
            raise ValueError(f"scan_order references {name!r}, which isn't among the loaded files")
        nums.append(sub.sbr_num)
    return nums


def build_interpreter(proj: Project, subs_by_num: dict[int, Subroutine]) -> Interpreter:
    mem = MemoryImage()
    for token, value in proj.initial_memory.items():
        addr = parse_address(token)
        if addr is None:
            raise ValueError(f"initial_memory key {token!r} is not a valid memory address")
        mem.write(addr, value)
    return Interpreter(mem=mem, subs=subs_by_num, ms_per_scan=proj.ms_per_scan)
