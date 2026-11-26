"""R-292: runs the source-level guards on brote.swift (tools/brote/float/tests/test_source_guards.py) as part of the main suite."""
import importlib.util
import unittest
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "tools" / "brote" / "float" / "tests" / "test_source_guards.py"
_spec = importlib.util.spec_from_file_location("brote_source_guards", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

Guards = _mod.Guards

_SWIFT = Path(__file__).resolve().parents[1] / "tools" / "brote" / "float" / "brote.swift"

# R-846: the three call-site wirings that keep him in his corner. Each needle must appear in the source.
R846_WIRING = {
    "outing gate": "} else if outingDue(explorer, state: glue.bridge.state, idle: glue.outingFree, now: now) {",
    "stroll wander": "visit: visit, wander: K.randomStroll)",
    "route clamp": "let legs = clampedLegs(raw, from: layout.pos, screens: layout.screens)",
}


def r846_missing(src):
    return [name for name, needle in R846_WIRING.items() if needle not in src]


class R846Wiring(unittest.TestCase):
    def setUp(self):
        self.src = _SWIFT.read_text(encoding="utf-8")

    def test_the_outing_gate_is_the_only_way_to_leave(self):
        self.assertNotIn("explorer.mayLeave(", self.src.replace("ex.mayLeave(", ""))
        self.assertNotIn("outing gate", r846_missing(self.src))

    def test_the_stroll_tick_takes_wander_from_the_constant(self):
        self.assertNotIn("stroll wander", r846_missing(self.src))
        self.assertIn("static let outingsOn = false, randomStroll = false", self.src)

    def test_every_route_is_clamped_to_the_visible_frame(self):
        self.assertNotIn("route clamp", r846_missing(self.src))

    def test_removing_any_wiring_fails_the_guard(self):
        # scratch-copy mutation: drop each wiring in memory and expect the guard to name it
        for name, needle in R846_WIRING.items():
            self.assertEqual(r846_missing(self.src.replace(needle, "")), [name])

if __name__ == "__main__":
    unittest.main()
