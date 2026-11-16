"""Tests for measuring the laptop's CPU use in the benchmark (node/video/cpu_usage.py).

The counters themselves are read by `core/system_probes.py` (tested in
`tests/core/test_system_probes.py`); here, the reading between two calls.
"""

import sys

from baja_studio.node.video.cpu_usage import SystemCpu


def test_each_reading_is_the_use_since_the_previous_one() -> None:
    cpu = SystemCpu()
    reading = cpu.percent()
    if sys.platform in ("win32", "linux"):
        # Both systems have counters. Right after the first reading the counters
        # may not have moved yet: then there is no use to report (None).
        assert reading is None or 0.0 <= reading <= 100.0
    else:
        assert reading is None
