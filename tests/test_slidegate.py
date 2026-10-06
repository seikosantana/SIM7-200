from sim7200.awl_parser import parse_file
from sim7200.interpreter import Interpreter
from sim7200.memory import Address, MemoryImage
from conftest import PLC_DIR

# cmdOpen, cmdClose, iOpen, iClose, qRunOpen, qRunClose, flgFail
ARGS = ["V200.0", "V200.1", "V200.2", "V200.3", "Q1.0", "Q1.1", "V200.4"]


def _mem_interp():
    sub = parse_file(PLC_DIR / "awl/plc1/SLIDEGATE.awl")
    mem = MemoryImage()
    return mem, Interpreter(mem=mem, subs={15: sub})


def test_open_command_self_clears_once_open_position_is_reached():
    mem, interp = _mem_interp()
    mem.write_bit(Address("V", "bit", 200, 0), True)  # cmdOpen
    interp.call(15, ARGS)
    assert mem.read_bit(Address("Q", "bit", 1, 0)) is True  # qRunOpen
    assert mem.read_bit(Address("V", "bit", 200, 0)) is True  # cmdOpen still latched

    mem.write_bit(Address("V", "bit", 200, 2), True)  # iOpen: gate reached open position
    interp.call(15, ARGS)
    assert mem.read_bit(Address("V", "bit", 200, 0)) is False  # cmdOpen self-cleared (Network 5)
    assert mem.read_bit(Address("Q", "bit", 1, 0)) is True  # same scan: Network 1 ran before the reset

    interp.call(15, ARGS)  # next scan: cmdOpen already false
    assert mem.read_bit(Address("Q", "bit", 1, 0)) is False


def test_fail_flag_resets_both_open_and_close_commands():
    mem, interp = _mem_interp()
    mem.write_bit(Address("V", "bit", 200, 0), True)  # cmdOpen
    mem.write_bit(Address("V", "bit", 200, 1), True)  # cmdClose
    mem.write_bit(Address("V", "bit", 200, 4), True)  # flgFail
    interp.call(15, ARGS)
    assert mem.read_bit(Address("V", "bit", 200, 0)) is False
    assert mem.read_bit(Address("V", "bit", 200, 1)) is False
