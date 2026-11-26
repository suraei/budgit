import importlib.machinery, importlib.util, json, os, socket, subprocess, sys, unittest
from unittest import mock
from helpers import StudioTestCase, ROOT

import auditmcp

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
SHA40 = "a" * 40
DIGEST = "sha256:" + "b" * 64

# secret values used by the fixtures; none of them may ever appear in any output
SECRETS = ["sk-live-9f8e7d6c5b4a3210zz", "hunter2pw", "ghp_ABCDEFGHIJKLMNOPQRSTUV1234", "tok_query_77aa88bb",
           "cli-flag-secret-4521", "Bearer-xyz-secret-99", "docker-env-secret-31"]


class AuditCase(StudioTestCase):
    def put(self, rel, data, base=None):
        path = os.path.join(base or self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def verdicts(self, servers, rel=".mcp.json"):
        """{server: verdict} for one .mcp.json holding the given servers."""
        self.put(rel, {"mcpServers": servers})
        return {f.server: f.verdict for f in auditmcp.scan([self.project])}

    def find(self, servers, name):
        self.put(".mcp.json", {"mcpServers": servers})
        return [f for f in auditmcp.scan([self.project]) if f.server == name][0]


class SelectorClassTest(AuditCase):
    def test_npx_pins(self):
        v = self.verdicts({
            "exact": {"command": "npx", "args": ["-y", "pkg@1.2.3"]},
            "scoped": {"command": "npx", "args": ["-y", "@scope/pkg@1.0.0"]},
            "prerelease": {"command": "npx", "args": ["pkg@1.0.0-beta.2"]},
            "caret": {"command": "npx", "args": ["-y", "pkg@^1"]},
            "tilde": {"command": "npx", "args": ["pkg@~1.2"]},
            "partial": {"command": "npx", "args": ["pkg@1"]},
            "scoped_caret": {"command": "npx", "args": ["@scope/pkg@^2.0.0"]},
            "bare": {"command": "npx", "args": ["-y", "pkg"]},
            "bare_scoped": {"command": "npx", "args": ["-y", "@scope/pkg"]},
            "latest": {"command": "npx", "args": ["-y", "pkg@latest"]},
            "next": {"command": "npx", "args": ["-y", "pkg@next"]},
            "full_path_cmd": {"command": "/usr/local/bin/npx", "args": ["pkg"]},
        })
        self.assertEqual(v, {"exact": "SAFE", "scoped": "SAFE", "prerelease": "SAFE", "caret": "MEDIUM",
                             "tilde": "MEDIUM", "partial": "MEDIUM", "scoped_caret": "MEDIUM", "bare": "HIGH",
                             "bare_scoped": "HIGH", "latest": "HIGH", "next": "HIGH", "full_path_cmd": "HIGH"})

    def test_dash_y_is_context_not_a_finding(self):
        f = self.find({"s": {"command": "npx", "args": ["-y", "pkg@1.2.3"]}}, "s")
        self.assertEqual(f.verdict, "SAFE")
        self.assertEqual(f.token, "pkg@1.2.3")
        self.assertEqual(f.command, "npx")
        self.assertNotIn("-y", f.why)

    def test_other_node_runners(self):
        v = self.verdicts({
            "bunx_ok": {"command": "bunx", "args": ["pkg@2.0.0"]},
            "bunx_bare": {"command": "bunx", "args": ["pkg"]},
            "pnpm_ok": {"command": "pnpm", "args": ["dlx", "pkg@2.0.0"]},
            "pnpm_bare": {"command": "pnpm", "args": ["dlx", "pkg"]},
            "npm_exec": {"command": "npm", "args": ["exec", "--yes", "pkg@3.1.0"]},
            "npx_p": {"command": "npx", "args": ["-p", "pkg@4.0.0", "bin"]},
            "cmd_wrap": {"command": "cmd", "args": ["/c", "npx", "-y", "pkg@1.0.0"]},
        })
        self.assertEqual(v, {"bunx_ok": "SAFE", "bunx_bare": "HIGH", "pnpm_ok": "SAFE", "pnpm_bare": "HIGH",
                             "npm_exec": "SAFE", "npx_p": "SAFE", "cmd_wrap": "SAFE"})

    def test_uvx_and_pipx(self):
        v = self.verdicts({
            "pin": {"command": "uvx", "args": ["pkg==1.0"]},
            "pin_extras": {"command": "uvx", "args": ["pkg[x]==1.0.2"]},
            "ge": {"command": "uvx", "args": ["pkg>=1"]},
            "compat": {"command": "uvx", "args": ["pkg~=1.2"]},
            "wild": {"command": "uvx", "args": ["pkg==1.*"]},
            "bare": {"command": "uvx", "args": ["pkg"]},
            "from_pin": {"command": "uvx", "args": ["--from", "pkg==2.0", "tool"]},
            "from_bare": {"command": "uvx", "args": ["--from", "pkg", "tool"]},
            "at_latest": {"command": "uvx", "args": ["pkg@latest"]},
            "pipx_pin": {"command": "pipx", "args": ["run", "pkg==1.0"]},
            "pipx_bare": {"command": "pipx", "args": ["run", "pkg"]},
        })
        self.assertEqual(v, {"pin": "SAFE", "pin_extras": "SAFE", "ge": "MEDIUM", "compat": "MEDIUM",
                             "wild": "MEDIUM", "bare": "HIGH", "from_pin": "SAFE", "from_bare": "HIGH",
                             "at_latest": "HIGH", "pipx_pin": "SAFE", "pipx_bare": "HIGH"})

    def test_git_urls_need_a_commit_sha(self):
        v = self.verdicts({
            "nosha": {"command": "uvx", "args": ["--from", "git+https://github.com/o/r", "tool"]},
            "branch": {"command": "uvx", "args": ["--from", "git+https://github.com/o/r@main", "tool"]},
            "sha": {"command": "uvx", "args": ["--from", "git+https://github.com/o/r@" + SHA40, "tool"]},
            "npm_git": {"command": "npx", "args": ["git+https://github.com/o/r.git"]},
            "npm_git_sha": {"command": "npx", "args": ["git+https://github.com/o/r.git#" + SHA40]},
            "npm_shorthand": {"command": "npx", "args": ["o/r"]},
            "tarball": {"command": "npx", "args": ["https://example.com/pkg-1.0.0.tgz"]},
        })
        self.assertEqual(v, {"nosha": "HIGH", "branch": "HIGH", "sha": "SAFE", "npm_git": "HIGH",
                             "npm_git_sha": "SAFE", "npm_shorthand": "HIGH", "tarball": "HIGH"})

    def test_docker_and_podman(self):
        v = self.verdicts({
            "tag": {"command": "docker", "args": ["run", "--rm", "-i", "image:tag"]},
            "notag": {"command": "docker", "args": ["run", "--rm", "-i", "image"]},
            "latest": {"command": "docker", "args": ["run", "-i", "image:latest"]},
            "digest": {"command": "docker", "args": ["run", "--rm", "-i", "image@" + DIGEST]},
            "tag_and_digest": {"command": "docker", "args": ["run", "-i", "ghcr.io/o/i:1.0@" + DIGEST]},
            "flags_with_values": {"command": "docker", "args": ["run", "--rm", "-i", "-v", "/a:/b", "--network",
                                                                "host", "--name=x", "reg.io/o/i@" + DIGEST]},
            "podman": {"command": "podman", "args": ["run", "--rm", "-i", "image:1"]},
        })
        self.assertEqual(v, {"tag": "HIGH", "notag": "HIGH", "latest": "HIGH", "digest": "SAFE",
                             "tag_and_digest": "SAFE", "flags_with_values": "SAFE", "podman": "HIGH"})
        f = self.find({"d": {"command": "docker", "args": ["run", "--rm", "-i", "image:tag"]}}, "d")
        self.assertEqual((f.command, f.token), ("docker", "image:tag"))

    def test_local_command_and_remote_url(self):
        v = self.verdicts({
            "local": {"command": "/opt/tools/my-server", "args": ["--port", "1"]},
            "node_script": {"command": "node", "args": ["./server.js"]},
            "https": {"type": "http", "url": "https://mcp.example.com/mcp"},
            "sse": {"type": "sse", "url": "https://mcp.example.com/sse"},
            "http": {"type": "http", "url": "http://mcp.example.com/mcp"},
            "loopback": {"type": "http", "url": "http://localhost:8080/mcp"},
        })
        self.assertEqual(v, {"local": "REVIEW", "node_script": "REVIEW", "https": "REVIEW", "sse": "REVIEW",
                             "http": "HIGH", "loopback": "REVIEW"})
        f = self.find({"local": {"command": "/opt/tools/my-server"}}, "local")
        self.assertEqual(f.command, "my-server")
        self.assertTrue(f.file.endswith(".mcp.json"))

    def test_worst_verdict_per_server_and_one_row_each(self):
        self.put(".mcp.json", {"mcpServers": {"a": {"command": "npx", "args": ["pkg@1.0.0"],
                                                    "env": {"API_KEY": SECRETS[0]}}}})
        got = auditmcp.scan([self.project])
        self.assertEqual([(f.server, f.verdict) for f in got], [("a", "HIGH")])
        self.assertIn("literal secret", got[0].why)


class FileFormatTest(AuditCase):
    def test_files_and_shapes_read(self):
        self.put(".mcp.json", {"mcpServers": {"root": {"command": "npx", "args": ["a@1.0.0"]}}})
        self.put(".claude/settings.json", {"permissions": {"allow": ["Bash(*)"]}})
        self.put(".claude/settings.local.json", {"enableAllProjectMcpServers": True})
        self.put(".claude-plugin/plugin.json",
                 {"mcpServers": {"inline": {"command": "npx", "args": ["b@1.0.0"]}}})
        self.put("plugin/.claude-plugin/plugin.json", {"mcpServers": "./servers.json"})
        self.put("plugin/servers.json", {"viapath": {"command": "npx", "args": ["c"]}})
        got = {(os.path.relpath(f.file, self.project), f.server): f.verdict for f in auditmcp.scan([self.project])}
        self.assertEqual(got[(".mcp.json", "root")], "SAFE")
        self.assertEqual(got[(".claude-plugin/plugin.json", "inline")], "SAFE")
        self.assertEqual(got[("plugin/servers.json", "viapath")], "HIGH")
        self.assertEqual(got[(".claude/settings.json", "-")], "MEDIUM")
        self.assertEqual(got[(".claude/settings.local.json", "-")], "HIGH")

    def test_plugin_mcp_path_cannot_escape_the_plugin_dir(self):
        outside = self.put("outside.json", {"evil": {"command": "npx", "args": ["x"]}}, base=self.tmp)
        self.put(".claude-plugin/plugin.json", {"mcpServers": "../outside.json"})
        got = auditmcp.scan([self.project])
        self.assertEqual([(f.verdict, f.server) for f in got], [("REVIEW", "-")])
        self.assertIn("outside", got[0].why)
        self.assertNotIn("evil", repr(got))              # the outside file was never read

    def test_missing_invalid_and_empty(self):
        self.assertEqual(auditmcp.scan([self.project]), [])
        self.put(".mcp.json", "{ not json")
        got = auditmcp.scan([self.project])
        self.assertEqual([f.verdict for f in got], ["REVIEW"])
        self.assertIn("not scanned", got[0].why)

    def test_symlink_and_big_files_skipped_but_reported(self):
        real = self.put("real.json", {"mcpServers": {"a": {"command": "npx", "args": ["pkg"]}}})
        os.symlink(real, os.path.join(self.project, ".mcp.json"))
        self.put(".claude/settings.json", " " * (auditmcp.MAX_BYTES + 1))
        got = auditmcp.scan([self.project])
        self.assertEqual(sorted(f.verdict for f in got), ["REVIEW", "REVIEW"])
        self.assertTrue(all("not scanned" in f.why for f in got))


class SettingsTest(AuditCase):
    def test_enable_all_project_servers_is_high(self):
        self.put(".claude/settings.json", {"enableAllProjectMcpServers": True})
        got = auditmcp.scan([self.project])
        self.assertEqual([f.verdict for f in got], ["HIGH"])
        self.assertIn("enableAllProjectMcpServers", got[0].why)
        self.put(".claude/settings.json", {"enableAllProjectMcpServers": False})
        self.assertEqual(auditmcp.scan([self.project]), [])

    def test_broad_allow_rules_are_medium(self):
        broad = ["Read(//**)", "Read(~/**)", "Bash(*)", "Bash", "Edit(/**)", "Write(/**)", "WebFetch"]
        fine = ["Read(./src/**)", "Bash(npm test:*)", "Edit(./docs/**)", "WebFetch(domain:example.com)"]
        self.put(".claude/settings.json", {"permissions": {"allow": broad + fine, "deny": ["Bash(*)"]}})
        got = auditmcp.scan([self.project])
        self.assertEqual(len(got), len(broad))
        self.assertTrue(all(f.verdict == "MEDIUM" for f in got))
        self.assertEqual(sorted(f.token for f in got), sorted(broad))


class SecretTest(AuditCase):
    def servers(self):
        return {
            "env": {"command": "npx", "args": ["pkg@1.0.0"], "env": {"API_KEY": SECRETS[0], "LOG_LEVEL": "debug",
                                                                       "FROM_ENV": "${REAL_KEY}"}},
            "header": {"type": "http", "url": "https://mcp.example.com/mcp",
                       "headers": {"Authorization": "Bearer " + SECRETS[5], "X-Ok": "1"}},
            "userinfo": {"command": "npx", "args": ["@scope/server-postgres@1.0.0",
                                                    "postgresql://u:%s@h/db" % SECRETS[1]]},
            "query": {"type": "sse", "url": "https://mcp.example.com/sse?token=%s&x=1" % SECRETS[3]},
            "flag_eq": {"command": "uvx", "args": ["pkg==1.0", "--api-key=" + SECRETS[4]]},
            "flag_sp": {"command": "uvx", "args": ["pkg==1.0", "--api-key", SECRETS[4]]},
            "pattern": {"command": "npx", "args": ["pkg@1.0.0"], "env": {"WHATEVER": SECRETS[2]}},
            "docker_env": {"command": "docker", "args": ["run", "-i", "--rm", "-e", "DB_PASSWORD=" + SECRETS[6],
                                                         "img@" + DIGEST]},
        }

    def test_each_shape_is_high_literal_secret(self):
        got = {f.server: f for f in (self._scan_secrets())}
        for name in self.servers():
            self.assertEqual(got[name].verdict, "HIGH", name)
            self.assertIn("literal secret", got[name].why, name)

    def _scan_secrets(self):
        self.put(".mcp.json", {"mcpServers": self.servers()})
        return auditmcp.scan([self.project])

    def test_only_key_names_are_reported(self):
        got = {f.server: f for f in self._scan_secrets()}
        self.assertIn("API_KEY", got["env"].why)
        self.assertNotIn("LOG_LEVEL", got["env"].why)
        self.assertIn("Authorization", got["header"].why)
        self.assertIn("token", got["query"].why)
        self.assertIn("--api-key", got["flag_eq"].why)
        self.assertIn("DB_PASSWORD", got["docker_env"].why)

    def test_env_references_and_plain_values_are_not_secrets(self):
        f = self.find({"s": {"command": "npx", "args": ["pkg@1.0.0"],
                             "env": {"API_KEY": "${MY_KEY}", "TOKEN": "$TOKEN", "MODE": "prod", "EMPTY": ""},
                             "headers": {"Authorization": "Bearer ${TOKEN}"}}}, "s")
        self.assertEqual(f.verdict, "SAFE")

    def test_values_never_reach_findings_or_report(self):
        got = self._scan_secrets()
        blob = repr(got) + auditmcp.report(got)
        for s in SECRETS:
            self.assertNotIn(s, blob)

    def test_cli_stdout_stderr_never_contain_secrets(self):
        self._scan_secrets()
        r = run_cli(self, "audit-mcp", self.project)
        self.assertEqual(r.returncode, 1, r.stderr)
        for s in SECRETS:
            self.assertNotIn(s, r.stdout)
            self.assertNotIn(s, r.stderr)
        self.assertIn("literal secret", r.stdout)

    def test_url_token_and_userinfo_stripped_from_the_token_column(self):
        got = {f.server: f for f in self._scan_secrets()}
        self.assertNotIn("u:", got["userinfo"].token)
        self.assertNotIn("token=", got["query"].token)


class NoValueEverPrintedTest(AuditCase):
    """D1: a unique canary injected in every place a value can sit must never reach any output."""

    CASES = {
        "npx_flag_value": {"command": "npx", "args": ["-y", "--api-key", "CANARY-01", "pkg@1.0.0"]},
        "npx_short_flag": {"command": "npx", "args": ["-k", "CANARY-02", "pkg@1.0.0"]},
        "npx_unknown_flag": {"command": "npx", "args": ["-y", "--foo", "CANARY-03", "pkg"]},
        "npx_flag_eq": {"command": "npx", "args": ["--token=CANARY-04", "pkg@1.0.0"]},
        "npx_after_pkg": {"command": "npx", "args": ["-y", "pkg@1.0.0", "--password", "CANARY-05"]},
        "bunx_flag": {"command": "bunx", "args": ["--secret", "CANARY-06", "pkg@1.0.0"]},
        "uvx_flag_value": {"command": "uvx", "args": ["--api-key", "CANARY-07", "pkg==1.0"]},
        "uvx_unknown_flag": {"command": "uvx", "args": ["--foo", "CANARY-08", "pkg"]},
        "uvx_from_url": {"command": "uvx", "args": ["--from", "git+https://u:CANARY-09@github.com/o/r", "t"]},
        "pipx_flag": {"command": "pipx", "args": ["run", "--foo", "CANARY-10", "pkg==1.0"]},
        "docker_log_opt": {"command": "docker", "args": ["run", "--log-opt", "splunk-token=CANARY-11", "img:1"]},
        "docker_env": {"command": "docker", "args": ["run", "-e", "DB_PASSWORD=CANARY-12", "img@" + DIGEST]},
        "docker_env_eq": {"command": "docker", "args": ["run", "--env=DB_PASSWORD=CANARY-13", "img@" + DIGEST]},
        "docker_unknown_flag": {"command": "docker", "args": ["run", "--weird", "CANARY-14", "img:1"]},
        "arg_url": {"command": "npx", "args": ["@s/server-postgres@1.0.0", "postgresql://u:CANARY-15@h/db"]},
        "arg_shape": {"command": "npx", "args": ["pkg@1.0.0", "sk_" + "live_" + "CANARY16abcdefghijklmnop"]},
        "pkg_is_shape": {"command": "npx", "args": ["sk-CANARY17abcdefghijklmnop"]},
        "env_named": {"command": "npx", "args": ["pkg@1.0.0"], "env": {"API_KEY": "CANARY-18"}},
        "env_number": {"command": "npx", "args": ["pkg@1.0.0"], "env": {"API_KEY": 918273645}},
        "header": {"type": "http", "url": "https://h.example.com/mcp", "headers": {"X-Api-Key": "CANARY-20"}},
        "url_userinfo": {"type": "sse", "url": "https://u:CANARY-21@h.example.com/sse"},
        "url_query": {"type": "sse", "url": "https://h.example.com/sse?api_key=CANARY-22"},
        "url_path_key": {"type": "sse", "url": "https://h.example.com/mcp/sk-CANARY23abcdefghijklmnop/sse"},
        "command_is_secret": {"command": "sk-CANARY24abcdefghijklmnop", "args": []},
        # round 2 (verifier N1/N8): the same leak class through reasons, tags and positional names
        "dk_glued_e": {"command": "docker", "args": ["run", "-eAPI_KEY=QCANARYA1", "img@" + DIGEST]},
        "dk_glued_e2": {"command": "docker", "args": ["run", "-eQCANARYA2", "img@" + DIGEST]},
        "dk_glued_v": {"command": "docker", "args": ["run", "-vQCANARYA3:/x", "img@" + DIGEST]},
        "dk_e_eq": {"command": "docker", "args": ["run", "-e=TOKEN=QCANARYA4", "img@" + DIGEST]},
        "dk_unknown_glued": {"command": "docker", "args": ["run", "-ZQCANARYA0=x", "img@" + DIGEST]},
        "dk_unknown_long_eq": {"command": "docker", "args": ["run", "--zz=QCANARYA0b", "img@" + DIGEST]},
        "npx_tag_shape": {"command": "npx", "args": ["-y", "pkg@sk-QCANARYA5abcdefghijklmnop"]},
        "npx_tag_plain": {"command": "npx", "args": ["-y", "pkg@QCANARYA6"]},
        "mcp_remote_hdr": {"command": "npx", "args": ["-y", "mcp-remote@0.1.0", "https://h.example.com/sse",
                                                      "--header", "Authorization: Bearer QCANARYA7"]},
        "b64_pos": {"command": "npx", "args": ["pkg@1.0.0", "tokenQCANARYA8xyz=="]},
        "pos_name_eq": {"command": "npx", "args": ["pkg@1.0.0", "authQCANARYA9=x"]},
        "py_glued": {"command": "uvx", "args": ["-pQCANARYB1", "pkg==1.0"]},
        "server_url_frag": {"type": "sse", "url": "https://h.example.com/sse#token=QCANARYB2"},
        "url_port_userinfo": {"type": "sse", "url": "https://QCANARYB3@h.example.com:8443/sse"},
        "cmd_path_secret": {"command": "/opt/QCANARYB4/run-sk-QCANARYB4abcdefghijklmnop", "args": []},
        "docker_image_userinfo": {"command": "docker", "args": ["run", "user:QCANARYB5@registry.example.com/img:1"]},
        "npx_pkg_url_token": {"command": "npx", "args": ["https://h.example.com/pkg.tgz?token=QCANARYB6"]},
        "npx_registry_auth": {"command": "npx", "args": ["--registry", "https://u:QCANARYB7@r.example.com", "pkg@1.0.0"]},
        "uvx_index": {"command": "uvx", "args": ["--index-url", "https://u:QCANARYB8@pypi.example.com/simple", "pkg==1.0"]},
        "cmd_c": {"command": "cmd", "args": ["/c", "npx", "-y", "--api-key", "QCANARYB9", "pkg@1.0.0"]},
        "docker_dash_e_bare": {"command": "docker", "args": ["run", "-e", "QCANARYC1", "img@" + DIGEST]},
        "uvx_with": {"command": "uvx", "args": ["--with", "https://u:QCANARYC2@h.example.com/x.whl", "pkg==1.0"]},
        "uvx_with_eq": {"command": "uvx", "args": ["--with=git+https://QCANARYC3@github.com/o/r", "pkg==1.0"]},
    }

    def test_every_case_no_canary_anywhere(self):
        self.put(".mcp.json", {"mcpServers": self.CASES})
        got = auditmcp.scan([self.project])
        blob = repr(got) + auditmcp.report(got)
        r = run_cli(self, "audit-mcp", self.project)
        blob += r.stdout + r.stderr
        self.assertNotIn("CANARY", blob.upper())
        self.assertNotIn("918273645", blob)
        self.assertEqual(len(got), len(self.CASES))

    def test_secret_cases_are_high(self):
        self.put(".mcp.json", {"mcpServers": self.CASES})
        v = {f.server: f.verdict for f in auditmcp.scan([self.project])}
        for name in ("npx_flag_value", "npx_flag_eq", "npx_after_pkg", "bunx_flag", "uvx_flag_value",
                     "docker_log_opt", "docker_env", "docker_env_eq", "arg_url", "arg_shape", "env_named",
                     "env_number", "header", "url_userinfo", "url_query", "url_path_key", "uvx_from_url"):
            self.assertEqual(v[name], "HIGH", name)

    def test_unknown_flag_means_unparsed_and_review(self):
        self.put(".mcp.json", {"mcpServers": self.CASES})
        got = {f.server: f for f in auditmcp.scan([self.project])}
        for name in ("npx_short_flag", "npx_unknown_flag", "uvx_unknown_flag", "pipx_flag", "docker_unknown_flag"):
            self.assertEqual((got[name].token, got[name].verdict), ("<unparsed>", "REVIEW"), name)
        self.assertEqual(got["npx_flag_value"].token, "<unparsed>")      # unknown flag + secret: still HIGH

    def test_reasons_never_carry_argument_text(self):
        """N1/N8: a reason names the flag (up to '=' or the letter after a single dash), never the argument."""
        def why(cfg):
            return auditmcp.classify(cfg)[3]
        self.assertEqual(why({"command": "docker", "args": ["run", "-Zbogus=QCANARY1", "img:1"]}),
                         ["unknown flag -Z: cannot tell which word is the image"])
        self.assertEqual(why({"command": "npx", "args": ["--zzflag", "QCANARY1", "pkg"]}),
                         ["unknown flag --zzflag: cannot tell which word is the package"])
        self.assertEqual(why({"command": "npx", "args": ["-Zbogus", "QCANARY1", "pkg"]}),
                         ["unknown flag -Z: cannot tell which word is the package"])
        self.assertEqual(why({"command": "uvx", "args": ["-pQCANARY2", "pkg==1.0"]}),
                         ["unknown flag -p: cannot tell which word is the package"])
        self.assertEqual(why({"command": "npx", "args": ["-y", "pkg@sk-QCANARY3abcdefghijklmnop"]})[-1], "floating tag")
        self.assertEqual(why({"command": "npx", "args": ["-y", "pkg@QCANARY4"]}), ["floating tag"])
        self.assertEqual(why({"command": "npx", "args": ["pkg@1.0.0", "tokenQCANARY5xyz=="]})[0],
                         "literal secret in positional arg #1")
        self.assertEqual(why({"command": "npx", "args": ["pkg@1.0.0", "authQCANARY6=x"]})[0],
                         "literal secret in positional arg #1")

    def test_glued_docker_value_flags_are_known(self):
        D = "img@" + DIGEST
        for args in (["run", "-eAPI_KEY=QCANARY7", D], ["run", "-e=TOKEN=QCANARY8", D], ["run", "-vQCANARY9:/x", D],
                     ["run", "-v=QCANARY9:/x", D], ["run", "-pQCANARY9:80", D]):
            base, token, verdict, whys = auditmcp.classify({"command": "docker", "args": args})
            self.assertEqual(token, D, args)
            self.assertNotIn("unknown flag", " ".join(whys), args)
        _, _, verdict, whys = auditmcp.classify({"command": "docker", "args": ["run", "-eAPI_KEY=QCANARY7", D]})
        self.assertEqual(verdict, "HIGH")
        self.assertEqual(whys[0], "literal secret in arg env API_KEY")
        _, _, verdict, whys = auditmcp.classify({"command": "docker", "args": ["run", "-e=TOKEN=QCANARY8", D]})
        self.assertEqual((verdict, whys[0]), ("HIGH", "literal secret in arg env TOKEN"))

    def test_known_flags_still_parse(self):
        v = self.verdicts({
            "a": {"command": "npx", "args": ["-y", "--prefer-offline", "--registry", "https://r.example", "pkg@1.0.0"]},
            "b": {"command": "uvx", "args": ["--quiet", "--python", "3.12", "pkg==1.0"]},
            "c": {"command": "docker", "args": ["run", "-it", "--rm", "--init", "--log-opt", "x=y", "img@" + DIGEST]},
        })
        self.assertEqual(v, {"a": "SAFE", "b": "SAFE", "c": "SAFE"})


class HomeTest(AuditCase):
    def setUp(self):
        super().setUp()
        self.fake_home = os.path.join(self.tmp, "fakehome")
        self._old = os.environ.get("HOME")
        os.environ["HOME"] = self.fake_home
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old

    def h(self, rel):
        return os.path.join(self.fake_home, rel)

    def build_home(self):
        srv = lambda p: {"command": "npx", "args": [p]}
        self.put(".claude/settings.json", {"enableAllProjectMcpServers": True}, base=self.fake_home)
        self.put(".claude.json", {"mcpServers": {"top": srv("a")},
                                  "projects": {"/x": {"mcpServers": {"proj": srv("b@1.0.0")}},
                                               "/y": {"mcpServers": {"proj2": srv("c")}}},
                                  "history": ["not scanned"]}, base=self.fake_home)
        self.put(".claude/.credentials.json", {"token": "CRED-SECRET-VALUE"}, base=self.fake_home)
        self.put(".claude/other.json", {"mcpServers": {"nope": srv("zzz")}}, base=self.fake_home)
        p1 = os.path.join(self.fake_home, ".claude", "plugins", "cache", "m", "p1", "1.0")
        p2 = os.path.join(self.fake_home, ".claude", "plugins", "cache", "m", "p2", "2.0")
        self.put(".mcp.json", {"mcpServers": {"p1srv": srv("d")}}, base=p1)
        self.put(".claude-plugin/plugin.json", {"mcpServers": {"p2srv": srv("e@1.0.0")}}, base=p2)
        self.put(".claude/plugins/installed_plugins.json",
                 {"version": 2, "plugins": {"p1@m": [{"installPath": p1, "version": "1.0"}],
                                            "p2@m": [{"installPath": p2, "version": "2.0"}]}}, base=self.fake_home)
        return p1, p2

    def test_home_is_off_by_default(self):
        self.build_home()
        self.assertEqual(auditmcp.scan([self.project]), [])

    def test_home_adds_the_fixed_list(self):
        self.build_home()
        got = {f.server: f.verdict for f in auditmcp.scan([self.project], home=True)}
        self.assertEqual(got, {"-": "HIGH", "top": "HIGH", "proj": "SAFE", "proj2": "HIGH", "p1srv": "HIGH",
                               "p2srv": "SAFE"})

    def test_only_the_fixed_paths_are_ever_opened(self):
        p1, p2 = self.build_home()
        self.put(".mcp.json", {"mcpServers": {"proj0": {"command": "npx", "args": ["q"]}}})
        os.symlink(self.h(".claude/other.json"), os.path.join(self.project, ".claude-plugin-link"))
        opened = []

        def spy(path, *a, **k):
            opened.append(os.path.realpath(path))
            return open(path, *a, **k)

        auditmcp.scan([self.project], home=True, opener=spy)
        want = [self.project + "/.mcp.json",
                self.h(".claude/settings.json"), self.h(".claude.json"),
                self.h(".claude/plugins/installed_plugins.json"),
                p1 + "/.mcp.json", p2 + "/.claude-plugin/plugin.json"]
        self.assertEqual(sorted(set(opened)), sorted(os.path.realpath(p) for p in want))
        for p in opened:
            self.assertNotIn(".credentials", p)
            self.assertNotIn("other.json", p)

    def test_credentials_file_never_opened_via_real_open(self):
        self.build_home()
        real_open = open
        seen = []

        def spy(path, *a, **k):
            seen.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", spy):
            auditmcp.scan([self.project], home=True)
        self.assertTrue(seen)
        self.assertFalse([p for p in seen if ".credentials" in p or "other.json" in p])

    def test_home_symlinked_file_skipped(self):
        self.build_home()
        os.remove(self.h(".claude/settings.json"))
        os.symlink(self.h(".claude.json"), self.h(".claude/settings.json"))
        got = auditmcp.scan([self.project], home=True)
        self.assertTrue([f for f in got if "symlink" in f.why])


class SafetyTest(AuditCase):
    def test_never_executes_and_never_touches_the_network(self):
        self.put(".mcp.json", {"mcpServers": {
            "a": {"command": "npx", "args": ["pkg"]},
            "b": {"type": "http", "url": "http://mcp.example.com/x"},
            "c": {"command": "docker", "args": ["run", "image"]}}})

        def boom(*a, **k):
            raise AssertionError("must not run or connect")

        with mock.patch.object(socket, "socket", boom), mock.patch.object(socket, "create_connection", boom), \
                mock.patch.object(socket, "getaddrinfo", boom), mock.patch.object(subprocess, "Popen", boom), \
                mock.patch.object(os, "system", boom), mock.patch.object(os, "execv", boom):
            got = auditmcp.scan([self.project])
            auditmcp.report(got)
        self.assertEqual(len(got), 3)


def run_cli(case, *args, cwd=None, extra_env=None):
    env = dict(os.environ, BUDGIT_HOME=case.home)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, CLI] + list(args), cwd=cwd or case.project, env=env,
                          capture_output=True, text=True)


