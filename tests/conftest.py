"""Shared fixtures of this repository's tests. A product that uses the loop has its own conftest.py (the loop's tests
import REAL_REPO from it), so this file is this repository's own (.workflow/loop-files.txt leaves it out)."""

from __future__ import annotations

import datetime as dt
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import time_machine

REAL_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, scope="session")
def own_temporary_folder(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Each test process gets its own system temporary folder: the tests run in parallel (pytest-xdist, `-n auto`)."""
    folder = tmp_path_factory.mktemp("tmp")
    before, tempfile.tempdir = tempfile.tempdir, str(folder)
    yield
    tempfile.tempdir = before


# The instant every test starts at: noon in the local zone, on a weekday, so no test runs near a day's or a week's edge.
# The clock ticks from there, so deadlines and waits still end.
FIXED_NOW = dt.datetime(2026, 11, 4, 12, 0).astimezone()  # a Wednesday


@pytest.fixture(autouse=True)
def fixed_clock(request: pytest.FixtureRequest) -> Iterator[None]:
    """Each test reads FIXED_NOW, ticking, from datetime, date and time (time-machine patches them in this process; never
    time.monotonic, a subprocess or the times the system writes on a file). `@pytest.mark.real_clock` turns it off for a
    test that needs the real clock."""
    if request.node.get_closest_marker("real_clock") or request.node.get_closest_marker("time_machine"):
        yield
        return
    with time_machine.travel(FIXED_NOW, tick=True):
        yield
