from pathlib import Path

from sim7200.memory import Address
from sim7200.project import build_interpreter, load_project, scan_order_sbr_nums

from conftest import PROJECTS_DIR


def test_buffer_full_stopping_cascade_reaches_the_real_motor_output():
    """Wires buffer-silo-ilocks.awl + buffer-silo-motors.awl + motor.awl + slidegate.awl
    together via a project file and drives the real I17.4 -> V3083.0 -> T109 -> V1083.0
    stopping cascade documented in buffer-silo-ilocks.awl's own network comments, all
    the way through to the actual motor output (Q12.1)."""
    proj, subs_by_num, subs_by_name = load_project(PROJECTS_DIR / "plc1-buffer-silo-test.sim.json")
    interp = build_interpreter(proj, subs_by_num)
    order = scan_order_sbr_nums(proj, subs_by_name)
    mem = interp.mem

    assert mem.read_bit(Address("V", "bit", 1083, 0)) is True  # cmdRun117 starts commanded on

    for _ in range(2):
        interp.run_scan(order)
    # VW22 (PT) = 3 scans; timer hasn't elapsed yet after 2.
    assert mem.read_bit(Address("V", "bit", 3083, 0)) is True  # stopping-mark is live (V7.0 AND I17.4)
    assert mem.read_bit(Address("V", "bit", 1083, 0)) is True  # cmdRun still on
    assert mem.read_bit(Address("Q", "bit", 12, 1)) is True  # qRun117 following cmdRun

    interp.run_scan(order)  # 3rd scan: T109 elapses
    assert mem.read_bit(Address("V", "bit", 1083, 0)) is False  # cmdRun117 reset by the interlock
    # Same scan: ilocks (SBR36) ran before motors (SBR27) in scan_order, so the motor
    # output already reflects the reset -- this is the real bug class the whole project
    # exists to catch: an ordering-dependent effect that's invisible without a scan-accurate sim.
    assert mem.read_bit(Address("Q", "bit", 12, 1)) is False
