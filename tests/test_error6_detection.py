"""Deliberately re-creates the historical bug `t-monitor-README.md` spent 7 design
iterations chasing: two MBUS_MSG calls both enabled in the same scan. Real S7-200
hardware rejects the second with Siemens' documented "error 6" -- this proves
SIM7-200 would have caught the actual incident this project already lived through,
without needing bench hardware to reproduce it.
"""
from sim7200.awl_parser import parse_text
from sim7200.interpreter import Interpreter
from sim7200.library import ERROR_MBUS_BUSY, make_mbus_msg, mbus_ctrl
from sim7200.memory import Address, MemoryImage

BROKEN_AWL = """\
SUBROUTINE_BLOCK BROKEN_DOUBLE_MBUS:SBR20
TITLE=
BEGIN
Network 1
// First MBUS_MSG, always enabled
LD     SM0.0
CALL   SBR2, SM0.0, 1, 0, 31001, 1, &VB100, V50.0, VB51
Network 2
// Second MBUS_MSG, ALSO always enabled the same scan -- the exact bug this
// project's own T_MONITOR post-mortem describes.
LD     SM0.0
CALL   SBR2, SM0.0, 2, 0, 31001, 1, &VB102, V50.1, VB52
END_SUBROUTINE_BLOCK
"""


def test_second_enabled_mbus_msg_in_the_same_scan_is_rejected_with_error_6():
    sub = parse_text(BROKEN_AWL, "fixture")
    mem = MemoryImage()
    interp = Interpreter(mem=mem, subs={20: sub})
    interp.library[1] = mbus_ctrl
    interp.library[2] = make_mbus_msg(2, {"1@31001": 999})

    interp.run_scan([20])

    assert mem.read_bit(Address("V", "bit", 50, 0)) is True  # first call completed normally
    assert mem.read_byte(Address("V", "byte", 51)) == 0

    assert mem.read_bit(Address("V", "bit", 50, 1)) is False  # second call: rejected, Done never set
    assert mem.read_byte(Address("V", "byte", 52)) == ERROR_MBUS_BUSY


def test_single_enabled_mbus_msg_per_scan_never_triggers_the_busy_error():
    """Sanity check the fixture isn't just always-busy -- t-monitor.awl's own shape
    (one CALL SBR2 site) must stay error-free, matching the real, working design."""
    sub = parse_text(
        BROKEN_AWL.replace(
            "CALL   SBR2, SM0.0, 2, 0, 31001, 1, &VB102, V50.1, VB52",
            "// (removed second call)",
        ),
        "fixture-fixed",
    )
    mem = MemoryImage()
    interp = Interpreter(mem=mem, subs={20: sub})
    interp.library[2] = make_mbus_msg(2, {"1@31001": 999})

    interp.run_scan([20])

    assert mem.read_bit(Address("V", "bit", 50, 0)) is True
    assert mem.read_byte(Address("V", "byte", 51)) == 0
