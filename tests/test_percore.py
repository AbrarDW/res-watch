import os
import subprocess
import sys
import time

import pytest

from reswatch import percore
from reswatch.percore import PerCoreTracker, parse_stat

STAT = ("1234 (my (weird) proc) R 1 1234 1234 0 -1 4194304 100 0 0 0 250 50 0 0 20 0 3 0 100 "
        "1000000 200 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 17 5 0 0 0 0 0")


def test_parse_stat_handles_parens_in_name():
    ticks, cpu = parse_stat(STAT)
    assert ticks == 300
    assert cpu == 5


@pytest.mark.skipif(not percore.SUPPORTED, reason="Linux only")
def test_burn_loop_lands_on_one_core_and_sums_to_total():
    burn = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    try:
        t = PerCoreTracker()
        t.sample([burn.pid])
        time.sleep(1.0)
        cores = t.sample([burn.pid])[burn.pid]
        assert len(cores) == (os.cpu_count() or 1)
        total = sum(cores)
        assert 50 < total <= 105, cores
        assert max(cores) > 0.5 * total  # mostly on one core (it may migrate once)
    finally:
        burn.kill()


@pytest.mark.skipif(not percore.SUPPORTED or not hasattr(os, "sched_setaffinity"), reason="needs Linux affinity")
def test_pinned_process_is_attributed_to_its_core():
    core = max(os.sched_getaffinity(0))
    burn = subprocess.Popen([sys.executable, "-c", f"import os; os.sched_setaffinity(0, {{{core}}})\nwhile True: pass"])
    try:
        time.sleep(0.3)
        t = PerCoreTracker()
        t.sample([burn.pid])
        time.sleep(1.0)
        cores = t.sample([burn.pid])[burn.pid]
        assert cores[core] > 60, cores
        assert sum(cores) - cores[core] < 5
    finally:
        burn.kill()
