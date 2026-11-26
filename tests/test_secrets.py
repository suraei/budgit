"""wave2 security: `budgit secrets` (gitleaks + osv-scanner, stubbed), opt-in pre-commit, deny audit + `budgit denies`.
Never real secrets: fake key strings are built by concatenation; scanners are stub executables on a fake PATH."""
import hashlib, json, os, stat, subprocess, sys
from unittest import mock
from helpers import BudgitTestCase, StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
FAKE_KEY = "AK" + "IA" + "Q7" * 8          # AWS-shaped, not a real key
FAKE_TOKEN = "gh" + "p_" + "x9" * 15        # GitHub-shaped, not a real token

STUB = """#!/bin/sh
echo "$0 $*" >> "%(log)s"
name=$(basename "$0")
if [ "$1" = "--version" ]; then echo "osv-scanner version: %(osv_version)s"; exit 0; fi
code=$(cat "%(dir)s/$name.exit" 2>/dev/null || echo 0)
if [ "$code" = "1" ]; then
  echo "Finding:     aws_access_key_id = REDACTED"
  echo "Secret:      REDACTED"
  echo "File:        app.py"
  echo "Line:        3"
  echo "leaked line had %(fake)s in it"
fi
exit "$code"
"""


class SecretsCase(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.tmp, "calls.log")

    def stub(self, name, exit_code=0, osv_version="2.7.0"):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(STUB % {"log": self.calls, "dir": self.bin, "osv_version": osv_version, "fake": FAKE_KEY})
        os.chmod(path, 0o755)
        with open(os.path.join(self.bin, name + ".exit"), "w") as f:
            f.write(str(exit_code))

    def run_cli(self, *args, cwd=None):
        env = dict(os.environ, BUDGIT_HOME=self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=cwd or self.project, env=env,
                              capture_output=True, text=True)

    def git_init(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)

    def calls_text(self):
        return open(self.calls).read() if os.path.exists(self.calls) else ""


class SecretsScanTest(SecretsCase):
    def test_missing_tools_skip_with_one_brew_command_and_exit_0(self):
        open(os.path.join(self.project, "package-lock.json"), "w").write("{}")
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 3, r.stderr)       # R-1128: a skipped scan exits 3, never 0
        self.assertIn("gitleaks: skipped (not installed)", r.stdout)
        self.assertIn("osv-scanner: skipped (not installed)", r.stdout)
        self.assertEqual(r.stdout.count("brew install"), 1, r.stdout)
        self.assertIn("brew install gitleaks osv-scanner", r.stdout)

    def test_git_repo_uses_gitleaks_git_redacted_and_clean_exit_0(self):
        self.git_init()
        self.stub("gitleaks", 0)
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.calls_text()
        self.assertIn("gitleaks git ", call)
        self.assertIn("--redact", call)
        self.assertIn("--no-banner", call)
        self.assertIn("gitleaks: no leaks", r.stdout)
        self.assertIn("osv-scanner: skipped (no lockfiles)", r.stdout)

    def test_non_git_path_uses_gitleaks_dir(self):
        self.stub("gitleaks", 0)
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gitleaks dir ", self.calls_text())

    def test_staged_uses_pre_commit_staged_and_skips_osv(self):
        self.git_init()
        self.stub("gitleaks", 0)
        self.stub("osv-scanner", 0)
        open(os.path.join(self.project, "uv.lock"), "w").write("")
        r = self.run_cli("secrets", "--staged", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.calls_text()
        self.assertIn("--pre-commit", call)
        self.assertIn("--staged", call)
        self.assertNotIn("osv-scanner scan", call)

    def test_findings_exit_1_and_output_never_shows_a_raw_secret(self):
        self.git_init()
        self.stub("gitleaks", 1)
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("gitleaks: LEAKS FOUND", r.stdout)
        self.assertIn("app.py", r.stdout)
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)

    def test_osv_runs_offline_on_lockfiles_v2_flag_and_vulns_exit_1(self):
        self.stub("gitleaks", 0)
        self.stub("osv-scanner", 1)
        os.makedirs(os.path.join(self.project, "web"))
        open(os.path.join(self.project, "web", "package-lock.json"), "w").write("{}")
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        call = self.calls_text()
        self.assertIn("osv-scanner scan --offline -r", call)
        self.assertIn("osv-scanner: VULNERABILITIES FOUND", r.stdout)

    def test_osv_v1_uses_experimental_offline_flag(self):
        self.stub("gitleaks", 0)
        self.stub("osv-scanner", 0, osv_version="1.9.2")
        open(os.path.join(self.project, "go.sum"), "w").write("")
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--experimental-offline", self.calls_text())
        self.assertNotIn(" --offline", self.calls_text())


