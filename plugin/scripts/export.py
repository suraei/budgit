"""budgit export — a shareable copy of the plugin + skill (R-52): nothing private leaves.

Layout of <out> (a one-plugin marketplace another person can add from a private GitHub repo):
  .claude-plugin/marketplace.json   source ./plugin
  plugin/                           .claude-plugin, agents, bin, hooks, scripts, skills/budgit-kickoff (no CHANGELOG)
  README.md, assets/*.svg           Spanish README + pixel art from the hub's share/ folder
  CHANGELOG.md                      one line
Markdown prose is scrubbed (the owner's `share_scrub` table from config.json, then ledger/lesson refs like `(I-n)` / `(L-00n)`);
code never is (the suite's leak test keeps it clean). Built in a temp dir, gated by leak.scan + the owner's 6-word
runs from the user's ledgers + `budgit secrets`; any hit → exit 1 and <out> is untouched. Stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import leak
import lint
import studiolib as sl
import vendor

MARKETPLACE = "budgit-hub"
RUN_WORDS = 6
_WORD = re.compile(r"\w+", re.U)
_REF = r"[IL]-\d+(?:/[IL]-\d+)*"
SCRUB_RE = (
    (re.compile(r" ?\((?:her |lesson )?%s(?:, [^)\n]*)?\)" % _REF), ""),   # (L-002) (her I-16) (L-003, hit twice)  # voice-ok
    (re.compile(r"\((?:her )?%s[:;] " % _REF), "("),                      # (her I-46/I-47; measured … → (measured …  # voice-ok
    (re.compile(r"\bher %s: " % _REF), ""),                                # , her I-21: any files → , any files  # voice-ok
)
FLOAT_SRC = os.path.join("tools", "brote", "float")      # R-175: the floating Brote, shipped as source under experimental/
FLOAT_DST = os.path.join("experimental", "brote-float")
FLOAT_FILES = ("brote.swift", "sprites.js", "say.js", "personality.js", "director.js", "menu.json", "book-icon.json", "Info.plist", "build.sh")
SHARE_DIR = "share"          # hub-root folder with the shareable README.md and pixel-art svgs (never in the plugin)
MIN_CLI = "2.1.292"


VOICE_RE = re.compile(r"\b(?:her|hers|herself|she)\b|\bis_her_\w+", re.I)  # voice-ok: the rule itself; the owner's voice: the plugin speaks to "you" / "the user"
VOICE_OK = "voice-ok"                                    # a line carrying this marker is allowed (a compatibility alias)
SPANISH_RE = re.compile(r"[áéíóúñ¿¡]|\b(?:%s)\b" % "|".join(
    "nada ahora versión primera compartida semana cuota llegas hasta sesión carpeta proyecto".split()), re.I)
SPANISH_FILES = ("README.md", "CHANGELOG.md")            # the public face is English; code carries the es catalog
OMIT_FROM_EXPORT = (os.path.join("bin", "studio"),)      # the old command name: someone new never had it
SHIPPED_DIRS = ("plugin", os.path.join("skills", vendor.SKILL), SHARE_DIR)


class ExportRefused(sl.StudioError):
    pass


def shipped_text_files(repo_root=None):
    """Every text file the public tree is built from, extensionless ones (plugin/bin/budgit) included: the source of
    plugin/, the kickoff skill (its CHANGELOG is not exported) and share/. One list for the D1 test and public_rules."""
    repo_root = repo_root or os.path.dirname(vendor.plugin_root())
    out = []
    for sub in SHIPPED_DIRS:
        for d, dirs, files in os.walk(os.path.join(repo_root, sub)):
            dirs[:] = sorted(x for x in dirs if x not in lint.SKIP_DIRS)
            for f in sorted(files):
                if f == "CHANGELOG.md" or f.endswith((".pyc", ".DS_Store")):
                    continue
                path = os.path.join(d, f)
                if lint._text(path)[0] is not None:
                    out.append(path)
    return out


def voice_hits(paths, base):
    """Lines that speak of the owner in the third person -> [leak.Hit]."""
    hits = []
    for path in paths:
        text, _ = lint._text(path)
        for n, line in enumerate((text or "").splitlines(), 1):
            if VOICE_OK not in line:
                m = VOICE_RE.search(line)
                if m:
                    hits.append(leak.Hit(os.path.relpath(path, base), n, "owner voice", m.group(0)))
    return hits


def public_rules(stage_dir):
    """Rules for the public tree (R-118, R-176): English README/CHANGELOG, and the plugin speaks to "you"."""
    hits = []
    for name in SPANISH_FILES:
        text, _ = lint._text(os.path.join(stage_dir, name))
        for n, line in enumerate((text or "").splitlines(), 1):
            m = SPANISH_RE.search(line)
            if m:
                hits.append(leak.Hit(name, n, "spanish", m.group(0)))
    plugin = os.path.join(stage_dir, "plugin")
    changelog = os.path.join(stage_dir, "CHANGELOG.md")
    files = [changelog] if os.path.isfile(changelog) else []
    files += [os.path.join(d, f) for d, dirs, names in os.walk(plugin)
             if not dirs.__setitem__(slice(None), sorted(x for x in dirs if x not in lint.SKIP_DIRS))
             for f in sorted(names)]
    return hits + voice_hits(files, stage_dir)


def _git(path, *args):
    return vendor._git(path, *args)


def release_check(root):
    """The export is a release: the newest CHANGELOG line names plugin.json's version and plugin/ + skills/ are
    committed. Returns the version."""
    version = vendor.plugin_version()
    skill = vendor._skill_source()
    lines = []
    if skill and os.path.isfile(os.path.join(skill, "CHANGELOG.md")):
        with open(os.path.join(skill, "CHANGELOG.md"), encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.startswith("- v")]
    if not lines or not lines[-1].startswith("- v%s " % version):
        raise ExportRefused("not a release: the newest CHANGELOG line is not v%s (plugin.json) — cut the release "
                            "first" % version)
    public = os.path.join(root, SHARE_DIR, "CHANGELOG.md")
    if version not in (vendor._read(public) if os.path.isfile(public) else ""):
        raise ExportRefused("not a release: %s/CHANGELOG.md does not mention v%s — write the public notes first"
                            % (SHARE_DIR, version))
    dirty = _git(root, "status", "--porcelain", "--", "plugin", "skills", SHARE_DIR)
    if dirty is None or dirty:
        raise ExportRefused("not a release: plugin/, skills/ or %s/ has uncommitted changes — commit them first"
                            % SHARE_DIR)
    return version


def _check_out(out):
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise ExportRefused("%s exists and is not empty — pick a new folder" % out)
    probe = out
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    if _git(probe, "rev-parse", "--show-toplevel") is not None:
        raise ExportRefused("%s is inside a git work tree — export outside any repo, then `git init` it" % out)


def scrub(text, table=()):
    """Markdown prose: the owner's exact replacements first (personal anecdotes), then ledger/lesson refs."""
    for old, new in table:
        text = text.replace(old, new)
    for pat, new in SCRUB_RE:
        text = pat.sub(new, text)
    return text


