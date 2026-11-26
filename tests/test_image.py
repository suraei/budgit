import json, os, stat, subprocess, sys
from helpers import StudioTestCase, ROOT, codex_on
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, uuid
home = os.environ["CODEX_HOME"]
tid = "thread-" + uuid.uuid4().hex[:8]
d = os.path.join(home, "generated_images", tid); os.makedirs(d)
open(os.path.join(d, "exec-1.png"), "wb").write(b"\x89PNG\r\n\x1a\nfake")
prompt = sys.argv[-1]
if not os.path.samestat(os.fstat(0), os.stat(os.devnull)):
    open(os.path.join(home, "stdin_not_devnull"), "w").write("codex would wait on stdin")
refs = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--image=")]
assert not any(a == "-i" for a in sys.argv), "variadic -i would swallow the prompt"
open(os.path.join(home, "last_refs.txt"), "w").write("\n".join(refs))
print(json.dumps({"type": "thread.started", "thread_id": tid}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 30}}))
open(os.path.join(home, "last_prompt.txt"), "w").write(prompt)
'''


class ImageTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        codex_on(self.home)
        self.codex_home = os.path.join(self.tmp, "codexhome"); os.makedirs(self.codex_home)
        self.fake = os.path.join(self.tmp, "fake-codex")
        open(self.fake, "w").write(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)

    def cli(self, *args, stdin=None):
        env = dict(os.environ, STUDIO_HOME=self.home, CODEX_HOME=self.codex_home, STUDIO_CODEX_BIN=self.fake)
        return subprocess.run([sys.executable, CLI, "--project", self.project] + list(args), env=env,
                              capture_output=True, text=True, stdin=stdin)

    def test_image_is_copied_and_traced(self):
        r = self.cli("image", "cozy miso soup, warm light", "--name", "soup-key-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = os.path.join(self.project, "assets", "generated", "soup-key-01.png")
        self.assertTrue(os.path.isfile(out))
        rec = json.loads(open(os.path.join(self.project, "assets", "generated", "MANIFEST.jsonl")).read().splitlines()[-1])
        for key in ("file", "prompt", "sha256", "tool", "thread_id", "usage", "seconds", "ts"):
            self.assertIn(key, rec)
        self.assertEqual(rec["prompt"], "cozy miso soup, warm light")
        sent = open(os.path.join(self.codex_home, "last_prompt.txt")).read()
        self.assertIn("cozy miso soup, warm light", sent)
        self.assertIn("Do not write", sent)

    def test_codex_stdin_is_devnull_so_background_runs_cannot_hang(self):
        # inbox 2026-10-12: codex exec reads "additional input" from a non-EOF stdin and hangs
        r = self.cli("image", "cozy soup", "--name", "soup-stdin", stdin=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.codex_home, "stdin_not_devnull")))

    def test_existing_name_is_not_overwritten(self):
        self.cli("image", "a", "--name", "same")
        r = self.cli("image", "b", "--name", "same")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exists", r.stderr)

    def test_bad_names_rejected(self):
        self.assertNotEqual(self.cli("image", "x", "--name", "../escape").returncode, 0)


class ImageSymlinkTest(ImageTest):
    def test_symlinked_output_dir_is_refused(self):
        outside = os.path.join(self.tmp, "outside"); os.makedirs(outside)
        os.makedirs(os.path.join(self.project, "assets"))
        os.symlink(outside, os.path.join(self.project, "assets", "generated"))
        r = self.cli("image", "x", "--name", "one")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(outside), [])

    def test_symlinked_target_is_replaced_not_written_through(self):
        victim = os.path.join(self.tmp, "victim.txt"); open(victim, "w").write("precious")
        os.makedirs(os.path.join(self.project, "assets", "generated"))
        # a dangling symlink at the target name must not be followed
        os.symlink(victim, os.path.join(self.project, "assets", "generated", "two.png"))
        r = self.cli("image", "x", "--name", "two")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(open(victim).read(), "precious")


class ImageRefTest(ImageTest):
    def test_reference_image_is_passed_and_traced(self):
        ref = os.path.join(self.project, "ref.png"); open(ref, "wb").write(b"\x89PNG\r\n\x1a\nref")
        r = self.cli("image", "restyle as cel animation", "--name", "restyle-a", "--ref", ref)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(os.path.realpath(ref), open(os.path.join(self.codex_home, "last_refs.txt")).read())
        rec = json.loads(open(os.path.join(self.project, "assets", "generated", "MANIFEST.jsonl")).read().splitlines()[-1])
        self.assertEqual(rec["refs"], ["ref.png"])

    def test_missing_reference_rejected(self):
        self.assertNotEqual(self.cli("image", "x", "--name", "n", "--ref", "/nope.png").returncode, 0)
