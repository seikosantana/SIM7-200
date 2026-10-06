"""MBUS_CTRL / MBUS_MSG stand-ins.

T_MONITOR:SBR10 (PLC2) is the only place this project's AWL corpus calls the
Modbus RTU master library (`CALL SBR1, ...` = MBUS_CTRL, `CALL SBR2, ...` =
MBUS_MSG). Per the user's ask, SIM7-200 doesn't poll a real RTU bus -- it
looks the requested (Slave, Addr) up in a project-configured `fake_slaves`
table and returns that value, so BprDryerSCADA can be end-to-end tested
against configurable numbers without any real hardware in the loop.

This also reproduces Siemens' documented "error 6" (a second MBUS_MSG enabled
in the same scan while one is already in flight) -- the exact bug class
`t-monitor-README.md` spent 7 design iterations chasing -- so a future edit
that reintroduces it gets caught by SIM7-200 instead of by a bench cycle.
"""
from __future__ import annotations

from .interpreter import Interpreter
from .memory import Address, parse_address

ERROR_MBUS_BUSY = 6  # Siemens' documented "error 6"
ERROR_NO_FAKE_VALUE = 2  # SIM7-200's own stand-in: no fake_slaves entry configured


def mbus_ctrl(interp: Interpreter, args: list[str]) -> None:
    """MBUS_CTRL(Mode, Baud, Parity, Port, Timeout, Done, Error) -- always reports the
    master as initialized; SIM7-200 has no real serial link to actually fail to open."""
    done_addr = parse_address(args[-2])
    error_addr = parse_address(args[-1])
    interp.mem.write_bit(done_addr, True)
    interp.mem.write_byte(error_addr, 0)


def make_mbus_msg(sbr_num: int, fake_slaves: dict[str, int]):
    """MBUS_MSG(First, Slave, RW, Addr, Count, DataPtr, Done, Error).

    `fake_slaves` keys are `f"{slave}@{addr}"` (Addr in the AWL's own Modicon-prefixed
    form, e.g. "5@40001") -> the word value to hand back on a read. Writes always
    report success without actually storing anything (nothing downstream of this
    project's corpus reads a written value back).
    """

    def mbus_msg(interp: Interpreter, args: list[str]) -> None:
        error_addr = parse_address(args[7])
        if interp.enabled_library_calls.get(sbr_num, 0) > 1:
            # A second MBUS_MSG got enabled in the same scan as one still in flight --
            # real hardware rejects this with error 6, so we do too, instead of quietly
            # racing two requests the way a naive stub would.
            interp.mem.write_byte(error_addr, ERROR_MBUS_BUSY)
            return

        mem = interp.mem
        slave = mem.resolve_operand(args[1])
        rw = mem.resolve_operand(args[2])
        addr = mem.resolve_operand(args[3])
        dataptr_addr = parse_address(args[5])
        done_addr = parse_address(args[6])

        if rw == 0:  # read
            key = f"{slave}@{addr}"
            if key not in fake_slaves:
                mem.write_bit(done_addr, True)
                mem.write_byte(error_addr, ERROR_NO_FAKE_VALUE)
                return
            mem.write_word(Address(dataptr_addr.area, "word", dataptr_addr.byte), fake_slaves[key])
        # write: nothing to store, just report success
        mem.write_bit(done_addr, True)
        mem.write_byte(error_addr, 0)

    return mbus_msg


def install_t_monitor_library(interp: Interpreter, fake_slaves: dict[str, int], sbr_ctrl: int = 1, sbr_msg: int = 2) -> None:
    """Registers the MBUS_CTRL/MBUS_MSG stand-ins at the SBR numbers T_MONITOR calls them
    on (SBR1/SBR2) -- safe only when nothing else in the loaded project claims those
    numbers, which holds for every PLC2 project in this repo (T_MONITOR itself is SBR10)."""
    interp.library[sbr_ctrl] = mbus_ctrl
    interp.library[sbr_msg] = make_mbus_msg(sbr_msg, fake_slaves)
