"""Security gate 2: the read-modify-write files of the Budgit home (the TM inbox, the config's protected_dirs, the trust and
approval files) are written one writer at a time. Eight parallel writers each; a slow write widens the window so a missing lock
shows as a lost write every time."""
import json
import os
import threading
import time

from test_livework import LiveCase
import portfolio
import studiolib as sl

WRITERS = 8


def together(fn):
    """Run fn(i) for i in range(WRITERS) at the same moment; re-raise the first error."""
    errors, gate = [], threading.Barrier(WRITERS)

    def one(i):
        try:
            gate.wait()
            fn(i)
        except Exception as exc:                         # noqa: BLE001 — reported below
            errors.append(exc)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]


class HomeLocksTest(LiveCase):
    def slow(self, owner, name):
        """Make `owner.name` wait a moment before it runs: the gap between a read and the write after it."""
        real = getattr(owner, name)

        def slowed(*a, **k):
            time.sleep(0.03)
            return real(*a, **k)
        setattr(owner, name, slowed)
        self.addCleanup(lambda: setattr(owner, name, real))

    def projects(self, count):
        root = self.proj("Dev") if hasattr(self, "proj") else os.path.join(self.uhome, "Dev")
        self.config(root=root)
        paths = [self.mk(os.path.join(root, "p%d" % i), [("R-1", "open")]) for i in range(count)]
        portfolio.registry()
        return paths

    def test_eight_tm_prompts_get_eight_different_ids(self):
        self.slow(portfolio, "load_tm")
        ids = []
        together(lambda i: ids.append(portfolio.append_tm("mensaje %d" % i, "s-flow0001")))
        self.assertEqual(sorted(ids), sorted("T-%d" % n for n in range(1, WRITERS + 1)))
        self.assertEqual(len(portfolio.load_tm()), WRITERS)

    def test_eight_routes_keep_every_routed_mark(self):
        (path,) = self.projects(1)
        for n in range(WRITERS):
            portfolio.append_tm("mensaje %d" % n, "s-flow0001")
        self.slow(portfolio, "load_tm")
        together(lambda i: portfolio.route("T-%d" % (i + 1), os.path.basename(path)))
        self.assertEqual([bool(r["routed"]) for r in portfolio.load_tm()], [True] * WRITERS)

    def test_eight_protects_keep_every_protected_dir(self):
        paths = self.projects(WRITERS)
        self.slow(sl, "atomic_write")
        together(lambda i: portfolio.protect(os.path.basename(paths[i])))
        with open(os.path.join(self.bhome, "config.json"), encoding="utf-8") as f:
            held = json.load(f)["protected_dirs"]
        self.assertEqual(sorted(os.path.realpath(d) for d in held), sorted(os.path.realpath(p) for p in paths))

    def test_eight_pending_grants_keep_every_note(self):
        self.slow(sl, "_save_dict")
        together(lambda i: sl.note_pending_grant("s-pend%04d" % i, self.uhome))
        with open(sl._pending_path(), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), WRITERS)

    def test_eight_approvals_keep_every_digest(self):
        paths = [os.path.join(self.uhome, "ap%d" % i) for i in range(WRITERS)]
        for p in paths:
            os.makedirs(os.path.join(p, ".studio"))
        self.slow(sl, "atomic_write")
        together(lambda i: sl._write_approval(paths[i], ["true"]))
        self.assertEqual(len(sl._load_approvals()), WRITERS)


class StrictLockTest(LiveCase):
    """A lock file that cannot be opened (a symlink planted at its path) never lets a writer go on unlocked."""
    projects = HomeLocksTest.projects

    def plant(self, *names):
        outside = os.path.join(self.uhome, "elsewhere")
        os.makedirs(outside, exist_ok=True)
        os.makedirs(sl.home_state(), exist_ok=True)
        for name in names:
            if os.path.lexists(os.path.join(sl.home_state(), name + ".lock")):
                os.unlink(os.path.join(sl.home_state(), name + ".lock"))
            os.symlink(os.path.join(outside, name), os.path.join(sl.home_state(), name + ".lock"))
        return outside

    def test_the_default_lock_still_yields_so_old_callers_keep_working(self):
        self.plant("old")
        with sl.home_lock("old"):
            pass
        with self.assertRaises(sl.StudioError):
            with sl.home_lock("old", strict=True):
                pass

    def test_route_and_append_tm_refuse_and_write_nothing(self):
        (path,) = self.projects(1)
        portfolio.append_tm("antes", "s-flow0001")
        before = open(portfolio.tm_path(), "rb").read()
        outside = self.plant("tm-inbox")
        with self.assertRaises(sl.StudioError):
            portfolio.append_tm("despues", "s-flow0001")
        with self.assertRaises(sl.StudioError):
            portfolio.route("T-1", os.path.basename(path))
        self.assertEqual(open(portfolio.tm_path(), "rb").read(), before)
        self.assertEqual(os.listdir(outside), [])

    def test_protect_and_the_config_writers_refuse_and_write_nothing(self):
        (path,) = self.projects(1)
        self.plant("config")
        before = open(os.path.join(self.bhome, "config.json"), "rb").read()
        for call in (lambda: portfolio.protect(os.path.basename(path)), lambda: sl.set_status_colour("off"),
                     lambda: __import__("i18n").set_language("en")):
            with self.assertRaises(sl.StudioError):
                call()
        self.assertEqual(open(os.path.join(self.bhome, "config.json"), "rb").read(), before)

    def test_the_trust_and_approval_writers_refuse_and_write_nothing(self):
        self.plant("trust", "trust-pending", "approvals")
        proj = os.path.join(self.uhome, "ap")
        os.makedirs(os.path.join(proj, ".studio"))
        with self.assertRaises(sl.StudioError):
            sl._write_approval(proj, ["true"])
        with self.assertRaises(sl.StudioError):
            sl.note_pending_grant("s-pend0001", proj)
        with self.assertRaises(sl.StudioError):
            sl.revoke_trust(proj)
        self.assertFalse(os.path.exists(sl._approvals_path()))
        self.assertFalse(os.path.exists(sl._pending_path()))

    def test_the_hook_logs_and_skips_a_tm_prompt_it_cannot_lock(self):
        import hook
        self.plant("tm-inbox")
        os.makedirs(sl.home_state(), exist_ok=True)
        with open(os.path.join(sl.home_state(), "portfolio.json"), "w") as f:
            json.dump({"projects": [], "tm": "s-flow0001"}, f)
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        self.assertTrue(hook.on_tm("user-prompt-submit", {"session_id": "s-flow0001", "prompt": "hola"}))
        self.assertFalse(os.path.exists(portfolio.tm_path()))
