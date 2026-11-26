"""R-1106: `budgit legal profile save --from FILE` writes the legal profile atomically (0600), refuses symlinks and oversize
files, is a home-class row (a subagent is denied) and the skill's intake names it. Synthetic data only (acme)."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from helpers import ROOT
from test_guard_trust import CLI
import studiolib as sl

SKILL_MD = os.path.join(ROOT, "skills", "legal-eu-es-fr", "SKILL.md")
PROFILE = "# Legal profile\nacme sarl, residence FR\n"


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.src = os.path.join(self.tmp, "draft.md")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(PROFILE)
        self.env = {k: v for k, v in os.environ.items() if k not in ("LEGAL_HOME", "BUDGIT_SESSION")}
        self.env.update(BUDGIT_HOME=self.home, STUDIO_HOME=self.home, HOME=self.tmp)
        self.target = os.path.join(self.home, "legal", "legal-profile.md")

    def run_cli(self, source):
        return subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", source],
                              env=self.env, capture_output=True, text=True)

    def tree(self):
        out = []
        for base, dirs, files in os.walk(self.home):
            out += [os.path.relpath(os.path.join(base, n), self.home) for n in dirs + files]
        return sorted(out)

    def test_the_verb_writes_only_that_path_at_0600(self):
        r = self.run_cli(self.src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.tree(), ["legal", os.path.join("legal", "legal-profile.md")])
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.target)).st_mode), 0o700)
        with open(self.target, encoding="utf-8") as f:
            self.assertEqual(f.read(), PROFILE)
        self.assertEqual(self.run_cli(self.src).returncode, 0)            # overwrite leaves no temp file behind
        self.assertEqual(self.tree(), ["legal", os.path.join("legal", "legal-profile.md")])

    def test_it_refuses_a_symlinked_source(self):
        link = os.path.join(self.tmp, "link.md")
        os.symlink(self.src, link)
        self.assertNotEqual(self.run_cli(link).returncode, 0)
        self.assertEqual(self.tree(), [])

    def test_it_refuses_a_symlinked_target(self):
        os.makedirs(os.path.dirname(self.target))
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, self.target)
        self.assertNotEqual(self.run_cli(self.src).returncode, 0)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")

    def test_it_refuses_a_symlinked_folder(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.home, "legal"))
        self.assertNotEqual(self.run_cli(self.src).returncode, 0)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_it_refuses_an_oversize_file(self):
        with open(self.src, "w", encoding="utf-8") as f:
            f.write("a" * (64 * 1024 + 1))
        self.assertNotEqual(self.run_cli(self.src).returncode, 0)
        self.assertEqual(self.tree(), [])

    def test_it_refuses_a_non_utf8_file(self):
        with open(self.src, "wb") as f:
            f.write(b"\xff\xfe\x00bad")
        self.assertNotEqual(self.run_cli(self.src).returncode, 0)
        self.assertEqual(self.tree(), [])


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "draft.md")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(PROFILE)

    def test_it_refuses_a_directory_and_a_fifo(self):
        env = dict(os.environ, HOME=self.tmp)
        fifo = os.path.join(self.tmp, "pipe")
        os.mkfifo(fifo)
        for bad in (self.tmp, fifo):
            r = subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", bad],
                               env=env, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(r.returncode, 0, bad)

    def test_the_target_is_legal_home_in_every_budgit_home_layout(self):
        sys.path.insert(0, os.path.join(ROOT, "skills", "legal-eu-es-fr", "scripts"))
        import legalcheck as lc
        layouts = {"only .budgit": ((".budgit", True),),
                   "only .studio": ((".studio", True),),
                   "both, ledger in .studio": ((".budgit", True), (".studio", True)),
                   "empty .budgit plus valid .studio": ((".budgit", False), (".studio", True))}
        for label, dirs in layouts.items():
            user = tempfile.mkdtemp()
            for name, ledger in dirs:
                os.makedirs(os.path.join(user, name))
                if ledger:
                    with open(os.path.join(user, name, "ledger.json"), "w") as f:
                        f.write("{}")
            env = {k: v for k, v in os.environ.items() if k not in ("LEGAL_HOME", "BUDGIT_HOME", "STUDIO_HOME", "BUDGIT_SESSION")}
            env["HOME"] = user
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                expected = os.path.join(lc.legal_home(), "legal-profile.md")
            r = subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", self.src],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, label + r.stdout + r.stderr)
            self.assertTrue(os.path.isfile(expected), label)
            want = ".budgit" if label == "only .budgit" else ".studio"
            self.assertEqual(os.path.dirname(expected), os.path.join(user, want, "legal"), label)


LAYOUTS = {"only .budgit": ((".budgit", True),),
           "only .studio": ((".studio", True),),
           "both, ledger in .studio": ((".budgit", True), (".studio", True)),
           "empty .budgit plus valid .studio": ((".budgit", False), (".studio", True)),
           "migrated .budgit plus old .studio": ((".budgit", "MIGRATED"), (".studio", True)),
           "empty .budgit only": ((".budgit", False),)}


def make_home(dirs):
    user = tempfile.mkdtemp()
    for name, kind in dirs:
        os.makedirs(os.path.join(user, name))
        if kind:
            with open(os.path.join(user, name, "MIGRATED" if kind == "MIGRATED" else "ledger.json"), "w") as f:
                f.write("{}")
    env = {k: v for k, v in os.environ.items() if k not in ("LEGAL_HOME", "BUDGIT_HOME", "STUDIO_HOME", "BUDGIT_SESSION")}
    env["HOME"] = user
    return user, env


class InlinedResolverTest(unittest.TestCase):
    def test_the_inlined_resolver_equals_studiolib_resolve_data_dir(self):
        sys.path.insert(0, os.path.join(ROOT, "skills", "legal-eu-es-fr", "scripts"))
        import legalcheck as lc
        for label, dirs in LAYOUTS.items():
            user, env = make_home(dirs)
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(lc.legal_home(), os.path.join(sl.resolve_data_dir(user), "legal"), label)
                self.assertEqual(lc.legal_home(), os.path.join(sl.home_state(), "legal"), label)
        with unittest.mock.patch.dict(os.environ, dict(env, BUDGIT_HOME="/tmp/acme-home"), clear=True):
            self.assertEqual(lc.legal_home(), os.path.join(sl.home_state(), "legal"))

    def test_the_isolated_reader_and_the_verb_agree_on_the_folder(self):
        script = os.path.join(ROOT, "skills", "legal-eu-es-fr", "scripts", "legalcheck.py")
        src = os.path.join(tempfile.mkdtemp(), "draft.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write(PROFILE)
        for label, dirs in LAYOUTS.items():
            user, env = make_home(dirs)
            r = subprocess.run([sys.executable, "-I", script, "home"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, label + r.stderr)
            v = subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", src], env=env, capture_output=True, text=True)
            self.assertEqual(v.returncode, 0, label + v.stderr)
            self.assertTrue(os.path.isfile(os.path.join(r.stdout.strip(), "legal-profile.md")), label)


def load_cli_module():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("budgit_cli_r1106", CLI)
    spec = importlib.util.spec_from_loader("budgit_cli_r1106", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


SCRIPT = os.path.join(ROOT, "skills", "legal-eu-es-fr", "scripts", "legalcheck.py")


class Round2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "draft.md")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(PROFILE)
        self.env = {k: v for k, v in os.environ.items() if k not in ("LEGAL_HOME", "BUDGIT_HOME", "STUDIO_HOME", "BUDGIT_SESSION")}
        self.env["HOME"] = self.tmp

    def save(self, env, source=None, timeout=30):
        return subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", source or self.src],
                              env=env, capture_output=True, text=True, timeout=timeout)

    def test_the_cli_folder_equals_legalcheck_legal_home_in_every_layout_and_with_legal_home(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import legalcheck as lc
        cli = load_cli_module()
        for label, dirs in LAYOUTS.items():
            user, env = make_home(dirs)
            variants = [env, dict(env, LEGAL_HOME=os.path.join(user, "elsewhere", "legal")),
                        dict(env, BUDGIT_HOME=os.path.join(user, "bh"))]
            for e in variants:
                with unittest.mock.patch.dict(os.environ, e, clear=True):
                    got = cli._legal_home_dir()
                    self.assertEqual(got, lc.legal_home(), label)
                r = subprocess.run([sys.executable, "-I", SCRIPT, "home"], env=e, capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), got, label)

    def test_a_planted_skill_next_to_the_plugin_is_never_executed(self):
        import shutil
        sentinel = os.path.join(self.tmp, "sentinel")
        try:
            copy = os.path.join(self.tmp, "copy")                       # a throwaway copy of the plugin: nothing is planted in the repo
            os.makedirs(os.path.join(copy, "plugin", "bin"))
            shutil.copy(CLI, os.path.join(copy, "plugin", "bin", "budgit"))
            shutil.copytree(os.path.join(ROOT, "plugin", "scripts"), os.path.join(copy, "plugin", "scripts"))
            plants = [os.path.join(copy, "plugin", "skills", "legal-eu-es-fr", "scripts", "legalcheck.py"),
                      os.path.join(copy, "skills", "legal-eu-es-fr", "scripts", "legalcheck.py")]
            for p in plants:
                os.makedirs(os.path.dirname(p))
                with open(p, "w") as f:
                    f.write("import os\nopen(os.environ['R1106_SENTINEL'], 'w').write('ran')\ndef legal_home():\n    return '/nonexistent'\n")
            for p in plants:
                self.assertTrue(os.path.isfile(p))                      # the plant is really there: the test cannot pass vacuously
            home = os.path.join(self.tmp, "home")
            os.makedirs(home)
            env = dict(self.env, BUDGIT_HOME=home, R1106_SENTINEL=sentinel)
            r = subprocess.run([sys.executable, os.path.join(copy, "plugin", "bin", "budgit"), "legal", "profile", "save", "--from", self.src],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(sentinel))
            self.assertTrue(os.path.isfile(os.path.join(home, "legal", "legal-profile.md")))
        finally:
            shutil.rmtree(os.path.join(self.tmp, "copy"), ignore_errors=True)

    def test_a_relative_legal_home_is_refused(self):
        env = dict(self.env, LEGAL_HOME="relative/legal")
        r = self.save(env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("absolute", r.stderr + r.stdout)
        r = subprocess.run([sys.executable, "-I", SCRIPT, "home"], env=env, capture_output=True, text=True, cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, CLI, "legal", "profile", "save", "--from", self.src], env=env, capture_output=True,
                           text=True, cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "relative")))

    def test_a_legal_home_inside_a_git_work_tree_is_refused(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        target = os.path.join(repo, "sub", "legal")
        r = self.save(dict(self.env, LEGAL_HOME=target))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("git work tree", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(target))

    def test_a_source_swapped_to_a_fifo_or_a_symlink_is_refused_without_hanging(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = dict(self.env, BUDGIT_HOME=home)
        fifo = os.path.join(self.tmp, "pipe")
        os.mkfifo(fifo)
        link = os.path.join(self.tmp, "link-to-pipe")
        os.symlink(fifo, link)
        for bad in (fifo, link):
            self.assertNotEqual(self.save(env, bad, timeout=15).returncode, 0, bad)
        self.assertFalse(os.path.exists(os.path.join(home, "legal", "legal-profile.md")))

    def test_a_fresh_folder_ends_at_0700(self):
        folder = os.path.join(self.tmp, "fresh", "legal")
        self.assertEqual(self.save(dict(self.env, LEGAL_HOME=folder)).returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(folder, "legal-profile.md")).st_mode), 0o600)
        self.assertEqual(os.listdir(folder), ["legal-profile.md"])

    def test_an_existing_0755_folder_outside_the_home_is_refused_and_keeps_its_mode(self):
        folder = os.path.join(self.tmp, "mylegal")
        os.makedirs(folder)
        os.chmod(folder, 0o755)
        r = self.save(dict(self.env, LEGAL_HOME=folder))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not private", r.stderr + r.stdout)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o755)
        self.assertEqual(os.listdir(folder), [])

    def test_an_existing_0755_legal_folder_under_the_budgit_home_ends_at_0700(self):
        home = os.path.join(self.tmp, "bhome")
        folder = os.path.join(home, "legal")
        os.makedirs(folder)
        os.chmod(folder, 0o755)
        self.assertEqual(self.save(dict(self.env, BUDGIT_HOME=home)).returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)

    def test_a_symlinked_ancestor_into_a_git_work_tree_is_refused_and_writes_nothing(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, "docs"))
        os.symlink(os.path.join(repo, "docs"), os.path.join(self.tmp, "link"))
        r = self.save(dict(self.env, LEGAL_HOME=os.path.join(self.tmp, "link", "legal")))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("git work tree", r.stderr + r.stdout)
        self.assertEqual(os.listdir(os.path.join(repo, "docs")), [])


class GuardTest(unittest.TestCase):
    def test_a_subagent_is_denied_and_the_row_is_home_class(self):
        words = [CLI, "legal", "profile", "save", "--from", "/tmp/acme-draft.md"]
        call = sl._cli_call(words, list(words), "", tempfile.mkdtemp())
        self.assertEqual(call[1][0], "home")                 # no row of its own: the guard's default for an unlisted command
        cmd = "%s legal profile save --from /tmp/acme-draft.md" % CLI
        got = sl.home_write_check("Bash", {"command": cmd}, tempfile.mkdtemp(), None, subagent=True)[0]
        self.assertEqual(got[0] if got else None, "deny")


class SkillTextTest(unittest.TestCase):
    def test_the_skill_intake_names_the_verb(self):
        with open(SKILL_MD, encoding="utf-8") as f:
            self.assertIn("budgit legal profile save --from", f.read())


if __name__ == "__main__":
    unittest.main()
