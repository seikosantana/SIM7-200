"""Exercises SimState's own logic (project load, scan stepping, labeling, changed-byte
tracking) against a real project -- no browser/NiceGUI server involved. Uses
plc1-buffer-silo-test.sim.json specifically because it has no modbus_tcp_port, so
loading it doesn't bind a socket."""
import asyncio

import pytest

from conftest import HAVE_PLC
from sim7200.gui import SimState, fuzzy_filter
from sim7200.memory import Address


def test_fuzzy_filter_matches_out_of_order_substrings_and_ranks_best_first():
    items = ["Buffer Silo Hi Level", "Bin 1 Hi Level", "unrelated"]
    result = fuzzy_filter(items, lambda s: s, "bufhi")
    assert result[0] == "Buffer Silo Hi Level"
    assert "unrelated" not in result


def test_fuzzy_filter_empty_query_returns_everything_unfiltered():
    items = ["a", "b", "c"]
    assert fuzzy_filter(items, lambda s: s, "") == items


@pytest.mark.skipif(not HAVE_PLC, reason="SIM7200_PLC_DIR not set to a PLC project")
def test_load_step_and_label_against_a_real_project():
    async def scenario():
        state = SimState()
        await state.load("plc1-buffer-silo-test.sim.json")
        try:
            assert state.interp is not None
            assert state.interp.scan_count == 0

            # I17.4 is labeled in Memory Map.xlsx's Inputs sheet -- confirms labels.py
            # actually found and normalized it to match Address.__str__'s format.
            i17_4 = [a for a, _ in state.i_addrs if str(a) == "I17.4"]
            assert i17_4, "I17.4 should be a labeled input in this project"

            state.step_once()
            assert state.interp.scan_count == 1
            assert state.error is None

            # V1083.0 (documented cascade output) is expected to change over the run.
            rows = state.table_rows("")
            assert any(r["address"].endswith("V1083.0") for r in rows)

            # M is included wholesale as byte rows even without labels.
            assert any(r["address"] == "MB0" for r in rows)

            # filter narrows to fuzzy-matching address/label text, best match first
            filtered = state.table_rows("V1083")
            assert filtered
            assert filtered[0]["address"] == "V1083.0"
        finally:
            await state.unload()
        assert state.interp is None

    asyncio.run(scenario())
