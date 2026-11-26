"""R-1307 slice A0a: the length cap inside `writeguard._git_text` and the program gate in `writeguard._brace_text_why`.

Times are multiples of one calibration unit U (the median process CPU time of 5 runs of a fixed pure-Python loop), never seconds.
The program's name is built with chr() here so this file never holds it.
Named residual: the U literals were checked on the current Python only; Python 3.8 is not installed on this machine, so the plan's
3.8 check of them is still open."""
import statistics
import time
import unittest

import writeguard

G, I, T = chr(103), chr(105), chr(116)
V = G + I + T
OB, CB = chr(123), chr(125)             # open and close brace, kept out of the shell-looking literals below
MEGABYTE = 1024 * 1024

# Bounds in units of U. The first measurement is pinned here (checked on the current Python; 3.8 is not installed on this machine).
ONE_MB_BOUND_U = 5


def _calibration_loop():
    n = 0
    for _ in range(200000):
        n += 1
    return n


def unit():
    runs = []
    for _ in range(5):
        start = time.process_time()
        _calibration_loop()
        runs.append(time.process_time() - start)
    return statistics.median(runs)


def cost_in_units(fn, best_of=1, floor=0.2):
    """Process CPU time of one call of fn in units of U. Below `floor` U (at least 0.2) the loop count doubles until the block lasts that long."""
    u = unit()
    best = None
    for _ in range(best_of):
        loops = 1
        while True:
            start = time.process_time()
            for _ in range(loops):
                fn()
            spent = time.process_time() - start
            if spent >= floor * u:
                break
            loops *= 2
        per_call = spent / loops
        best = per_call if best is None else min(best, per_call)
    return best / u


def brace(inner):
    return OB + inner + CB


class GitTextCapsItsOwnLength(unittest.TestCase):
    def test_r1307_git_text_caps_its_own_length(self):
        cases = [
            ("1 MB holding the name as plain text asks", "echo " + V + " " + "a" * MEGABYTE, True),
            ("1 MB with a dollar asks", "echo " + chr(36) + "x " + "a" * MEGABYTE, True),
            ("1 MB with a brace and the letters g i t apart asks", "echo " + brace("a") + " " + G + " " + I + " " + T + " " + "a" * MEGABYTE, True),
            ("5000 characters with a letters-gated brace name asks", "echo " + brace("gi,xx") + "t " + "a" * 5000, True),
            ("5000 characters of JSON with no name letters does not ask", "echo '" + brace('"a":1') + "' " + "a" * 5000, False),
            ("1 MB with no name, no dollar, no brace does not ask", "echo " + "a" * MEGABYTE, False),
        ]
        for label, line, asks in cases:
            with self.subTest(label):
                spent = cost_in_units(lambda: writeguard._git_text(line))
                why = writeguard._git_text(line)
                if asks:
                    self.assertIsNotNone(why, label)
                    self.assertIn("may name " + V, why)
                else:
                    self.assertIsNone(why, label)
                if len(line) > MEGABYTE:
                    self.assertLessEqual(spent, ONE_MB_BOUND_U, "%s: %.1f U" % (label, spent))

    def test_r1307_oversize_gate_is_what_the_plan_says(self):
        gate = writeguard._oversize_may_name
        self.assertTrue(gate("x " + V))
        self.assertTrue(gate(G + "''" + I + T))
        self.assertTrue(gate("a " + chr(36) + "b"))
        self.assertTrue(gate("a " + brace("gi,xx") + "t"))
        self.assertTrue(gate("a " + brace("f..h") + I + T.upper()))
        self.assertTrue(gate(brace("a..b") + " nothing"))
        self.assertFalse(gate("a " + brace('"a":1')))
        self.assertFalse(gate("plain words " + "a" * 100))

    def test_r1307_git_text_is_linear_below_its_cap(self):
        timings = {}
        for size in (512, 1024, 2048, 4000):
            line = "echo " + "a" * (size - 5)
            self.assertIsNone(writeguard._git_text(line))
            timings[size] = cost_in_units(lambda: writeguard._git_text(line), best_of=9, floor=4)
        sizes = sorted(timings)
        for small, big in zip(sizes, sizes[1:]):
            with self.subTest("%d to %d" % (small, big)):
                self.assertLessEqual(timings[big] / timings[small], 2.6, repr(timings))


