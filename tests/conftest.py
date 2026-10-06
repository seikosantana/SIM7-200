import os
from pathlib import Path

import pytest

# Most tests run against a real PLC project (AWL exports + *.sim.json files), which is not
# part of this repo. Point SIM7200_PLC_DIR at it; tests that need it are skipped otherwise.
PLC_DIR = Path(os.environ.get("SIM7200_PLC_DIR", "__unset__"))
PROJECTS_DIR = PLC_DIR / "sim"
HAVE_PLC = PLC_DIR.is_dir()


def pytest_collection_modifyitems(config, items):
    if HAVE_PLC:
        return
    skip = pytest.mark.skip(reason="SIM7200_PLC_DIR not set to a PLC project")
    for item in items:
        if "PLC_DIR" in item.module.__dict__ or "PROJECTS_DIR" in item.module.__dict__:
            item.add_marker(skip)
