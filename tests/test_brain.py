"""P3 extended brain: typed [[links]] between notes, lint + small graph queries (read-only)."""
import hashlib, json, os, subprocess, sys, time, unicodedata
from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def note(status=None, aliases=None, body="", title=None):
    fm = ["---", "type: research"]
    if status:
        fm.append("status: %s" % status)
    if aliases:
        fm.append("aliases: [%s]" % ", ".join(aliases))
    fm.append("---")
    return "\n".join(fm) + "\n# %s\n\n%s\n" % (title or "Note", body)


class BrainCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.project, ".studio"))

    def put(self, rel, text):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def brain(self):
        import brain
        return brain.load(self.project)

    def errors(self, only=None):
        import brain
        return [i for i in brain.lint(self.brain(), only) if i.level == "ERROR"]

    def classes(self, only=None):
        return sorted({i.cls for i in self.errors(only)})

    def cli(self, *args):
        return subprocess.run([sys.executable, CLI, "brain"] + list(args) + ["--repo", self.project],
                              capture_output=True, text=True, env=dict(os.environ, STUDIO_HOME=self.home))


class ParseTest(BrainCase):
    def test_typed_edges_only_under_relations_heading(self):
        self.put("research/a.md", note(body="See [[b]] for context.\n- Added [[c]] to roster\n\n"
                                            "## Relations\n- depends_on [[b]] (uses its numbers)\n"
                                            "- supports [[c]]\n\n## Next\n- more [[b]]\n"))
        self.put("research/b.md", note())
        self.put("research/c.md", note())
        a = self.brain().notes["a"]
        self.assertEqual(sorted((e.type, e.target) for e in a.edges), [("depends_on", "b"), ("supports", "c")])
        self.assertEqual(sorted(l.target for l in a.links), ["b", "b", "c"])
        self.assertEqual(self.errors(), [])

    def test_label_heading_and_case_resolve_to_the_note(self):
        self.put("lessons/L-001-cross-check.md", note(aliases=["cross check", "Comprobación"]))
        self.put("docs/x.md", note(body="[[l-001-CROSS-check|the lesson]] [[L-001-cross-check#Why]] "
                                        "[[comprobación]] [[Cross Check]]"))
        b = self.brain()
        self.assertEqual({b.resolve(l.target)[0] for l in b.notes["x"].links}, {"l-001-cross-check"})
        self.assertEqual(self.errors(), [])

    def test_block_list_aliases_and_id_resolve(self):
        self.put("lessons/L-002-ab.md", "---\nid: L-002\naliases:\n  - A/B test\n  - \"medir antes\"\nstatus: candidate\n---\n# AB\n")
        self.put("docs/x.md", note(body="[[L-002]] [[a/b test]] [[Medir antes]]"))
        b = self.brain()
        self.assertEqual({b.resolve(l.target)[0] for l in b.notes["x"].links}, {"l-002-ab"})
        self.assertEqual(self.errors(), [])

    def test_code_and_frontmatter_links_make_no_edge(self):
        self.put("docs/x.md", "---\ntype: plan\nrelated: \"[[ghost]]\"\n---\n# X\n\n```md\n## Relations\n"
                              "- supersedes [[ghost]]\n```\n~~~\n[[ghost]]\n~~~\nInline `[[ghost]]` here.\n")
        n = self.brain().notes["x"]
        self.assertEqual((n.edges, n.links), ([], []))
        self.assertEqual(self.errors(), [])

    def test_path_style_target_resolves(self):
        self.put("lessons/INDEX.md", note())
        self.put("research/INDEX.md", note())
        self.put("docs/x.md", note(body="[[lessons/INDEX]]"))
        b = self.brain()
        self.assertEqual(b.resolve("lessons/INDEX")[0], "lessons/index")
        self.assertEqual(self.errors(), [])

    def test_roots_from_project_json_and_dot_dirs_skipped(self):
        self.put(".studio/project.json", json.dumps({"brain": {"roots": ["notes"]}}))
        self.put("notes/a.md", note())
        self.put("notes/.obsidian/x.md", note())
        self.put("notes/.trash/y.md", note())
        self.put("research/ignored.md", note())
        self.assertEqual(sorted(self.brain().notes), ["a"])


