"""Cheap smoke test that the parser/interpreter cover the *whole* real PLC1 AWL
corpus (30 files, every top-level orchestration subroutine), not just the
hand-picked cascade in test_buffer_silo_ilocks.py. `scan_order` here is a
plausible reconstruction of the missing OB1 (interlocks -> trip/fail checks ->
motors -> acks), not a recovered fact -- see project.py's module docstring."""
from pathlib import Path

from sim7200.project import build_interpreter, load_project, scan_order_sbr_nums

from conftest import PROJECTS_DIR


def test_full_plc1_corpus_runs_without_unresolved_calls_or_unknown_opcodes():
    proj, subs_by_num, subs_by_name = load_project(PROJECTS_DIR / "plc1-full.sim.json")
    assert len(subs_by_num) == 30
    interp = build_interpreter(proj, subs_by_num)
    order = scan_order_sbr_nums(proj, subs_by_name)
    assert len(order) == 27

    for _ in range(50):
        interp.run_scan(order)