def _scrub_table(config):
    table = config.get("share_scrub") or {}
    return sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True)


def _copy_tree(src, dst, table, skip=()):
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in lint.SKIP_DIRS)
        for f in sorted(files):
            if f.endswith((".pyc", ".DS_Store")) or f == "CHANGELOG.md":
                continue
            s = os.path.join(d, f)
            if os.path.relpath(s, src) in skip:
                continue
            t = os.path.join(dst, os.path.relpath(s, src))
            if f.endswith(".md"):
                vendor._write(t, scrub(vendor._read(s), table))
            else:
                vendor._copy(s, t, 0o755 if os.access(s, os.X_OK) else 0o644)


def build(stage_dir, version, config, repo="OWNER/REPO", author=None):
    root = vendor.plugin_root()
    table = _scrub_table(config)
    plugin = os.path.join(stage_dir, "plugin")
    for sub in ("agents", "bin", "hooks", "scripts"):
        skip = tuple(o[len(sub) + 1:] for o in OMIT_FROM_EXPORT if o.startswith(sub + os.sep))
        _copy_tree(os.path.join(root, sub), os.path.join(plugin, sub), table, skip)
    skill = vendor._skill_source()
    if skill is None:
        raise ExportRefused("the %s skill was not found next to the plugin" % vendor.SKILL)
    _copy_tree(skill, os.path.join(plugin, "skills", vendor.SKILL), table)
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["name"] = "budgit"            # R-120: an export always carries the new identity, whatever the tree says
    manifest["version"] = version
    if repo != "OWNER/REPO" and re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo or ""):
        manifest["homepage"] = "https://github.com/" + repo   # the watermark's public home (R-113); never the private repo
    if author is not None:
        manifest["author"] = {"name": author}
    vendor._write(os.path.join(plugin, ".claude-plugin", "plugin.json"), json.dumps(manifest, indent=2) + "\n")
    market = {"name": MARKETPLACE, "owner": manifest.get("author") or {"name": "budgit"},
              "metadata": {"description": "Budgit: phased, token-saving workflow for Claude Code"},
              "plugins": [{"name": manifest["name"], "source": "./plugin", "description": manifest["description"]}]}
    vendor._write(os.path.join(stage_dir, ".claude-plugin", "marketplace.json"), json.dumps(market, indent=2) + "\n")
    share = os.path.join(os.path.dirname(root), SHARE_DIR)
    if not os.path.isfile(os.path.join(share, "README.md")):
        raise ExportRefused("%s/README.md not found next to the plugin: run export from the hub checkout" % SHARE_DIR)
    fields = (("repo", repo), ("marketplace", MARKETPLACE), ("min_cli", MIN_CLI), ("version", version))

    def fill(text):
        for key, value in fields:
            text = text.replace("{%s}" % key, value)
        return text
    vendor._write(os.path.join(stage_dir, "README.md"), fill(vendor._read(os.path.join(share, "README.md"))))
    licence = os.path.join(share, "LICENSE")
    if not os.path.isfile(licence):
        raise ExportRefused("%s/LICENSE not found next to the plugin: the public tree needs a licence" % SHARE_DIR)
    holder = (manifest.get("author") or {}).get("name") or "the Budgit authors"
    vendor._write(os.path.join(stage_dir, "LICENSE"), vendor._read(licence).replace("{author}", holder))
    for name in sorted(os.listdir(share)):                                       # pixel art (share/pixelart.py)
        if name.endswith(".svg"):                                                # the version strip carries fields
            vendor._write(os.path.join(stage_dir, "assets", name), fill(vendor._read(os.path.join(share, name))))
    _copy_float(os.path.dirname(root), stage_dir)
    changelog = os.path.join(share, "CHANGELOG.md")                              # written by hand, shipped as it is
    if not os.path.isfile(changelog):
        raise ExportRefused("%s/CHANGELOG.md not found next to the plugin: the public tree needs a changelog" % SHARE_DIR)
    vendor._write(os.path.join(stage_dir, "CHANGELOG.md"), fill(vendor._read(changelog)))   # {repo}, {version}...


