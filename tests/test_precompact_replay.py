"""R-123 replay: every compaction in compactions.log must have a checkpoint within 1 minute.
Before the fix (no PreCompact hook) every logged compaction is uncovered; after it, none is."""
import calendar, os, re, time

from helpers import StudioTestCase
import studiolib as sl
import checkpoint

ENTRY = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.\d+Z trigger=(\S*)$", re.M)
AT = re.compile(r"^at: (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z$", re.M)


def epoch(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))


def uncovered(log_text, sdir):
    """Compactions in the log with no checkpoint within 60 s."""
    cps = []
    d = os.path.join(sdir, "cps")
    for name in (os.listdir(d) if os.path.isdir(d) else []):
        m = AT.search(open(os.path.join(d, name), encoding="utf-8").read())
        if m:
            cps.append(epoch(m.group(1)))
    return [m.group(1) for m in ENTRY.finditer(log_text)
            if not any(abs(epoch(m.group(1)) - c) <= 60 for c in cps)]


class PreCompactReplayTest(StudioTestCase):
    def synthetic_log(self):
        """The shape hook.on_post_compact writes: '<now> trigger=<t>' then the summary, then a blank line."""
        stamps = [1_700_000_000, 1_700_003_600, 1_700_090_000]
        text = ""
        for i, t in enumerate(stamps):
            iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".123Z"
            text += "%s trigger=%s\nsummary %d\nline two\n\n" % (iso, "auto" if i else "manual", i)
        return text, stamps

    def test_every_compaction_is_uncovered_before_the_fix_and_none_after(self):
        text, stamps = self.synthetic_log()
        sdir = os.path.join(self.tmp, "state")
        os.makedirs(sdir)
        self.assertEqual(len(uncovered(text, sdir)), 3)                 # before: no checkpoint exists
        for t in stamps:                                               # after: the hook ran 2 s before each PostCompact
            path, err = checkpoint.run(self.project, sdir, [], None, "auto", "s1", at=t - 2,
                                         store=os.path.join(sdir, "cps"))
            self.assertEqual(err, "")
        self.assertEqual(uncovered(text, sdir), [])

    def test_the_log_shape_matches_what_the_hook_writes(self):
        sl.activate(self.project, "s1")
        import hook
        hook.on_post_compact({"trigger": "auto", "compact_summary": "sum"}, self.project)
        log = open(os.path.join(sl.studio_dir(self.project), "compactions.log"), encoding="utf-8").read()
        self.assertEqual(len(ENTRY.findall(log)), 1)