class ReportAndCliTest(AuditCase):
    def test_exit_codes(self):
        self.put(".mcp.json", {"mcpServers": {"ok": {"command": "npx", "args": ["pkg@1.0.0"]},
                                              "med": {"command": "npx", "args": ["pkg@^1"]}}})
        r = run_cli(self, "audit-mcp", self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SAFE", r.stdout)
        self.assertIn("MEDIUM", r.stdout)
        self.put(".mcp.json", {"mcpServers": {"bad": {"command": "npx", "args": ["pkg"]}}})
        r = run_cli(self, "audit-mcp", self.project)
        self.assertEqual(r.returncode, 1)
        self.assertIn("HIGH", r.stdout)

    def test_enable_all_exits_1(self):
        self.put(".claude/settings.json", {"enableAllProjectMcpServers": True})
        self.assertEqual(run_cli(self, "audit-mcp", self.project).returncode, 1)

    def test_default_path_is_cwd_and_footer_present(self):
        self.put(".mcp.json", {"mcpServers": {"ok": {"command": "npx", "args": ["pkg@1.0.0"]}}})
        r = run_cli(self, "audit-mcp")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SAFE means pinned, not verified (no integrity/provenance check)", r.stdout)
        for col in ("FILE", "SERVER", "COMMAND", "PACKAGE", "VERDICT", "WHY"):
            self.assertIn(col, r.stdout)

    def test_no_findings_still_prints_footer_and_exits_0(self):
        r = run_cli(self, "audit-mcp", self.project)
        self.assertEqual(r.returncode, 0)
        self.assertIn("no MCP", r.stdout)
        self.assertIn("SAFE means pinned", r.stdout)

    def test_output_capped_at_60_lines_with_more_marker(self):
        servers = {"s%03d" % i: {"command": "npx", "args": ["p%d" % i]} for i in range(150)}
        servers["safe_one"] = {"command": "npx", "args": ["p@1.0.0"]}
        self.put(".mcp.json", {"mcpServers": servers})
        r = run_cli(self, "audit-mcp", self.project)
        lines = r.stdout.rstrip("\n").split("\n")
        self.assertLessEqual(len(lines), 60)
        self.assertTrue([ln for ln in lines if ln.startswith("+") and "more" in ln])
        self.assertNotIn("safe_one", r.stdout)               # worst first: the cap hides SAFE, not HIGH
        self.assertEqual(r.returncode, 1)
        self.assertIn("151 servers", r.stdout)                # the summary counts everything, not just shown rows

    def test_report_is_sanitised(self):
        self.put(".mcp.json", {"mcpServers": {"zq\u202ezq\nname": {"command": "npx", "args": ["pkg"]}}})
        out = auditmcp.report(auditmcp.scan([self.project]))
        self.assertNotIn("\u202e", out)
        self.assertEqual(len([ln for ln in out.split("\n") if "zq" in ln]), 1)

    def test_home_flag_via_cli(self):
        home = os.path.join(self.tmp, "fh")
        self.put(".claude/settings.json", {"enableAllProjectMcpServers": True}, base=home)
        r = run_cli(self, "audit-mcp", "--home", self.project, extra_env={"HOME": home})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("settings.json", r.stdout)
        r = run_cli(self, "audit-mcp", self.project, extra_env={"HOME": home})
        self.assertEqual(r.returncode, 0)

    def test_perf(self):
        import time
        self.put(".mcp.json", {"mcpServers": {"s%d" % i: {"command": "npx", "args": ["p@1.0.%d" % i]}
                                              for i in range(300)}})
        t = time.process_time()                                          # CPU time, not wall (R-1282)
        auditmcp.report(auditmcp.scan([self.project]))
        self.assertLess(time.process_time() - t, 2.0)


class HintTest(AuditCase):
    """`budgit new` / `budgit activate` print one line only when HIGH findings exist."""

    def test_activate_prints_hint_only_with_high(self):
        r = run_cli(self, "activate", self.project, extra_env={"CLAUDE_CODE_SESSION_ID": "s1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("audit-mcp", r.stdout)
        self.put(".mcp.json", {"mcpServers": {"a": {"command": "npx", "args": ["pkg"]},
                                              "b": {"command": "npx", "args": ["pkg@^1"]},
                                              "c": {"command": "uvx", "args": ["pkg"]}}})
        r = run_cli(self, "activate", self.project, extra_env={"CLAUDE_CODE_SESSION_ID": "s1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("audit-mcp: 2 HIGH findings \u2014 run: budgit audit-mcp", r.stdout)

    def test_hint_can_never_break_new_or_activate(self):
        loader = importlib.machinery.SourceFileLoader("studio_cli", CLI)
        spec = importlib.util.spec_from_loader("studio_cli", loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        with mock.patch.object(auditmcp, "scan", side_effect=RuntimeError("boom")):
            cli._audit_hint(self.project)                    # must not raise
        with mock.patch.object(auditmcp, "scan", side_effect=RecursionError()):
            cli._audit_hint(self.project)


if __name__ == "__main__":
    unittest.main()