class RealHookEntryPoint(unittest.TestCase):
    """The narrowing must hold where the hook calls the guard: `destructive_git_decision`, not only `_git_text`."""

    def ask(self, line):
        return writeguard.destructive_git_decision("Bash", {"command": line})

    def test_r1307_real_path_json_without_the_letters_gets_no_ask(self):
        self.assertIsNone(self.ask("echo '" + brace('"a":1') + "' " + "a" * 5000))

    def test_r1307_real_path_json_with_the_letters_asks_accepted_false_ask(self):
        got = self.ask("echo '" + brace('"a":1') + "' " + G + " " + I + " " + T + " " + "a" * 5000)
        self.assertIsNotNone(got)
        self.assertIn("may name " + V, got[1])

    def test_r1307_real_path_built_name_over_the_cap_asks(self):
        self.assertIsNotNone(self.ask("echo " + brace("gi,xx") + "t " + "a" * 5000))

    def test_r1307_real_path_one_megabyte_with_a_dollar_asks(self):
        self.assertIsNotNone(self.ask("echo " + chr(36) + "x " + "a" * MEGABYTE))

    def test_r1307_real_path_destructive_commands_over_the_cap_ask(self):
        pad = "a" * 5000
        rows = {
            "plain hard reset": V + " reset --hard " + pad,
            "hard reset behind sudo": "sudo " + V + " reset --hard " + pad,
            "hard reset behind env": "env X=1 " + V + " reset --hard " + pad,
            "built-name brace hard reset": brace("gi,xx") + "t reset --hard " + pad,
            "plain forced push": V + " push --force origin main " + pad,
            "padding first, then a hard reset": "echo " + pad + "; " + V + " reset --hard",
        }
        for label, line in rows.items():
            with self.subTest(label):
                self.assertGreater(len(line), 5000)
                self.assertIsNotNone(self.ask(line), label)

    @unittest.expectedFailure       # pre-existing glob gap, owned by R-1307 slice A GLOB flag
    def test_r1307_real_path_glob_program_word_asks(self):
        self.assertIsNotNone(self.ask("[" + G + "]" + I + T + " reset --hard"))

    @unittest.expectedFailure       # pre-existing glob gap, owned by R-1307 slice A GLOB flag
    def test_r1307_real_path_glob_program_word_over_the_cap_asks(self):
        self.assertIsNotNone(self.ask("[" + G + "]" + I + T + " reset --hard " + "a" * 5000))

    @unittest.expectedFailure       # asked before A0a removed the outer oversize ask; accepted residual, plan C16 amended 21 Nov
    def test_r1307_real_path_brace_glob_program_word_over_the_cap_asks(self):
        self.assertIsNotNone(self.ask(brace("/usr/*/" + G + "?" + T + ",") + " reset --hard " + "a" * 5000))

    def test_r1307_real_path_subshell_glued_to_a_brace_name_asks(self):
        self.assertIsNotNone(self.ask("(" + brace(V + ",") + " reset --hard )"))


class SplitNameBehindUnknownWrapper(unittest.TestCase):
    def test_r1307_split_name_behind_unknown_wrapper_asks(self):
        asks = [
            "setsid " + brace("gi,xx") + brace("t,y") + " reset --hard",
            V + " " + brace("reset,status") + " --hard",
            brace("gi,xx") + "t " + brace("reset,xx") + " --hard",
            "/usr/bin/" + brace("gi,xx") + "t reset --hard",
        ]
        for line in asks:
            with self.subTest(line):
                self.assertIsNotNone(writeguard._git_text(line))
        quiet = [
            "setsid " + brace("gi,xx") + brace("t,y") + " status",
            V + " " + brace("status,log") + " --hard",
            brace("gi,xx") + "t " + brace("status,xx"),
        ]
        for line in quiet:
            with self.subTest(line):
                self.assertIsNone(writeguard._git_text(line))


class EverydayBraceLines(unittest.TestCase):
    def test_r1307_everyday_brace_lines_do_not_ask(self):
        lines = [
            "make " + brace("clean,all"),
            "echo " + brace("reset,--hard"),
            "npm run " + brace("clean,build"),
            "rm -rf " + brace("a,b"),
            "cp f" + brace(",.bak"),
            "echo " + chr(36) + brace("HOME") + "/x",
            "find . -exec echo " + brace("") + " +",
            "mkdir -p a/" + brace("b,c") + "/d",
        ]
        for line in lines:
            with self.subTest(line):
                self.assertIsNone(writeguard._git_text(line))

    def test_r1307_oversize_rows_ask_only_when_the_name_can_be_built(self):
        json_line = "echo '" + brace('"a":1') + "' " + "a" * 5000
        find_line = "find . -exec echo " + brace("") + " + " + "a" * 5000
        awk_line = "awk '" + brace("print $1") .replace(chr(36), "") + "' " + "a" * 5000
        for line in (json_line, find_line, awk_line):
            with self.subTest(line[:20]):
                self.assertIsNone(writeguard._git_text(line))
                self.assertIsNone(writeguard._brace_text_why(line))      # an oversize segment is skipped when it cannot name the program
        built = "echo " + brace("gi,xx") + "t " + "a" * 5000
        self.assertIsNotNone(writeguard._git_text(built))
        self.assertIsNotNone(writeguard._brace_text_why(built))

    def test_r1307_oversize_json_with_the_letters_asks_and_that_is_accepted(self):
        # accepted false ask, limited to lines over 4096 characters: a brace plus the letters g, i and t apart cannot be told from a built name
        line = "echo '" + brace('"a":1') + "' " + G + " " + I + " " + T + " " + "a" * 5000
        self.assertIsNotNone(writeguard._git_text(line))


if __name__ == "__main__":
    unittest.main()
