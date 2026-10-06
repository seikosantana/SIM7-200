"""Real Modbus TCP round trip against a live MemoryImage -- proves this server is a
usable drop-in target for BprDryerSCADA's connection config, not just a unit-level
formula check."""
import asyncio

from pymodbus.client import AsyncModbusTcpClient

from sim7200.memory import Address, MemoryImage
from sim7200.modbus_server import Sim7200ModbusServer


def test_modbus_server_round_trips_against_the_live_memory_image():
    async def scenario():
        mem = MemoryImage()
        mem.write_bit(Address("I", "bit", 0, 0), True)  # discrete input register 0
        mem.write_bit(Address("V", "bit", 1, 0), True)  # HREG 0, bit-flag formula: V(0*2+1).0 = V1.0
        mem.write_word(Address("V", "word", 10), 42)  # HREG 5, word formula: VW(5*2) = VW10

        server = Sim7200ModbusServer(mem, host="127.0.0.1", port=15020, word_registers=frozenset({5}))
        await server.start()
        await asyncio.sleep(0.2)  # give the listener a moment to bind

        client = AsyncModbusTcpClient("127.0.0.1", port=15020)
        try:
            await client.connect()
            di = await client.read_discrete_inputs(0, count=1)
            assert di.bits[0] is True

            hr0 = await client.read_holding_registers(0, count=1)
            assert hr0.registers[0] == 1  # bit-flag register reads back as 0/1

            hr5 = await client.read_holding_registers(5, count=1)
            assert hr5.registers[0] == 42  # word register

            await client.write_coil(0, True)
            assert mem.read_bit(Address("Q", "bit", 0, 0)) is True  # write landed on the live memory

            await client.write_register(0, 0)  # clear the bit-flag HREG via Modbus
            assert mem.read_bit(Address("V", "bit", 1, 0)) is False
        finally:
            client.close()
            await server.stop()

    asyncio.run(scenario())
