"""Two full test suites at a time on this machine, at low priority.

Several projects can start their full suite together; on a loaded machine the timing-sensitive tests then fail for the
wrong reason. Every full-suite run (`budgit main-check`, `budgit suite`) takes one of the machine-wide slots first and
waits for one to free up (R-382: two slots by default, `admit.suite_slots` in the home config or BUDGIT_SUITE_SLOTS; one
reproduces the old one-at-a-time queue). A slot is an flock on one file under the Budgit home (suite.lock, suite.lock.1, ...),
so it is released when the holder dies (no stale pid to judge). The load average at the start is recorded so a timing
failure can be judged against how busy the machine was (the busy snapshot of admit.is_busy: CPU and memory pressure).

The suite command itself runs under `nice -n 10`, which leaves the CPU to hooks and the UI (BUDGIT_SUITE_PRIORITY=background uses
`taskpolicy -b` instead, =off leaves the priority alone). `taskpolicy -b` is not the default: on Apple silicon it confines the
suite to the efficiency cores and measured 4.5x slower than `nice` for the same work.

Who gets a free slot is decided by suiteorder.py (R-402): a release suite first, then the projects in turn, one waiting
suite per checkout, so a project with many lanes cannot keep another project's suite waiting.

A suite also asks `budgit admit` (admit.py) before it starts: while the busy CPU (kernel counters, in cores) is above the
limit, 85 % of the cores by default, it does not start (`--wait` waits and rechecks every 30 s instead). The thresholds live in the home config, not here; nothing here
runs inside a hook.

A suite that starts a nested run (a test that calls `budgit main-check`) passes through: the holder marks its child with
BUDGIT_SUITE_QUEUE_HELD, otherwise the child would wait for its own parent forever."""
import fcntl
import json
import os
import shlex
import shutil
import time

import studiolib as sl
import suiteorder

LOCK_NAME = "suite.lock"
HELD_ENV = "BUDGIT_SUITE_QUEUE_HELD"
POLL = 0.5
NOTICE_EVERY = 60
LOAD_POLL = 30
DEFAULT_SLOTS = 2
NICE = 10


def lock_path():
    return sl.env("SUITE_LOCK") or os.path.join(sl.home_state(), LOCK_NAME)


def slot_count():
    """How many suites may run at once: BUDGIT_SUITE_SLOTS, else `admit.suite_slots` in the home config, else 2 (at least 1)."""
    raw = sl.env("SUITE_SLOTS")
    if raw is None:
        try:
            cfg = sl.load_config().get("admit")
        except sl.StudioError:
            cfg = None
        raw = cfg.get("suite_slots") if isinstance(cfg, dict) else None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SLOTS
    return n if n >= 1 else DEFAULT_SLOTS


def slot_paths():
    base = lock_path()
    return [base] + ["%s.%d" % (base, i) for i in range(1, slot_count())]


def lower_priority(command):
    """`command` (a shell string) run at low priority, per BUDGIT_SUITE_PRIORITY: nice (default), background, off."""
    mode = (sl.env("SUITE_PRIORITY") or "nice").lower()
    if mode == "off":
        return command
    if mode == "background" and shutil.which("taskpolicy"):
        return "taskpolicy -b /bin/sh -c %s" % shlex.quote(command)
    nice = shutil.which("nice")
    return "%s -n %d /bin/sh -c %s" % (shlex.quote(nice), NICE, shlex.quote(command)) if nice else command


class LoadTooHigh(sl.StudioError):
    def __init__(self, load, limit):
        super().__init__("busy CPU %.1f cores is above the limit %.1f cores" % (load, limit))
        self.load, self.limit = load, limit


def load_limit():
    """The load above which a suite does not start (admit's wait threshold: the core count unless the config or
    BUDGIT_SUITE_LOAD_LIMIT says otherwise)."""
    import admit
    return admit.thresholds({"cores": admit.cores()})["wait_load"]


SNAPSHOT_WINDOW = 1        # seconds of CPU sampling for the start note; a note, so one short sample


def busy_snapshot():
    """R-889b: the busy snapshot recorded at suite start, from the ONE shared busy check (admit.is_busy): "busy: <signal>"
    or the calm text ("free", plus any signal that could not be read). A timing note only, never decides; never the load
    average. None when the check fails."""
    import admit
    try:
        busy, why = admit.is_busy(window=SNAPSHOT_WINDOW)
    except Exception:
        return None
    return ("busy: " + why) if busy else why


