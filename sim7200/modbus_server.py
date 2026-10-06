"""Modbus TCP server bound directly to a MemoryImage.

Reads/writes go straight to the same bytes the interpreter executes against --
no second copy to keep in sync, which would just be its own class of
addressing bug. Uses the exact two formulas `PLC/CLAUDE.md` documents for the
real Modbus slave block, so this server is a drop-in target for BprDryerSCADA's
connection config:

- Discrete Inputs / Coils (I/Q): register = byte*8 + bit
- Holding Registers (V): bit-flag entries at V(reg*2+1).0, numeric/word
  entries at VW(reg*2) -- `word_registers` tells this server which register
  numbers are the numeric kind; everything else defaults to the bit formula.

pymodbus 3.14 deprecated the classic ModbusSlaveContext/getValues/setValues
datastore API in favor of a config-driven SimData/SimDevice simulator that
has no live-callback hook -- not a fit for "wrap my own already-running
memory". Pinned to pymodbus 3.9.2, the last stable line with the classic
callback-style datastore this server relies on.
"""
from __future__ import annotations

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import ModbusTcpServer

from .memory import Address, MemoryImage


class _BitAreaBlock:
    """Discrete Inputs / Coils block: register = byte*8 + bit over a MemoryImage area."""

    def __init__(self, mem: MemoryImage, area: str) -> None:
        self.mem = mem
        self.area = area

    def getValues(self, address: int, count: int = 1) -> list[int]:
        reg0 = address - 1
        return [int(self.mem.read_bit(Address(self.area, "bit", *divmod(reg0 + i, 8)))) for i in range(count)]

    def setValues(self, address: int, values) -> None:
        if not isinstance(values, list):
            values = [values]
        reg0 = address - 1
        for i, v in enumerate(values):
            byte, bit = divmod(reg0 + i, 8)
            self.mem.write_bit(Address(self.area, "bit", byte, bit), bool(v))


class _HoldingRegisterBlock:
    """Holding Registers block: V(reg*2+1).0 for bit-flag regs, VW(reg*2) for word regs."""

    def __init__(self, mem: MemoryImage, word_registers: frozenset[int] = frozenset()) -> None:
        self.mem = mem
        self.word_registers = word_registers

    def _addr_for(self, reg: int) -> Address:
        if reg in self.word_registers:
            return Address("V", "word", reg * 2)
        return Address("V", "bit", reg * 2 + 1, 0)

    def getValues(self, address: int, count: int = 1) -> list[int]:
        reg0 = address - 1
        return [self.mem.read(self._addr_for(reg0 + i)) for i in range(count)]

    def setValues(self, address: int, values) -> None:
        if not isinstance(values, list):
            values = [values]
        reg0 = address - 1
        for i, v in enumerate(values):
            self.mem.write(self._addr_for(reg0 + i), v)


def build_context(mem: MemoryImage, word_registers: frozenset[int] = frozenset()) -> ModbusServerContext:
    slave = ModbusSlaveContext(
        di=_BitAreaBlock(mem, "I"),
        co=_BitAreaBlock(mem, "Q"),
        hr=_HoldingRegisterBlock(mem, word_registers),
        ir=ModbusSequentialDataBlock(0, [0]),
    )
    return ModbusServerContext(slaves=slave, single=True)


class Sim7200ModbusServer:
    """Thin lifecycle wrapper: start()/stop() as an asyncio task, so it can share the
    event loop with the interpreter's scan-cycle task and (Phase 3) the GUI."""

    def __init__(self, mem: MemoryImage, host: str = "0.0.0.0", port: int = 502, word_registers: frozenset[int] = frozenset()) -> None:
        self._server = ModbusTcpServer(build_context(mem, word_registers), address=(host, port))
        self._task = None

    async def start(self) -> None:
        import asyncio

        self._task = asyncio.create_task(self._server.serve_forever())

    async def stop(self) -> None:
        await self._server.shutdown()
        if self._task is not None:
            self._task.cancel()
