"""S7-200 SMART memory model.

Each area (I, Q, V, M, SM) is one flat bytearray. VBn/VWn/VDn/Vn.b are not
separate stores -- they are byte/word/dword/bit *views* over the same bytes
(VWn = VBn:VBn+1, VDn = VBn..VBn+3, big-endian). Storing it this way, instead
of as named registers, is what makes cross-instruction overlap (one network
writes MOVW VW200 while another only meant to touch VB200) a real, catchable
effect instead of something the interpreter has to special-case.

L memory is call-scoped: a stack of fresh 64-byte frames, one per active
subroutine invocation (see interpreter.py's CALL handling), never shared
across sibling calls -- this is why motor.awl can reuse L0.0-L0.2 for every
one of ~51 motor instances safely.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

# ponytail: fixed byte areas sized generously for this project's corpus
# (max observed: I21.7, Q13.7, M11.0, V6009); grow if a future AWL exceeds these.
AREA_SIZES = {"I": 32, "Q": 32, "M": 32, "SM": 32, "V": 10000}

_BIT_RE = re.compile(r"^(I|Q|V|M|SM|L)(\d+)\.([0-7])$")
_BYTE_RE = re.compile(r"^(IB|QB|VB|MB|SMB|LB)(\d+)$")
_WORD_RE = re.compile(r"^(IW|QW|VW|MW|SMW|LW)(\d+)$")
_DWORD_RE = re.compile(r"^(ID|QD|VD|MD|SMD|LD)(\d+)$")
_TIMER_RE = re.compile(r"^T(\d+)$")
_INT_RE = re.compile(r"^-?\d+$")

_PREFIX_AREA = {"I": "I", "Q": "Q", "V": "V", "M": "M", "SM": "SM", "L": "L"}


@dataclass(frozen=True)
class Address:
    area: str  # I, Q, V, M, SM, L, T
    kind: str  # bit, byte, word, dword, timer
    byte: int  # byte offset, or timer number when kind == "timer"
    bit: int = 0  # only meaningful when kind == "bit"

    def __str__(self) -> str:
        if self.kind == "timer":
            return f"T{self.byte}"
        if self.kind == "bit":
            return f"{self.area}{self.byte}.{self.bit}"
        suffix = {"byte": "B", "word": "W", "dword": "D"}[self.kind]
        return f"{self.area}{suffix}{self.byte}"

    def byte_span(self) -> tuple[int, int]:
        """Inclusive [start, end] byte range this address touches (bit addresses count their byte)."""
        size = {"bit": 1, "byte": 1, "word": 2, "dword": 4}[self.kind]
        return (self.byte, self.byte + size - 1)


def parse_address(token: str) -> Address | None:
    """Parse an AWL operand into an Address, or None if it's not a memory operand
    (e.g. a literal int, or a subroutine name)."""
    token = token.strip()
    if token.startswith("&"):
        token = token[1:]

    m = _BIT_RE.match(token)
    if m:
        area, byte, bit = m.groups()
        return Address(area, "bit", int(byte), int(bit))

    m = _BYTE_RE.match(token)
    if m:
        return Address(_PREFIX_AREA[m.group(1)[0]] if m.group(1) != "SMB" else "SM", "byte", int(m.group(2)))

    m = _WORD_RE.match(token)
    if m:
        prefix = m.group(1)
        area = "SM" if prefix == "SMW" else prefix[0]
        return Address(area, "word", int(m.group(2)))

    m = _DWORD_RE.match(token)
    if m:
        prefix = m.group(1)
        area = "SM" if prefix == "SMD" else prefix[0]
        return Address(area, "dword", int(m.group(2)))

    m = _TIMER_RE.match(token)
    if m:
        return Address("T", "timer", int(m.group(1)))

    return None


def is_literal(token: str) -> bool:
    return bool(_INT_RE.match(token.strip()))


@dataclass
class TimerState:
    cv: int = 0
    bit: bool = False


class MemoryImage:
    def __init__(self) -> None:
        self.areas: dict[str, bytearray] = {name: bytearray(size) for name, size in AREA_SIZES.items()}
        self.l_stack: list[bytearray] = [bytearray(64)]
        self.timers: dict[int, TimerState] = {}

    # -- L-memory call frames -------------------------------------------------
    def push_frame(self) -> None:
        self.l_stack.append(bytearray(64))

    def pop_frame(self) -> None:
        if len(self.l_stack) > 1:
            self.l_stack.pop()

    def _buf(self, area: str) -> bytearray:
        return self.l_stack[-1] if area == "L" else self.areas[area]

    # -- raw views --------------------------------------------------------------
    def read_bit(self, addr: Address) -> bool:
        buf = self._buf(addr.area)
        return bool(buf[addr.byte] & (1 << addr.bit))

    def write_bit(self, addr: Address, value: bool) -> None:
        buf = self._buf(addr.area)
        if value:
            buf[addr.byte] |= 1 << addr.bit
        else:
            buf[addr.byte] &= ~(1 << addr.bit) & 0xFF

    def read_byte(self, addr: Address) -> int:
        return self._buf(addr.area)[addr.byte]

    def write_byte(self, addr: Address, value: int) -> None:
        self._buf(addr.area)[addr.byte] = value & 0xFF

    def read_word(self, addr: Address) -> int:
        buf = self._buf(addr.area)
        return struct.unpack_from(">H", buf, addr.byte)[0]

    def write_word(self, addr: Address, value: int) -> None:
        buf = self._buf(addr.area)
        struct.pack_into(">H", buf, addr.byte, value & 0xFFFF)

    def read_dword(self, addr: Address) -> int:
        buf = self._buf(addr.area)
        return struct.unpack_from(">I", buf, addr.byte)[0]

    def write_dword(self, addr: Address, value: int) -> None:
        buf = self._buf(addr.area)
        struct.pack_into(">I", buf, addr.byte, value & 0xFFFFFFFF)

    # -- generic dispatch ---------------------------------------------------
    def read(self, addr: Address) -> int:
        if addr.kind == "bit":
            return int(self.read_bit(addr))
        if addr.kind == "byte":
            return self.read_byte(addr)
        if addr.kind == "word":
            return self.read_word(addr)
        if addr.kind == "dword":
            return self.read_dword(addr)
        if addr.kind == "timer":
            return int(self.timers.get(addr.byte, TimerState()).bit)
        raise ValueError(f"unhandled address kind {addr.kind}")

    def write(self, addr: Address, value: int) -> None:
        if addr.kind == "bit":
            self.write_bit(addr, bool(value))
        elif addr.kind == "byte":
            self.write_byte(addr, value)
        elif addr.kind == "word":
            self.write_word(addr, value)
        elif addr.kind == "dword":
            self.write_dword(addr, value)
        else:
            raise ValueError(f"cannot write to address kind {addr.kind}")

    def resolve_operand(self, token: str) -> int:
        """Read either a memory operand or a literal integer -- used for MOVx/AB= sources."""
        if is_literal(token):
            return int(token)
        addr = parse_address(token)
        if addr is None:
            raise ValueError(f"unresolvable operand: {token!r}")
        return self.read(addr)

    def set_bit_seq(self, addr: Address, count: int, value: bool) -> None:
        """S/R <addr>, <count> -- set/reset `count` consecutive bits starting at addr."""
        bit_index = addr.byte * 8 + addr.bit
        for i in range(count):
            b, bit = divmod(bit_index + i, 8)
            self.write_bit(Address(addr.area, "bit", b, bit), value)
