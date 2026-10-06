"""Scan-cycle logic-stack VM for the AWL instruction subset this project uses.

Mirrors the real S7-200 STL execution model: each Network starts with a fresh
logic stack; LD/LDN push, A/AN/O/ON combine into the top without pushing,
ALD/OLD pop two and combine into one, LPS duplicates the top (so it can be
reused by a second branch), LPP pops it back off. Box instructions (S, R, =,
TON, CALL, MOVx) read -- and for S/R/MOVx, are gated by -- the current stack
top, matching how a rung's power flow gates a coil/box in ladder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .awl_parser import Instruction, Subroutine
from .memory import Address, MemoryImage, TimerState, parse_address

# ponytail: only the 100ms-resolution timer class (T37-T63, T101-T255) is
# supported, matching every TON usage in this project's corpus (see
# PLC/CLAUDE.md's timer resource map) -- add 1ms/10ms handling if a future
# AWL file ever uses T0-T36/T64-T100.
DEFAULT_MS_PER_SCAN = 100


class UnresolvedCallError(RuntimeError):
    pass


class InterpreterError(RuntimeError):
    pass


LibraryFn = Callable[["Interpreter", list[str]], None]


@dataclass
class Interpreter:
    mem: MemoryImage
    subs: dict[int, Subroutine]
    ms_per_scan: int = DEFAULT_MS_PER_SCAN
    library: dict[int, LibraryFn] = field(default_factory=dict)
    edge_prev: dict[str, bool] = field(default_factory=dict)
    scan_count: int = 0
    # library CALLs (MBUS_CTRL/MBUS_MSG, Phase 2) with EN=1 this scan, by sbr_num
    # -- used to detect the double-enable ("error 6") bug class.
    enabled_library_calls: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ms_per_scan % 100 != 0:
            raise ValueError("ms_per_scan must be a multiple of 100 (100ms-resolution timers only)")
        self.ticks_per_scan = self.ms_per_scan // 100

    def run_scan(self, top_level_sbr_nums: list[int]) -> None:
        self.scan_count += 1
        self.mem.write_bit(Address("SM", "bit", 0, 0), True)  # SM0.0: always on
        self.mem.write_bit(Address("SM", "bit", 0, 1), self.scan_count == 1)  # SM0.1: first scan
        half_period_scans = max(1, round(500 / self.ms_per_scan))
        clock_on = (self.scan_count // half_period_scans) % 2 == 0
        self.mem.write_bit(Address("SM", "bit", 0, 5), clock_on)  # SM0.5: 1Hz clock
        self.enabled_library_calls = {}
        for sbr in top_level_sbr_nums:
            self.call(sbr, [])

    def call(self, sbr_num: int, actual_args: list[str] | None = None) -> None:
        """Public entry point to invoke a subroutine directly (unconditionally enabled) --
        used for scan-cycle top-level calls, and for standalone leaf-subroutine tests."""
        self._call(sbr_num, actual_args or [], en=True)

    def _call(self, sbr_num: int, actual_args: list[str], en: bool) -> None:
        if not en:
            return
        if sbr_num in self.library:
            self.enabled_library_calls[sbr_num] = self.enabled_library_calls.get(sbr_num, 0) + 1
            self.library[sbr_num](self, actual_args)
            return
        sub = self.subs.get(sbr_num)
        if sub is None:
            raise UnresolvedCallError(
                f"CALL to SBR{sbr_num} has no matching loaded subroutine or registered library stub"
            )
        # S7-200 VAR_IN_OUT parameters are copy-in/copy-out: actuals are copied into a
        # fresh L frame, the subroutine runs against the local copies, and the (possibly
        # modified) values are copied back to the actual addresses on return. This is not
        # optional bookkeeping -- e.g. slidegate.awl's own Networks 3-5 RESET L0.0/L0.1
        # (cmdOpen/cmdClose) to enforce open/close mutual exclusion; without copy-back that
        # reset would only ever touch the throwaway local frame and never reach the real
        # cmdOpen/cmdClose addresses the rest of the program reads.
        self.mem.push_frame()
        final_bits: list[bool] = []
        try:
            for idx, arg in enumerate(actual_args):
                dest = Address("L", "bit", idx // 8, idx % 8)
                self.mem.write_bit(dest, bool(self.mem.resolve_operand(arg)))
            self._run_networks(sub)
            for idx in range(len(actual_args)):
                final_bits.append(self.mem.read_bit(Address("L", "bit", idx // 8, idx % 8)))
        finally:
            self.mem.pop_frame()
        for arg, value in zip(actual_args, final_bits):
            actual_addr = parse_address(arg)
            if actual_addr is not None:
                self.mem.write(actual_addr, int(value))

    def _run_networks(self, sub: Subroutine) -> None:
        for net in sub.networks:
            stack: list[bool] = []
            for ins in net.instructions:
                try:
                    self._exec(ins, stack)
                except IndexError as exc:
                    raise InterpreterError(f"stack underflow executing {ins.op} at {ins.key}") from exc

    def _read_bool(self, token: str) -> bool:
        addr = parse_address(token)
        if addr is None:
            raise InterpreterError(f"expected a boolean memory operand, got literal {token!r}")
        return bool(self.mem.read(addr))

    def _exec(self, ins: Instruction, stack: list[bool]) -> None:
        op, args = ins.op, ins.args
        if op == "LD":
            stack.append(self._read_bool(args[0]))
        elif op == "LDN":
            stack.append(not self._read_bool(args[0]))
        elif op == "A":
            stack[-1] = stack[-1] and self._read_bool(args[0])
        elif op == "AN":
            stack[-1] = stack[-1] and not self._read_bool(args[0])
        elif op == "O":
            stack[-1] = stack[-1] or self._read_bool(args[0])
        elif op == "ON":
            stack[-1] = stack[-1] or not self._read_bool(args[0])
        elif op == "ALD":
            b, a = stack.pop(), stack.pop()
            stack.append(a and b)
        elif op == "OLD":
            b, a = stack.pop(), stack.pop()
            stack.append(a or b)
        elif op == "LPS":
            stack.append(stack[-1])
        elif op == "LPP":
            stack.pop()
        elif op == "=":
            self.mem.write_bit(parse_address(args[0]), stack[-1])
        elif op == "S":
            if stack[-1]:
                self.mem.set_bit_seq(parse_address(args[0]), int(args[1]), True)
        elif op == "R":
            if stack[-1]:
                self.mem.set_bit_seq(parse_address(args[0]), int(args[1]), False)
        elif op == "EU":
            cur = stack[-1]
            stack[-1] = cur and not self.edge_prev.get(ins.key, False)
            self.edge_prev[ins.key] = cur
        elif op == "TON":
            self._exec_ton(args, stack[-1])
        elif op == "MOVB":
            if stack[-1]:
                self.mem.write_byte(parse_address(args[1]), self.mem.resolve_operand(args[0]))
        elif op == "MOVW":
            if stack[-1]:
                self.mem.write_word(parse_address(args[1]), self.mem.resolve_operand(args[0]))
        elif op == "MOVD":
            if stack[-1]:
                self.mem.write_dword(parse_address(args[1]), self.mem.resolve_operand(args[0]))
        elif op == "AB=":
            byte_val = self.mem.read_byte(parse_address(args[0]))
            stack[-1] = stack[-1] and (byte_val == self.mem.resolve_operand(args[1]))
        elif op == "CALL":
            sbr_num = int(args[0][3:])  # "SBR27" -> 27
            self._call(sbr_num, args[1:], en=stack[-1])
        else:
            raise InterpreterError(f"unsupported opcode {op!r} at {ins.key}")

    def _exec_ton(self, args: list[str], en: bool) -> None:
        addr = parse_address(args[0])
        pt = self.mem.resolve_operand(args[1])
        timer = self.mem.timers.setdefault(addr.byte, TimerState())
        if en:
            timer.cv = min(32767, timer.cv + self.ticks_per_scan)
            timer.bit = timer.cv >= pt
        else:
            timer.cv = 0
            timer.bit = False