def _copy_float(hub, stage_dir):
    """The floating window's source (no tests, no build/, no Spanish hand test, no export.mjs: its drift gate assumes the hub
    layout) with its public README. A hub without the folder ships without it."""
    src = os.path.join(hub, FLOAT_SRC)
    if not os.path.isdir(src):
        return
    dst = os.path.join(stage_dir, FLOAT_DST)
    for name in FLOAT_FILES:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            raise ExportRefused("%s/%s not found: the floating Brote cannot be exported without it" % (FLOAT_SRC, name))
        vendor._copy(path, os.path.join(dst, name), 0o755 if os.access(path, os.X_OK) else 0o644)
    readme = os.path.join(src, "README.public.md")
    if not os.path.isfile(readme):
        raise ExportRefused("%s/README.public.md not found: the floating Brote needs its public README" % FLOAT_SRC)
    vendor._write(os.path.join(dst, "README.md"), vendor._read(readme))
    vendor._write(os.path.join(dst, ".gitignore"), "build/\n")


def _ledger_texts(config):
    """The user's own words: ledger item texts and prompt inputs of the hub and every project under projects_root."""
    dirs = []
    for key in ("hub", "projects_root"):
        if config.get(key):
            dirs.append(os.path.expanduser(config[key]))
    root = config.get("projects_root")
    if root and os.path.isdir(os.path.expanduser(root)):
        base = os.path.expanduser(root)
        dirs += [os.path.join(base, n) for n in sorted(os.listdir(base))]
    texts = []
    for d in dict.fromkeys(dirs):
        data = sl.studio_dir(d)
        try:
            with open(os.path.join(data, "ledger.json"), encoding="utf-8") as f:
                texts += [i.get("text") or "" for i in json.load(f).get("items", [])]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            with open(os.path.join(data, "inbox.jsonl"), encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("source") == "prompt":
                        texts.append(row.get("text") or "")
        except OSError:
            pass
    return texts


def _runs(text):
    words = [w.lower() for w in _WORD.findall(text)]
    return {" ".join(words[i:i + RUN_WORDS]) for i in range(len(words) - RUN_WORDS + 1)}


def run_hits(stage_dir, texts):
    """Any 6-word run of the user's own words that reached a shared file → [leak.Hit]."""
    runs = set()
    for t in texts:
        runs |= _runs(t)
    hits = []
    if not runs:
        return hits
    for d, dirs, files in os.walk(stage_dir):
        dirs[:] = sorted(x for x in dirs if x not in lint.SKIP_DIRS)
        for f in sorted(files):
            text, _ = lint._text(os.path.join(d, f))
            for n, line in enumerate((text or "").splitlines(), 1):
                for run in sorted(_runs(line) & runs)[:1]:
                    hits.append(leak.Hit(os.path.relpath(os.path.join(d, f), stage_dir), n, "her words", run))  # voice-ok
    return hits


def gate(stage_dir, config):
    """leak.scan + the user's 6-word runs + `budgit secrets`. Returns the report text; raises on any hit."""
    allow = config.get("share_allow") or ()
    hits = leak.scan(stage_dir, leak.terms(config), allow)
    hits += [h for h in run_hits(stage_dir, _ledger_texts(config))
             if ("%s:%s" % (h.path, h.term)).lower() not in {a.lower() for a in allow}]
    if hits:
        raise ExportRefused(leak.report(hits))
    rules = public_rules(stage_dir)
    if rules:
        raise ExportRefused(leak.report(rules))
    vendor.scan_staged(stage_dir)
    return leak.report(hits)


PUBLISHED = "share-published.json"


def record_published(hub, version, out, when=None):
    """R-113: remember what was last exported, in the hub's own data folder (never in the public tree)."""
    path = os.path.join(sl.ensure_studio_dir(hub), PUBLISHED)
    sl.atomic_write(path, json.dumps({"version": version, "date": when or sl.now()[:10], "out": out}, indent=2) + "\n")


def pending_line(hub, version):
    """The line `budgit status` prints in the hub when the public repo is behind it, else None."""
    try:
        with open(os.path.join(sl.studio_dir(hub), PUBLISHED), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    published = record.get("version") if isinstance(record, dict) else None
    if not published or published == version:
        return None
    return "public repo at v%s, hub at v%s: re-export pending" % (published, version)


def export(out, repo="OWNER/REPO", check_release=True, config=None, record=False):
    config = sl.load_config() if config is None else config
    out = os.path.realpath(os.path.expanduser(out))
    version = release_check(os.path.dirname(vendor.plugin_root())) if check_release else vendor.plugin_version()
    _check_out(out)
    tmp = tempfile.mkdtemp(prefix="budgit-export-")
    try:
        build(tmp, version, config, repo, config.get("share_author"))
        report = gate(tmp, config)
        os.makedirs(out, exist_ok=True)
        for name in os.listdir(tmp):
            shutil.move(os.path.join(tmp, name), os.path.join(out, name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if record:
        try:
            record_published(sl.hub_path(config), version, out)
        except sl.StudioError:
            pass                                   # no hub configured: nothing to remember it in
    if os.path.isdir(os.path.join(out, ".git")):
        import watermark                           # a git working copy of the public repo: no Claude trailers in it either
        try:
            watermark.install(out, "strip-only")
        except sl.StudioError:
            pass
    return "budgit export: v%s → %s\n%s\nnext: read README.md, then `claude plugin validate --strict %s`" % (
        version, out, report, os.path.join(out, "plugin"))


def check(path, config=None):
    """`budgit export --check <dir>`: the gate alone over an existing folder."""
    config = sl.load_config() if config is None else config
    return gate(os.path.realpath(os.path.expanduser(path)), config)
