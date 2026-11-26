"""R-1282: one same-run load calibration for every test that holds a real wall deadline.

A real deadline (a product timeout, a lock wait, a fail-fast budget) cannot become CPU time, so its bound is scaled by how
slow this machine is right now: time a trivial python spawn-and-reap, compare with the idle figure. The outcome checks of a
test are never relaxed, only the wall budget is multiplied."""
import subprocess
import sys
import time

IDLE_SPAWN = 0.02       # seconds (measured 0.02 to 0.025 here, idle and under 8 busy loops) a trivial python spawn-and-reap takes on an idle machine


def load_factor():
    """1.0 on an idle machine, larger on a busy one: best of 3 spawn-and-reap times divided by IDLE_SPAWN, never below 1.0."""
    best = None
    for _ in range(3):
        t = time.monotonic()
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        took = time.monotonic() - t
        best = took if best is None else min(best, took)
    return max(1.0, best / IDLE_SPAWN)