class LintTest(BrainCase):
    def test_unknown_type(self):
        self.put("docs/a.md", note(body="## Relations\n- extends [[b]]\n"))
        self.put("docs/b.md", note())
        self.assertEqual(self.classes(), ["unknown-type"])

    def test_unresolved_typed_and_untyped(self):
        self.put("docs/a.md", note(body="[[nope]]\n\n## Relations\n- supports [[gone]]\n"))
        errs = self.errors()
        self.assertEqual([e.cls for e in errs], ["unresolved", "unresolved"])
        self.assertEqual([e.line for e in errs], [6, 9])
        self.assertTrue(all(e.rel == "docs/a.md" for e in errs))

    def test_ambiguous_target(self):
        self.put("lessons/README.md", note())
        self.put("research/README.md", note())
        self.put("docs/a.md", note(body="[[README]]"))
        self.assertEqual(self.classes(), ["ambiguous"])

    def test_self_edge(self):
        self.put("docs/a.md", note(body="## Relations\n- supports [[a]]\n"))
        self.assertEqual(self.classes(), ["self-edge"])

    def test_relation_line_outside_relations(self):
        self.put("docs/a.md", note(body="- depends_on [[b]] (stray)\n"))
        self.put("docs/b.md", note())
        self.assertEqual(self.classes(), ["outside-relations"])

    def test_second_relations_heading(self):
        self.put("docs/a.md", note(body="## Relations\n- supports [[b]]\n## Relations\n- supports [[b]]\n"))
        self.put("docs/b.md", note())
        self.assertEqual(self.classes(), ["duplicate-relations"])

    def test_supersedes_cycle_is_an_error_not_a_hang(self):
        self.put("docs/a.md", note(status="superseded", body="## Relations\n- supersedes [[b]]\n"))
        self.put("docs/b.md", note(status="superseded", body="## Relations\n- supersedes [[a]]\n"))
        self.assertIn("supersedes-cycle", self.classes())
        r = self.cli("current", "a")
        self.assertNotEqual(r.returncode, 0)

    def test_supersedes_target_must_say_superseded(self):
        self.put("docs/new.md", note(status="active", body="## Relations\n- supersedes [[old]]\n"))
        self.put("docs/old.md", note(status="active"))
        self.assertEqual(self.classes(), ["supersedes-status"])
        self.put("docs/old.md", note(status="superseded"))
        self.assertEqual(self.classes(), [])

    def test_embed_and_block_ref(self):
        self.put("docs/a.md", note(body="![[b]]\n[[b#^abc]]\n"))
        self.put("docs/b.md", note())
        self.assertEqual(self.classes(), ["block-ref", "embed"])

    def test_non_nfc_filename(self):
        self.put(unicodedata.normalize("NFD", "docs/comprobación.md"), note())
        names = os.listdir(os.path.join(self.project, "docs"))
        if all(unicodedata.is_normalized("NFC", n) for n in names):
            self.skipTest("filesystem normalised the name")
        self.assertEqual(self.classes(), ["non-nfc-name"])

    def test_unclosed_frontmatter(self):
        self.put("docs/a.md", "---\ntype: plan\n# A\nbody\n")
        self.assertEqual(self.classes(), ["unclosed-frontmatter"])

    def test_stem_and_alias_collisions_but_index_readme_exempt(self):
        self.put("research/plan.md", note())
        self.put("docs/Plan.md", note())
        self.put("lessons/INDEX.md", note()); self.put("research/INDEX.md", note())
        self.put("docs/README.md", note()); self.put("radar/README.md", note())
        self.assertEqual(self.classes(), ["collision"])
        os.remove(os.path.join(self.project, "docs", "Plan.md"))
        self.put("docs/other.md", note(aliases=["PLAN"]))
        self.assertEqual(self.classes(), ["collision"])

    def test_warnings_do_not_fail(self):
        import brain
        self.put("docs/a.md", "# A\n\nowner:: me\n")
        issues = brain.lint(self.brain())
        self.assertEqual(sorted(i.cls for i in issues), ["dataview-field", "no-frontmatter", "orphan"])
        self.assertTrue(all(i.level == "WARN" for i in issues))
        r = self.cli("lint")
        self.assertEqual(r.returncode, 0)
        self.assertIn("3 warnings", r.stdout)
        self.assertNotIn("orphan", r.stdout)
        self.assertIn("orphan", self.cli("lint", "--verbose").stdout)

    def test_dataview_prose_is_not_a_field(self):
        import brain
        self.put("docs/a.md", note(body="Classifier `Development Status :: 4` and Development Status :: 4 - Beta\n"))
        self.assertNotIn("dataview-field", [i.cls for i in brain.lint(self.brain())])

    def test_only_one_note(self):
        self.put("docs/a.md", note(body="[[nope]]"))
        self.put("docs/b.md", note(body="[[nope]]"))
        self.assertEqual([e.rel for e in self.errors(only=os.path.join(self.project, "docs/b.md"))], ["docs/b.md"])

    def test_cli_names_file_line_class_and_exits_1(self):
        self.put("docs/a.md", note(body="[[nope]]"))
        r = self.cli("lint")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("docs/a.md:6: ERROR unresolved", r.stdout)


