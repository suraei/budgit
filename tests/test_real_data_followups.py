"""R-493 verifier round 2: a whole-root pattern protects everything under the root; a read-only substitution held in a
variable that is only read is silent; tee through a /dev name that climbs out of /dev asks; and the remembered yes works
through the REAL hook entry (a subprocess with a temp state dir)."""
import json
import os
import time
import unittest

from test_guard_trust import GuardCase, HookCase2
from test_real_data_plans import PlansCase

import studiolib as sl


class WholeRootPatternTest(GuardCase):
    def protect(self, pattern):
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        with open(pj) as fh:
            cfg = json.load(fh)
        cfg["protected_paths"] = [pattern]
        with open(pj, "w") as fh:
            json.dump(cfg, fh)
        os.makedirs(os.path.join(self.project, "pkg"), exist_ok=True)
        with open(os.path.join(self.project, "pkg", "book.py"), "w") as fh:
            fh.write("x\n")

    def check(self, pattern):
        self.protect(pattern)
        r = self.root
        self.asks("echo x > pkg/book.py", "rm pkg/book.py", "sed -i s/a/b/ pkg/book.py", "cd %s && echo x > pkg/book.py" % r,
                  "cd %s && rm pkg/book.py" % r, "echo x > %s/pkg/book.py" % r, "cp /tmp/x pkg/book.py")
        self.passes("cat pkg/book.py", "ls pkg", "grep a pkg/book.py", "git -C %s log --oneline" % r)

    def test_a_dot(self):
        self.check(".")

    def test_a_dot_slash(self):
        self.check("./")

    def test_the_absolute_root(self):
        self.check(self.root)


class ReadOnlySubstitutionTest(PlansCase):
    def test_a_read_only_body_whose_variable_is_only_read_is_silent(self):
        r = self.root
        self.passes("F=$(ls -t %s/plans/* | head -1); sed -n 1,40p $F; cat $F | head" % r,
                    "F=$(find plans -name x); cat $F", "F=plans/$(date +%%F).json; cat $F",
                    "F=$(ls -t %s/plans/* | head -1); grep foo $F; wc -l $F; tail -3 $F; head -2 $F" % r,
                    "F=$(ls -t %s/plans/* | head -1); awk '{print $1}' $F" % r)

    def test_a_write_through_the_variable_still_asks(self):
        r = self.root
        for use in ("sed -i s/a/b/ $F", "echo x > $F", "rm $F", "echo x | tee $F", "cp /tmp/x $F"):
            self.asks("F=$(ls -t %s/plans/* | head -1); %s" % (r, use))

    def test_an_unreadable_or_writing_body_still_asks(self):
        r = self.root
        self.asks("F=$(mytool %s/plans); cat $F" % r, "F=$(find plans -name x -delete); cat $F",
                  "F=$(rm -f %s/plans/a.handles.json); cat $F" % r, "F=$(find plans -name x -exec rm {} +); cat $F")

    def test_quoted_and_backtick_forms_are_read_the_same_way(self):
        r = self.root
        self.passes('F="$(ls -t %s/plans/* | head -1)"; cat "$F"' % r, "F=`ls -t %s/plans/* | head -1`; head -3 $F" % r)

    def test_running_or_changing_what_the_variable_names_still_asks(self):
        r = self.root
        for use in ("$F", "bash $F", "python3 $F", "source $F", "cat $F > $F", "sed -i.bak s/a/b/ $F", "mv $F /tmp/x",
                    "rm plans/a.handles.json", "echo x >> $F", "ln -sf /tmp/x $F"):
            self.asks("F=$(ls -t %s/plans/* | head -1); %s" % (r, use))

    def test_a_substitution_that_is_not_only_an_assignment_asks(self):
        r = self.root
        self.asks("F=$(ls %s/plans/*); rm $(ls %s/plans/*.handles.json)" % (r, r),
                  "F=$(ls %s/plans/* | tee %s/plans/a.handles.json); cat $F" % (r, r), "F=$(ls $(echo %s/plans)); cat $F" % r,
                  "F=$(ls %s/plans/*); cat <<EOF\n$(rm %s/plans/a.handles.json)\nEOF" % (r, r))

    def test_an_inline_substitution_is_as_before(self):
        self.asks("rm $(ls %s/plans/*.handles.json)" % self.root)


class TeeThroughDevTest(GuardCase):
    def test_tee_through_a_dev_name_that_climbs_out_asks(self):
        self.asks("tee /dev/stdout/../..%s/data.json" % self.root, "echo x | tee /dev/stderr/../..%s/data.json" % self.root)
        self.passes("echo x | tee /dev/null")


class MemoryThroughTheRealHookTest(HookCase2):
    """ask -> pending written -> promote -> silent -> another session asks -> hand-written yes ignored -> wrong nonce ignored."""

    def setUp(self):
        super().setUp()
        self.cmd = "rm %s/data.json" % self.root
        self.key = "write:" + sl._fold(os.path.join(self.root, "data.json"))

    def post(self, session):
        self.fire("post-tool-use", dict(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": self.cmd},
                                        tool_response={}), session_id=session)

    def path(self, kind, session):
        return os.path.join(self.home, kind, session + ".jsonl")

    def test_the_memory_through_the_hook_entry(self):
        a, b, c, d = ["sess-e2e-" + n for n in ("aaaa1111", "bbbb2222", "cccc3333", "dddd4444")]
        for s in (a, b, c, d):
            sl.activate(self.project, session_id=s)
        self.assertEqual(self.bash(self.cmd, session_id=a), "ask", "step a")
        self.assertTrue(os.path.exists(self.path(sl.PENDING_DIR, a)))
        self.assertFalse(os.path.exists(self.path(sl.YES_DIR, a)))
        self.post(a)
        self.assertTrue(os.path.exists(self.path(sl.YES_DIR, a)))
        self.assertIsNone(self.bash(self.cmd, session_id=a))                          # the same write: silent
        self.assertEqual(self.bash(self.cmd, session_id=b), "ask", "step b")                    # another session asks again
        # a hand-written yes with no pending entry at all (session c has never asked)
        os.makedirs(os.path.dirname(self.path(sl.YES_DIR, c)), exist_ok=True)
        with open(self.path(sl.YES_DIR, c), "w") as fh:
            fh.write(json.dumps({"k": self.key, "fp": sl.call_digest("Bash", {"command": self.cmd}), "ts": time.time()}) + "\n")
        self.assertEqual(self.bash(self.cmd, session_id=c), "ask", "step c")
        # a pending entry exists (the ask wrote it) but the hand-written yes carries another nonce
        self.assertEqual(self.bash(self.cmd, session_id=d), "ask", "step d")
        with open(self.path(sl.YES_DIR, d), "w") as fh:
            fh.write(json.dumps({"k": self.key, "fp": sl.call_digest("Bash", {"command": self.cmd}), "nonce": "0" * 32,
                                 "ts": time.time()}) + "\n")
        self.assertEqual(self.bash(self.cmd, session_id=d), "ask", "step d")
        self.assertEqual(self.bash(self.cmd, session_id=a) is None, True)             # the genuine yes still stands


if __name__ == "__main__":
    unittest.main()