class InstallHookTest(SecretsCase):
    def hook_path(self):
        return os.path.join(self.project, ".git", "hooks", "pre-commit")

    def test_installs_executable_hook_calling_secrets_staged(self):
        self.git_init()
        self.init_project()
        r = self.run_cli("secrets", "--install-hook", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = open(self.hook_path()).read()
        self.assertIn("secrets --staged", text)
        self.assertTrue(os.stat(self.hook_path()).st_mode & stat.S_IXUSR)
        self.assertIsNone(subprocess.run(["git", "-C", self.project, "config", "--get", "core.hooksPath"],
                                         capture_output=True, text=True).stdout.strip() or None)

    def test_refuses_existing_hook_and_explains_chaining(self):
        self.git_init()
        self.init_project()
        os.makedirs(os.path.dirname(self.hook_path()), exist_ok=True)
        open(self.hook_path(), "w").write("#!/bin/sh\necho mine\n")
        r = self.run_cli("secrets", "--install-hook", self.project)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(open(self.hook_path()).read(), "#!/bin/sh\necho mine\n")
        self.assertIn("budgit secrets --staged || exit 1", r.stdout + r.stderr)

    def test_refuses_non_studio_repo(self):
        self.git_init()
        r = self.run_cli("secrets", "--install-hook", self.project)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(self.hook_path()))
        self.assertIn("not a Budgit project", r.stdout + r.stderr)

    def test_installed_hook_blocks_a_commit_when_gitleaks_finds_a_leak(self):
        self.git_init()
        self.init_project()
        self.assertEqual(self.run_cli("secrets", "--install-hook", self.project).returncode, 0)
        self.stub("gitleaks", 1)
        os.symlink(CLI, os.path.join(self.bin, "budgit"))
        open(os.path.join(self.project, "app.py"), "w").write("x = 1\n")
        env = dict(os.environ, BUDGIT_HOME=self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        subprocess.run(["git", "-C", self.project, "add", "app.py"], check=True, env=env)
        r = subprocess.run(["git", "-C", self.project, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"], env=env,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)


class HookWithoutGitleaksTest(SecretsCase):
    """Wave2 follow-up: a pre-commit hook that silently passes without gitleaks is false safety."""

    def commit_without_gitleaks(self):
        self.git_init()
        self.init_project()
        self.assertEqual(self.run_cli("secrets", "--install-hook", self.project).returncode, 0)
        os.symlink(CLI, os.path.join(self.bin, "budgit"))      # no gitleaks stub: it is absent from PATH
        open(os.path.join(self.project, "app.py"), "w").write("x = 1\n")
        env = dict(os.environ, BUDGIT_HOME=self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        subprocess.run(["git", "-C", self.project, "add", "app.py"], check=True, env=env)
        return subprocess.run(["git", "-C", self.project, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"],
                              env=env, capture_output=True, text=True)

    def test_installed_hook_fails_loudly_when_gitleaks_is_not_installed(self):
        r = self.commit_without_gitleaks()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("gitleaks is not installed", out)
        self.assertIn("brew install gitleaks", out)

    def test_hook_text_checks_gitleaks_before_running_the_scan(self):
        import secretscan
        text = secretscan._hook_text()
        self.assertIn("command -v gitleaks", text)
        self.assertLess(text.index("command -v gitleaks"), text.index("secrets --staged"))

    def test_plain_scan_names_each_skipped_scanner_and_why(self):
        open(os.path.join(self.project, "package-lock.json"), "w").write("{}")
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("skipped: gitleaks (not installed), osv-scanner (not installed)", r.stdout)
        self.assertIn("NOT scanned", r.stdout)

    def test_plain_scan_skip_summary_mentions_no_lockfiles(self):
        r = self.run_cli("secrets", self.project)
        self.assertIn("skipped: gitleaks (not installed), osv-scanner (no lockfiles)", r.stdout)


class SkippedNeverCleanTest(SecretsCase):
    def test_r1128_skipped_tool_never_reports_clean(self):
        r = self.run_cli("secrets", self.project)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("result: skipped: gitleaks (not installed)", r.stdout)
        self.assertIn("NOT clean", r.stdout)
        self.assertNotIn("result: clean", r.stdout)

    def test_r1128_r2_install_hook_outside_the_project_is_refused_before_it_runs(self):
        outside = os.path.join(self.tmp, "other-project")
        os.makedirs(outside)
        r = self.run_cli("secrets", "--install-hook", outside)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)     # the project check runs before --install-hook
        self.assertIn("outside the project", r.stderr)

    def test_r1128_r2_vendor_scan_staged_treats_a_skipped_scan_as_not_clean(self):
        import vendor
        with self.assertRaises(sl.StudioError):
            with mock.patch("secretscan.scan", return_value=3):
                vendor.scan_staged(self.tmp)

    def test_r1128_r3_m4_staged_without_gitleaks_exits_0_with_one_install_line(self):
        self.git_init()
        r = self.run_cli("secrets", "--staged", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("staged: NOT scanned (gitleaks missing)", r.stdout)
        self.assertEqual(r.stdout.count("brew install"), 1, r.stdout)
        self.assertNotIn("result: clean", r.stdout)

    def test_r1128_r3_m4_full_scan_with_a_skipped_tool_still_exits_3_and_staged_clean_names_the_dependency_scan(self):
        self.assertEqual(self.run_cli("secrets", self.project).returncode, 3)
        self.git_init()
        self.stub("gitleaks", 0)
        r = self.run_cli("secrets", "--staged", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("staged: clean (dependency scan not part of --staged)", r.stdout)

    def test_r1128_r3_m4_the_release_gate_never_counts_a_skipped_staged_scan_as_clean(self):
        import secretscan
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("sys.stdout"):
                self.assertEqual(secretscan.scan(self.project, staged=True), 0)
                self.assertEqual(secretscan.scan(self.project, staged=True, strict=True), secretscan.SKIPPED_EXIT)

    def test_r1128_all_tools_ran_says_clean(self):
        self.stub("gitleaks", 0)
        r = self.run_cli("secrets", self.project)
        self.assertIn("result: clean", r.stdout)

    def test_r1128_secrets_prints_target_first_and_refuses_outside_the_project(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        self.stub("gitleaks", 0)
        r = self.run_cli("secrets", outside)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertTrue(r.stdout.startswith("budgit secrets: target folder "), r.stdout)
        self.assertIn("refused", r.stderr)
        self.assertIn("outside the project", r.stderr)
        self.assertEqual(self.calls_text(), "")
        inside = self.run_cli("secrets", self.project)
        self.assertEqual(inside.returncode, 0, inside.stderr)


class BoundSessionProjectFlagTest(SecretsCase):
    """R-1128 hotfix 1 (c): with a bound session, `--project` outside the session's project is refused, and that check runs
    before --install-hook. (The "no .budgit folder is no project" rule is hotfix 2.)"""
    SID = "sess-r1128-c"

    def bound(self, *args):
        self.init_project()
        os.makedirs(os.path.join(self.home, "sessions"), exist_ok=True)
        with open(sl.pointer_path(self.SID), "w") as f:
            json.dump({"project": self.project}, f)
        env = dict(os.environ, BUDGIT_HOME=self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin", CLAUDE_CODE_SESSION_ID=self.SID)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.project, env=env, capture_output=True, text=True)

    def test_r1128_c_project_flag_outside_the_session_project_is_refused(self):
        outside = os.path.join(self.tmp, "elsewhere-c")
        os.makedirs(outside)
        self.stub("gitleaks", 0)
        r = self.bound("secrets", "--project", outside)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("outside this session's project", r.stderr)
        self.assertEqual(self.calls_text(), "")

    def test_r1128_c_project_check_runs_before_install_hook(self):
        outside = os.path.join(self.tmp, "elsewhere-d")
        os.makedirs(os.path.join(outside, ".git", "hooks"))
        r = self.bound("secrets", "--install-hook", "--project", outside)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("outside this session's project", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(outside, ".git", "hooks", "pre-commit")))


class DenyAuditTest(SecretsCase):
    def deny_payload(self):
        cmd = "echo PASS > .budgit/verify/R-1.md # " + FAKE_TOKEN + " 4111222233334444"
        return cmd, {"tool_name": "Bash", "cwd": self.project, "tool_input": {"command": cmd}}

    def fire(self):
        import hook
        cmd, payload = self.deny_payload()
        hook._OUT.clear()
        hook.on_pre_tool_use(payload, self.project)
        decision = hook._OUT["hookSpecificOutput"]["permissionDecision"]
        hook._OUT.clear()
        return cmd, decision

    def log(self):
        return open(os.path.join(self.project, ".budgit", "audit.log"), encoding="utf-8").read().splitlines()

    def test_deny_logged_with_guard_hash_and_masked_head_never_the_command(self):
        self.init_project()
        cmd, decision = self.fire()
        self.assertEqual(decision, "deny")
        lines = [l for l in self.log() if " guard-deny " in l]
        self.assertEqual(len(lines), 1, self.log())
        line = lines[0]
        self.assertIn("verify-forgery", line)
        self.assertIn("Bash", line)
        self.assertIn("sha256=" + hashlib.sha256(cmd.encode()).hexdigest(), line)
        self.assertNotIn(FAKE_TOKEN, line)
        self.assertNotIn("4111", line)
        self.assertNotIn("x9x9", line)
        import hook
        self.assertEqual(hook.summary_events(self.log()), [])

    def test_studio_denies_summarises_per_guard_and_marks_false_positive(self):
        self.init_project()
        self.fire()
        self.fire()
        r = self.run_cli("denies", "--since", "7d")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("verify-forgery", r.stdout)
        self.assertIn("2", r.stdout)
        self.assertNotIn(FAKE_TOKEN, r.stdout)
        deny_id = [l for l in self.log() if " guard-deny " in l][0].split()[2]
        r = self.run_cli("denies", "--fp", deny_id, "--note", "echo into a doc, not a report")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any((" guard-deny-fp %s " % deny_id) in l for l in self.log()), self.log())
        r = self.run_cli("denies")
        self.assertIn("1 false positive", r.stdout)
        r = self.run_cli("denies", "--fp", "nope0000", "--note", "x")
        self.assertEqual(r.returncode, 1)

    def test_since_window_excludes_old_denies(self):
        self.init_project()
        sl.append_text(os.path.join(self.project, ".budgit", "audit.log"),
                       "2020-01-01T00:00:00.000Z guard-deny abcd1234 Bash raw-store sha256=%s cmd=\"x\" why=\"y\"\n"
                       % ("0" * 64))
        r = self.run_cli("denies", "--since", "7d")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("raw-store", r.stdout)
        r = self.run_cli("denies", "--since", "all")
        self.assertIn("raw-store", r.stdout)


class ScrubShortSecretsTest(SecretsCase):
    """R-55 / P5 r9 D43: short secrets in common shapes must not reach .budgit/audit.log (digits are masked anyway,
    letters were not)."""

    def scrub(self, text):
        import denylog
        return denylog.scrub(text, 200)

    def test_assignment_values_of_secret_looking_names_are_masked_whatever_their_length(self):
        for cmd, plain in (("PASS=hunterTwo", "hunterTwo"), ("DB_PASS=Tr0ub4dorX", "ub"), ("export API_KEY=abcd", "abcd"),
                           ("passwd=letmein ls", "letmein"), ("MY_SECRET='two words' ls", "words"),
                           ("GH_TOKEN=\"abc def\" cmd", "def"), ("Pwd=xyz curl x", "xyz"),
                           ("AUTH=basicZ", "basicZ"), ("OAUTH_CREDENTIALS=zed", "zed"),
                           ("curl --password=hunterTwo x", "hunterTwo"), ("monkey=banana", "banana")):
            with self.subTest(cmd=cmd):
                out = self.scrub(cmd)
                self.assertNotIn(plain, out)
                self.assertIn("[REDACTED]", out)

    def test_other_short_secret_shapes_are_masked(self):
        for cmd, plain in (("sshpass -p 'MyPassword!x' ssh h", "MyPassword"), ("mysql -u root -pS3cretPw db", "cretPw"),
                           ("mysql -u root -p S3cretPw db", "cretPw"),
                           ("curl https://user:pa55w0rdLong@host/x", "rdLong"),
                           ("psql postgres://u:hunterTwo@h/db", "hunterTwo")):
            with self.subTest(cmd=cmd):
                self.assertNotIn(plain, self.scrub(cmd))

    def test_quoted_values_with_spaces_and_cli_password_flags_are_masked(self):
        for cmd, plain in (('PASSWORD="two words" ls', "words"), ("TOKEN='a b c' cmd", "b c"),
                           ('DB_PASS="my long pass" x', "long"),
                           ("redis-cli -a hunterTwo ping", "hunterTwo"), ("redis-cli -h h -a 'two words' ping", "words"),
                           ("tool --pass hunterTwo x", "hunterTwo"), ("tool --password hunterTwo x", "hunterTwo"),
                           ("tool --password 'two words' x", "words"), ("tool --password=hunterTwo x", "hunterTwo"),
                           ('tool --password="two words" x', "words"), ("mysqldump -phunterTwo db", "hunterTwo"),
                           ("mariadb-dump -u r -phunterTwo db", "hunterTwo")):
            with self.subTest(cmd=cmd):
                out = self.scrub(cmd)
                self.assertNotIn(plain, out)
                self.assertIn("[REDACTED]", out)

    def test_everyday_text_is_left_alone(self):
        for cmd in ("echo PASSING grade", "git clone git@github.com:org/repo.git", "ls -p src", "KEYS ls",
                    "cat notes.txt", "export PATH=/usr/bin:$PATH", "ssh me@host"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.scrub(cmd), cmd.replace('"', "'"))


class SecurityReviewerAgentTest(StudioTestCase):
    def test_read_only_opus_gatekeeper_with_short_body_wired_into_lens(self):
        import budget
        text = open(os.path.join(ROOT, "plugin", "agents", "security-reviewer.md"), encoding="utf-8").read()
        _, front, body = text.split("---\n", 2)
        fields = dict(l.split(": ", 1) for l in front.strip().splitlines())
        self.assertEqual(fields["model"], "opus")
        self.assertEqual(fields["effort"], "high")
        self.assertEqual([t.strip() for t in fields["tools"].split(",")], ["Read", "Grep", "Glob", "Bash", "Skill"])   # R-1211: Skill only loads a skill, it writes nothing
        for banned in ("Write", "Edit", "WebFetch", "WebSearch"):
            self.assertIn(banned, fields["disallowedTools"])
        self.assertLessEqual(len(body.strip().splitlines()), 40)
        for word in ("budgit secrets", "HOLD", "PASS", "Evidence:", "Required before PASS:", "Keep:"):
            self.assertIn(word, body)
        self.assertEqual(budget.role_for("budgit:security-reviewer"), "judgement")
        lens = open(os.path.join(ROOT, "skills", "budgit-kickoff", "lenses", "security.md"), encoding="utf-8").read()
        self.assertIn("budgit:security-reviewer", lens)
