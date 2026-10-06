from sim7200.awl_parser import parse_file
from sim7200.interpreter import Interpreter
from sim7200.memory import Address, MemoryImage
from conftest import PLC_DIR

# cmdRun, flgFailing, qRun -- matches motor.awl's VAR_IN_OUT order
ARGS = ["V100.0", "V100.1", "Q0.0"]


def _mem_interp():
    sub = parse_file(PLC_DIR / "awl/plc1/MOTOR.awl")
    mem = MemoryImage()
    return mem, Interpreter(mem=mem, subs={1: sub})


def test_motor_forwards_run_command_to_output():
    mem, interp = _mem_interp()
    mem.write_bit(Address("V", "bit", 100, 0), True)  # cmdRun
    interp.call(1, ARGS)
    assert mem.read_bit(Address("Q", "bit", 0, 0)) is True


def test_motor_resets_cmd_run_on_failure_and_it_propagates_to_the_actual_address():
    mem, interp = _mem_interp()
    mem.write_bit(Address("V", "bit", 100, 0), True)  # cmdRun
    mem.write_bit(Address("V", "bit", 100, 1), True)  # flgFailing
    interp.call(1, ARGS)
    # Network 2 resets L0.0 (cmdRun) -- must copy back to V100.0, not just the local frame.
    assert mem.read_bit(Address("V", "bit", 100, 0)) is False
    # Network 1 (qRun = cmdRun) already ran before the reset, same scan.
    assert mem.read_bit(Address("Q", "bit", 0, 0)) is True

    interp.call(1, ARGS)  # next scan: cmdRun is already false
    assert mem.read_bit(Address("Q", "bit", 0, 0)) is False
