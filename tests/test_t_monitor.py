"""t-monitor.awl against a configurable fake_slaves table -- no real RTU polling.

Running this through the interpreter surfaced a real finding, not just a test
fixture quirk: `t-monitor.awl`'s own Network 9 ("leave poll state, enter wait
state") runs *before* Network 10 (the T55 turnaround-delay timer) in the file.
Because Network 9 re-sets the wait-state bit (V224.1) the instant a poll
completes -- same scan, before Network 10 ever reads it -- T55's EN input is
never actually observed false after the very first wait period, so the timer
never resets. The documented "3.0s turnaround between requests" (see
`t-monitor-README.md`) holds for the first cycle only; every following
step-to-step transition then completes in a single scan, forever. This is
exactly the class of bug the whole project exists to catch -- data ends up
correct, only the *timing* silently degrades -- and it was found by running
the real file, not written into it on purpose. Report this upstream; these
tests capture what the ladder actually does today, not what its comments say
it does.
"""
from pathlib import Path

from sim7200.library import install_t_monitor_library
from sim7200.memory import Address
from sim7200.project import build_interpreter, load_project, scan_order_sbr_nums

from conftest import PROJECTS_DIR


def _setup():
    proj, subs_by_num, subs_by_name = load_project(PROJECTS_DIR / "plc2-t-monitor-test.sim.json")
    interp = build_interpreter(proj, subs_by_num)
    install_t_monitor_library(interp, proj.fake_slaves)
    order = scan_order_sbr_nums(proj, subs_by_name)
    return interp, order


def test_first_step_publishes_immediately_since_the_stub_completes_the_same_scan():
    interp, order = _setup()
    mem = interp.mem
    interp.run_scan(order)  # step 0's poll + publish (Network 3 -> Network 4) both fire this scan
    assert mem.read_word(Address("V", "word", 6002)) == 235  # datTSlave1PV, from fake_slaves["1@31001"]
    assert mem.read_word(Address("V", "word", 6004)) == 0  # step 1 hasn't been polled yet

    for _ in range(2):
        interp.run_scan(order)  # waiting out T55's turnaround delay (PT=30, 10 ticks/scan)
    assert mem.read_word(Address("V", "word", 6004)) == 0  # still waiting, step hasn't advanced
    interp.run_scan(order)  # 3rd wait scan: T55 elapses, step 0 -> 1, slave 2 polled+published
    assert mem.read_word(Address("V", "word", 6004)) == 240  # datTSlave2PV


def test_all_configured_slave_values_are_eventually_published():
    interp, order = _setup()
    mem = interp.mem
    for _ in range(10):
        interp.run_scan(order)
    assert mem.read_word(Address("V", "word", 6002)) == 235  # datTSlave1PV
    assert mem.read_word(Address("V", "word", 6004)) == 240  # datTSlave2PV
    assert mem.read_word(Address("V", "word", 6006)) == 238  # datTSlave3PV
    assert mem.read_word(Address("V", "word", 6008)) == 231  # datTSlave4PV
    assert mem.read_word(Address("V", "word", 6000)) == 250  # datTFurnaceSV


def test_unconfigured_slave_reports_no_response_instead_of_hanging():
    """A read the fake_slaves table doesn't cover must fail loudly (Error set), not
    silently return 0 or block -- otherwise a misconfigured test fixture looks like a
    working one."""
    proj, subs_by_num, subs_by_name = load_project(PROJECTS_DIR / "plc2-t-monitor-test.sim.json")
    proj.fake_slaves = {}  # nothing configured
    interp = build_interpreter(proj, subs_by_num)
    install_t_monitor_library(interp, proj.fake_slaves)
    order = scan_order_sbr_nums(proj, subs_by_name)
    interp.run_scan(order)  # first scan: step 0 poll fires immediately
    assert interp.mem.read_byte(Address("V", "byte", 228)) == 2  # ERROR_NO_FAKE_VALUE