def _holder(fd_path):
    try:
        with open(fd_path) as f:
            return json.loads(f.read() or "{}")
    except (OSError, ValueError):
        return {}


class SuiteSlot:
    """`with SuiteSlot(label, notify=fn) as slot:` -> slot.waited (seconds queued), slot.load (the busy snapshot at start,
    a string from busy_snapshot; `snapshot` injects it). `notify(waited_seconds, holder_dict)` is called when the wait
    begins and then every minute."""

    def __init__(self, label="", notify=None, sleep=time.sleep, wait_load=False, load_notice=None, project=None, release=None,
                 snapshot=None, cpu=None):
        self.label, self.notify, self.sleep = label, notify, sleep
        self.snapshot = snapshot or busy_snapshot
        self.cpu = cpu                                   # busy cores reader; default admit.cpu_load (kernel counters)
        self.project, self.release = project, (release if release is not None else bool(sl.env("SUITE_RELEASE")))
        self.wait_load, self.load_notice = wait_load, load_notice
        self.waited, self.load, self.fd, self.path = 0, None, None, None

    def __enter__(self):
        if os.environ.get(HELD_ENV):
            self.load = self.snapshot()
            return self
        paths = slot_paths()
        os.makedirs(os.path.dirname(paths[0]), exist_ok=True)
        began, last = time.time(), None
        ticket, key = self._join()
        try:
            while True:
                ahead = self._ahead(ticket)
                if ahead == 0:
                    self.fd, self.path = self._take(paths)
                    if self.fd is not None:
                        break
                waited = time.time() - began
                if self.notify and (last is None or waited - last >= NOTICE_EVERY):
                    self.notify(int(waited), dict(_holder(paths[0]), ahead=ahead))
                    last = waited
                self.sleep(POLL)
        finally:
            self._leave(ticket, key, served=self.fd is not None)
        try:
            self._admit()
        except BaseException:
            self._release()
            raise
        self.waited = int(time.time() - began)
        os.environ[HELD_ENV] = "1"
        try:
            os.ftruncate(self.fd, 0)
            os.write(self.fd, json.dumps({"pid": os.getpid(), "what": self.label, "since": sl.now(), "load": self.load}).encode())
        except OSError:
            pass
        return self

    def _join(self):
        """Take a place in the fair queue -> (ticket, project key); (None, None) when the queue file cannot be used."""
        if not self.project:
            return None, None
        key = suiteorder.project_key(self.project)
        ticket = "%d-%d" % (os.getpid(), time.time() * 1000)
        try:
            suiteorder.join(ticket, key, os.path.realpath(self.project), self.release, self.label)
        except OSError:
            return None, None
        return ticket, key

    @staticmethod
    def _ahead(ticket):
        if ticket is None:
            return 0
        try:
            return suiteorder.position(ticket)[0]
        except OSError:
            return 0

    @staticmethod
    def _leave(ticket, key, served):
        if ticket is None:
            return
        try:
            suiteorder.leave(ticket, key if served else None)
        except OSError:
            pass

    @staticmethod
    def _take(paths):
        """The first free slot -> (fd, path), or (None, None) when every slot is held."""
        for path in paths:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd, path
            except OSError:
                os.close(fd)
        return None, None

    def _admit(self):
        """Holding the lock: ask admit; refuse (or, with wait_load, wait for) a machine that is not ready for a suite."""
        import admit
        while True:
            self.load = self.snapshot()                             # recorded for timing notes only, never decides (R-889b)
            got = admit.decide({"cpu_load": (self.cpu or admit.cpu_load)(), "cores": admit.cores()}, "suite")
            if got["verdict"] == admit.GO:
                return
            limits = got["limits"]
            limit = limits["refuse_load"] if got["verdict"] == admit.REFUSE else limits["wait_load"]
            load = got["measured"]["cpu_load"]
            if not self.wait_load:
                raise LoadTooHigh(load, limit)
            if self.load_notice:
                self.load_notice(load, limit)
            self.sleep(LOAD_POLL)

    def __exit__(self, *exc):
        self._release()

    def _release(self):
        if self.fd is not None:
            os.environ.pop(HELD_ENV, None)
            try:
                os.ftruncate(self.fd, 0)
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
            self.fd = None