class QueryTest(BrainCase):
    def chain(self):
        self.put("docs/a.md", note(status="superseded", title="Alpha"))
        self.put("docs/b.md", note(status="superseded", title="Beta", body="## Relations\n- supersedes [[a]]\n"))
        self.put("docs/c.md", note(status="active", title="Gamma", body="## Relations\n- supersedes [[b]]\n"))

    def test_current_follows_supersedes_to_the_head(self):
        import brain
        self.chain()
        b = self.brain()
        self.assertEqual(brain.current(b, "a"), ["c"])
        self.assertEqual(brain.current(b, "b"), ["c"])
        self.assertEqual(brain.current(b, "c"), ["c"])
        r = self.cli("current", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Gamma", r.stdout)
        self.assertIn("docs/c.md", r.stdout)

    def test_impact_is_two_hops(self):
        import brain
        self.put("docs/a.md", note())
        self.put("docs/b.md", note(body="## Relations\n- derived_from [[a]]\n"))
        self.put("docs/c.md", note(body="## Relations\n- depends_on [[b]]\n"))
        self.put("docs/d.md", note(body="## Relations\n- implements [[c]]\n"))
        self.put("docs/e.md", note(body="## Relations\n- supports [[a]]\n"))
        self.assertEqual(brain.impact(self.brain(), "a"), ["b", "c"])

    def test_backlinks_typed_and_untyped(self):
        import brain
        self.put("docs/a.md", note())
        self.put("docs/b.md", note(body="## Relations\n- supports [[a]]\n"))
        self.put("docs/c.md", note(body="see [[a]]"))
        self.assertEqual(brain.backlinks(self.brain(), "a"), [("b", "supports"), ("c", None)])

    def test_index_lists_notes_to_stdout_only(self):
        self.put("lessons/INDEX.md", "# Hand-made index\n")
        self.put("lessons/L-001.md", note(title="Cross-check"))
        before = open(os.path.join(self.project, "lessons/INDEX.md")).read()
        r = self.cli("index", "lessons")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[[L-001]] — Cross-check", r.stdout)
        self.assertEqual(open(os.path.join(self.project, "lessons/INDEX.md")).read(), before)

    def test_unknown_note_exits_1(self):
        self.put("docs/a.md", note())
        r = self.cli("current", "zzz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no note", r.stderr + r.stdout)

    def test_output_capped(self):
        self.put("docs/hub.md", note())
        for i in range(200):
            self.put("docs/n%03d-a-rather-long-file-name-for-the-cap.md" % i, note(body="[[hub]]"))
        r = self.cli("backlinks", "hub")
        self.assertLessEqual(len(r.stdout.encode()), 1600)
        self.assertIn("more", r.stdout)

    def test_queries_are_logged_one_line_each(self):
        self.chain()
        self.cli("current", "a"); self.cli("current", "c"); self.cli("backlinks", "a")
        with open(os.path.join(self.project, ".studio", "brain-queries.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual([r["sub"] for r in rows], ["current", "current", "backlinks"])
        self.assertEqual(set(rows[0]), {"ts", "session", "sub", "args", "n", "bytes", "redirected"})
        self.assertEqual([r["redirected"] for r in rows], [True, False, False])

    def test_query_log_is_gitignored(self):
        with open(os.path.join(ROOT, ".gitignore")) as f:
            ignored = f.read().split()
        for entry in (".studio/brain-queries.jsonl", ".obsidian/workspace*.json", ".trash/"):
            self.assertIn(entry, ignored)

    def test_no_brain_command_modifies_a_note(self):
        self.chain()
        self.put("docs/x.md", note(body="[[nope]]\nowner:: me"))
        def snap():
            out = {}
            for d, _, fs in os.walk(os.path.join(self.project, "docs")):
                for f in fs:
                    p = os.path.join(d, f)
                    out[p] = (os.stat(p).st_mtime_ns, hashlib.sha256(open(p, "rb").read()).hexdigest())
            return out
        before = snap()
        for args in (("lint",), ("current", "a"), ("impact", "a"), ("backlinks", "a"), ("index",)):
            self.cli(*args)
        self.assertEqual(snap(), before)


class CorpusTest(BrainCase):
    def test_this_repo_lints_clean_and_fast(self):
        # bound on our own computation: CPU time of the child (RUSAGE_CHILDREN delta), not wall seconds, so a busy machine cannot fail it
        import resource
        def child_cpu():
            u = resource.getrusage(resource.RUSAGE_CHILDREN)
            return u.ru_utime + u.ru_stime
        c0 = child_cpu()
        r = subprocess.run([sys.executable, CLI, "brain", "lint", "--repo", ROOT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(child_cpu() - c0, 1.5)

    def test_seed_relations_answer_queries_here(self):
        import brain
        b = brain.load(ROOT)
        typed = [e for n in b.notes.values() for e in n.edges]
        self.assertGreaterEqual(len(typed), 8)
        # no note in the corpus truly supersedes another (one even says "supersedes: nothing"),
        # so the seed has no supersedes edge; current() is covered by the synthetic chain tests
        self.assertTrue(brain.backlinks(b, "2026-10-19-team-upgrade-proposal"))
        dep = [e.target for e in typed if e.type in ("depends_on", "derived_from", "implements")]
        self.assertTrue(any(brain.impact(b, t) for t in dep))

    def test_1000_notes_lint_under_1s(self):
        import brain
        for i in range(1000):
            self.put("research/n%04d.md" % i,
                     note(body="Text [[n%04d]] more.\n\n## Relations\n- depends_on [[n%04d]] (x)\n"
                               % ((i + 1) % 1000, (i + 2) % 1000)))
        t = time.process_time()      # CPU time of this process, not wall seconds (same bound, immune to machine load)
        issues = brain.lint(brain.load(self.project))
        self.assertLess(time.process_time() - t, 1.0)
        self.assertEqual([i for i in issues if i.level == "ERROR"], [])


class HookTest(BrainCase):
    def setUp(self):
        super().setUp()
        self.put(".studio/project.json", json.dumps({"brain": {"roots": ["docs"]}}))

    def run_hook(self, path):
        import hook

        inp = {"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": path},
               "cwd": self.project}
        hook._OUT.clear()
        hook.on_post_tool_use(inp, self.project)
        return (hook._OUT.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def test_edit_inside_root_injects_only_that_notes_errors(self):
        self.put("docs/other.md", note(body="[[ghost-other]]"))
        p = self.put("docs/a.md", note(body="[[ghost]]"))
        t = time.process_time()                                          # CPU time, not wall (R-1282)
        out = self.run_hook(p)
        self.assertLess(time.process_time() - t, 0.3)
        self.assertIn("ghost", out)
        self.assertIn("docs/a.md", out)
        self.assertNotIn("ghost-other", out)

    def test_clean_note_or_outside_root_is_silent(self):
        p = self.put("docs/a.md", note())
        self.assertEqual(self.run_hook(p), "")
        q = self.put("src/readme.md", note(body="[[ghost]]"))
        self.assertEqual(self.run_hook(q), "")
        self.assertEqual(self.run_hook(os.path.join(self.project, "docs", "x.py")), "")

    def test_project_without_brain_roots_is_silent(self):
        os.remove(os.path.join(self.project, ".studio", "project.json"))
        p = self.put("docs/a.md", note(body="[[ghost]]"))
        self.assertEqual(self.run_hook(p), "")

    def test_hooks_json_matches_edits(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")) as f:
            post = json.load(f)["hooks"]["PostToolUse"]
        matchers = "|".join(h.get("matcher", "") for h in post).split("|")
        for tool in ("Edit", "Write", "MultiEdit"):
            self.assertIn(tool, matchers)

    def test_session_pointer_only_with_brain_roots(self):
        import brain
        self.assertIn("budgit brain", brain.session_pointer(self.project))
        os.remove(os.path.join(self.project, ".studio", "project.json"))
        self.assertEqual(brain.session_pointer(self.project), "")


class VerifierRound1Test(BrainCase):
    """R-47-P3 verifier round 1 defects."""
    def test_index_output_capped(self):
        for i in range(300):
            self.put("docs/n%03d-long-name-for-the-index-cap.md" % i, note(title="Title %d" % i))
        r = self.cli("index")
        self.assertLessEqual(len(r.stdout.encode()), 1600)
        self.assertIn("more", r.stdout)

    def test_longer_fence_wraps_shorter_one(self):
        self.put("docs/a.md", note(body="````md\n```\n[[ghost1]]\n```\n[[ghost2]]\n````\n~~~~\n~~~\n[[ghost3]]\n~~~~\n"))
        n = self.brain().notes["a"]
        self.assertEqual(n.links, [])

    def test_bom_keeps_frontmatter(self):
        self.put("docs/a.md", chr(0xFEFF) + note(status="active"))
        n = self.brain().notes["a"]
        self.assertEqual(n.status, "active")
        self.assertNotIn("no-frontmatter", [i.cls for i in n.issues])

    def test_html_comments_and_indented_code_make_no_link(self):
        self.put("docs/a.md", note(body="<!-- [[ghost1]] -->\n<!--\n[[ghost2]]\n-->\nText.\n\n    [[ghost3]]\n    more\n\n"
                                        "- item\n\n    [[b]] continues the list\n"))
        self.put("docs/b.md", note())
        self.assertEqual([l.target for l in self.brain().notes["a"].links], ["b"])

    def test_lint_from_subfolder_finds_the_repo(self):
        self.put("docs/a.md", note(body="[[nope]]"))
        sub = os.path.join(self.project, "docs")
        r = subprocess.run([sys.executable, CLI, "brain", "lint"], cwd=sub, capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("docs/a.md:6", r.stdout)

    def test_no_notes_is_not_green(self):
        r = self.cli("lint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no notes", r.stdout + r.stderr)


class VerifierRound2Test(BrainCase):
    def test_fence_indented_under_list_items(self):
        self.put("docs/a.md", note(body="- item\n    ```\n    [[ghostL4]]\n    ```\n- a\n  - nested\n        ```\n"
                                        "        [[ghostN]]\n        ```\n"))
        self.assertEqual(self.brain().notes["a"].links, [])


class VerifierRound3Test(BrainCase):
    def test_fence_inside_blockquote(self):
        self.put("docs/a.md", note(body="> quote\n> ```\n> [[ghostBQ]]\n> ```\n"))
        self.assertEqual(self.brain().notes["a"].links, [])

    def test_fence_inside_callout(self):
        self.put("docs/a.md", note(body="> [!note] Title\n> ```md\n> [[ghostCO]]\n> ```\n>> ```\n>> [[ghostNested]]\n>> ```\n"))
        self.assertEqual(self.brain().notes["a"].links, [])

    def test_one_line_backtick_run_is_not_a_fence(self):
        # CommonMark: a backtick fence's info string may not contain backticks, so this is inline code
        self.put("docs/b.md", note())
        self.put("docs/a.md", note(body="```js const x = 1```\n\n## Relations\n- depends_on [[b]]\n"))
        self.assertEqual([e.target for e in self.brain().notes["a"].edges], ["b"])

    def test_fence_marker_inside_indented_code_does_not_open(self):
        self.put("docs/b.md", note())
        self.put("docs/a.md", note(body="text\n\n    ```\n    code\n\n## Relations\n- depends_on [[b]]\n"))
        self.assertEqual([e.target for e in self.brain().notes["a"].edges], ["b"])

    def test_quoted_fence_ends_with_its_quote(self):
        # CommonMark: leaving the blockquote closes its fence; the unquoted ``` opens a new one
        self.put("docs/a.md", note(body="> ```\n> [[ghostIn]]\n\nplain [[ghostOut]]\n"))
        self.assertEqual([l.target for l in self.brain().notes["a"].links], ["ghostOut"])

    def test_quoted_marker_does_not_close_plain_fence(self):
        self.put("docs/a.md", note(body="```\n> ```\n[[ghostInside]]\n```\n"))
        self.assertEqual(self.brain().notes["a"].links, [])
