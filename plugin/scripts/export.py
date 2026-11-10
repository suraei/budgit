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
import time

import floatmanifest
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
FLOAT_BUNDLE = ("Brote.app", "BUILD-INFO.txt", "MANIFEST.sha256", "src")     # everything the signed bundle consists of
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


def hidden_hits(stage_dir):
    """R-287: hidden characters (zero-width, tags, variation selectors, exotic spaces, controls) in the files that
    would ship; emoji sequences and joiners in Indic/Persian/Arabic text pass -> [leak.Hit]."""
    hits = []
    root = os.path.realpath(stage_dir)
    for d, dirs, files in os.walk(stage_dir):
        entries = [x for x in dirs if os.path.islink(os.path.join(d, x))]       # a link to a folder: named, never followed
        dirs[:] = sorted(x for x in dirs if x not in lint.NOISE_DIRS)
        for f in sorted(files + entries):
            path = os.path.join(d, f)
            rel = lint.show(os.path.relpath(path, stage_dir), "")
            hits += [leak.Hit(rel, 0, "hidden char in name", c) for c in lint.name_codepoints(os.path.relpath(path, stage_dir))]
            if os.path.islink(path):
                real = os.path.realpath(path)
                if real != root and not real.startswith(root + os.sep):
                    hits.append(leak.Hit(rel, 0, "symlink leaves the tree", lint.show(os.readlink(path), "")))
                hits += [leak.Hit(rel, 0, "hidden char in link target", c) for c in lint.name_codepoints(os.readlink(path))]
            elif not lint._regular(path):
                hits.append(leak.Hit(rel, 0, "special file", "pipe, socket or device (never opened)"))
            else:
                skipped = []
                for h in lint.scan_file(path, skipped):
                    if h.kind in ("invisible", "binary-text-file", "invalid-utf8"):
                        hits.append(leak.Hit(rel, h.line, "hidden char" if h.kind == "invisible" else h.kind, h.detail))
                if skipped and not os.path.relpath(path, stage_dir).startswith(os.path.join("plugin", "float") + os.sep):
                    hits.append(leak.Hit(rel, 0, "binary file that lint cannot read",
                                         "only the signed floating Brote (checked against its manifest) may ship unread"))
    return hits


def _git(path, *args):
    return vendor._git(path, *args)


def release_check(root):
    """The export is a release: the newest CHANGELOG line names plugin.json's version and plugin/ + skills/ are
    committed. Returns the version."""
    for name in (os.path.join(root, SHARE_DIR), os.path.join(root, SHARE_DIR, "CHANGELOG.md")):
        _refuse_odd(name, deep=False)
    version = vendor.plugin_version()
    skill = vendor._skill_source()
    if skill:
        _refuse_odd(os.path.join(skill, "CHANGELOG.md"), deep=False)
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


def _refuse_odd(path, deep=True):
    """A link or a special file (pipe, socket, device) at path, or anywhere under it when it is a folder, is refused before
    anything is read: copying follows links and opening a pipe waits for ever, so a planted one would carry a file from outside
    the tree into the public repo or hang the export. A path that does not exist is the caller's business."""
    if not os.path.lexists(path):
        return
    hub = os.path.dirname(os.path.abspath(vendor.plugin_root()))
    here = os.path.abspath(path)
    if here.startswith(hub + os.sep):                 # a link in any folder above the source would carry outside files in too
        cur = hub
        for part in os.path.relpath(os.path.dirname(here), hub).split(os.sep):
            cur = os.path.join(cur, part) if part != "." else cur
            if os.path.islink(cur):
                raise ExportRefused("%s is a link: the export copies only regular files" % lint.show(os.path.relpath(cur, hub), ""))
    if os.path.islink(path) or not (os.path.isdir(path) or lint._regular(path)):
        raise ExportRefused("%s is a link or a special file: the export copies only regular files" % lint.show(path, ""))
    for d, dirs, files in os.walk(path) if deep else ():
        dirs[:] = sorted(x for x in dirs if x not in lint.SKIP_DIRS)
        for f in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            if os.path.islink(os.path.join(d, f)) or not lint._regular(os.path.join(d, f)):
                raise ExportRefused("%s is a link or a special file: the export copies only regular files"
                                    % lint.show(os.path.relpath(os.path.join(d, f), path), ""))


def _copy_tree(src, dst, table, skip=()):
    """Copy src into dst; links and special files anywhere in src are refused first (_refuse_odd)."""
    _refuse_odd(src)
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
    for sub in ("agents", "bin", "commands", "hooks", "scripts", "rules"):
        skip = tuple(o[len(sub) + 1:] for o in OMIT_FROM_EXPORT if o.startswith(sub + os.sep))
        _copy_tree(os.path.join(root, sub), os.path.join(plugin, sub), table, skip)
    import rules as _rules                       # R-788: the worker digest is baked between markers; a second export is identical
    block = _rules.worker_block()
    agents_out = os.path.join(plugin, "agents")
    for name in sorted(os.listdir(agents_out)):
        if name.endswith(".md"):
            target = os.path.join(agents_out, name)
            vendor._write(target, _rules.bake(vendor._read(target), block))
    _copy_prebuilt_float(root, plugin)
    skill = vendor._skill_source()
    if skill is None:
        raise ExportRefused("the %s skill was not found next to the plugin" % vendor.SKILL)
    _copy_tree(skill, os.path.join(plugin, "skills", vendor.SKILL), table)
    _refuse_odd(os.path.join(root, ".claude-plugin", "plugin.json"), deep=False)
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
    _refuse_odd(share, deep=False)                                              # the folder itself may be a link
    for name in sorted(os.listdir(share)):
        if name in ("README.md", "LICENSE", "CHANGELOG.md") or name.endswith(".svg"):
            _refuse_odd(os.path.join(share, name))                              # every shared file is read, so check each first
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
    vendor._write(os.path.join(stage_dir, ".gitattributes"), "plugin/float/** -text -diff\n")   # the signed bundle: git never rewrites its bytes
    changelog = os.path.join(share, "CHANGELOG.md")                              # written by hand, shipped as it is
    if not os.path.isfile(changelog):
        raise ExportRefused("%s/CHANGELOG.md not found next to the plugin: the public tree needs a changelog" % SHARE_DIR)
    vendor._write(os.path.join(stage_dir, "CHANGELOG.md"), fill(vendor._read(changelog)))   # {repo}, {version}...


VERIFIED = os.path.join(FLOAT_SRC, "VERIFIED.json")     # the hub's note of the reviewed prebuilt bundle (hub only, never exported)


def float_problems(plugin_root, hub_root):
    """Why the prebuilt floating Brote under <plugin>/float may not be exported; [] when it may or when the plugin has none."""
    fd = os.path.join(plugin_root, "float")
    if not os.path.isdir(fd):
        return []
    _refuse_odd(fd)                                                             # every file the checks below open: a pipe would hang them
    for name in [VERIFIED] + [os.path.join(FLOAT_SRC, n) for n in floatmanifest.SRC_FILES]:
        _refuse_odd(os.path.join(hub_root, name), deep=False)
    extra = ["UNLISTED %s (only %s may sit in plugin/float)" % (lint.show(n, ""), ", ".join(FLOAT_BUNDLE))
             for n in sorted(os.listdir(fd)) if n not in FLOAT_BUNDLE]          # a stray file would ship unhashed and unread
    return extra + floatmanifest.check(fd, os.path.join(hub_root, FLOAT_SRC)) + floatmanifest.reviewed_problems(fd, os.path.join(hub_root, VERIFIED))


def _copy_prebuilt_float(plugin_root, dst_plugin):
    """plugin/float (the signed Brote.app, MANIFEST.sha256, BUILD-INFO.txt, the sources it was built from), byte for byte."""
    bad = float_problems(plugin_root, os.path.dirname(plugin_root))
    if bad:
        raise ExportRefused("the prebuilt floating Brote is not exportable: " + "; ".join(bad[:5]))
    src = os.path.join(plugin_root, "float")
    if not os.path.isdir(src):
        return
    dst = os.path.join(dst_plugin, "float")
    _copy_tree(src, dst, ())
    bad = floatmanifest.check(dst)
    if bad:
        raise ExportRefused("the copied floating Brote differs from its manifest: " + "; ".join(bad[:5]))


def _copy_float(hub, stage_dir):
    """The floating window's source (no tests, no build/, no Spanish hand test, no export.mjs: its drift gate assumes the hub
    layout) with its public README. A hub without the folder ships without it."""
    src = os.path.join(hub, FLOAT_SRC)
    if not os.path.isdir(src):
        return
    dst = os.path.join(stage_dir, FLOAT_DST)
    _refuse_odd(src, deep=False)                                                # the folder itself may be a link
    for name in FLOAT_FILES + ("README.public.md",):
        _refuse_odd(os.path.join(src, name))
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
    return [t for t, _ in _her_sources(config)]


def _her_sources(config):
    """[(text, epoch or None)]: each piece of the user's words with the time of its inbox row (None for ledger text or a row
    without a readable `ts`)."""
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
        ledger = os.path.join(data, "ledger.json")
        if os.path.lexists(ledger):
            try:
                if not lint._regular(ledger):                                       # a pipe here would block the export for ever
                    raise ValueError("not a regular file")
                with open(ledger, encoding="utf-8") as f:
                    for i in json.load(f).get("items", []):
                        texts += [(t, None) for t in _item_words(i)]
            except (OSError, ValueError, AttributeError, TypeError) as exc:       # an unread ledger is never "clean"
                raise ExportRefused("could not read %s (%s); fix it and run the export again" % (ledger, exc))
        try:
            if not lint._regular(os.path.join(data, "inbox.jsonl")):
                continue
            with open(os.path.join(data, "inbox.jsonl"), encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("source") != "manual":
                        when = sl._epoch(row.get("ts")) if isinstance(row.get("ts"), str) else None
                        texts += [(_typed(t), when) for t in _row_words(row)]
        except OSError:
            pass
    return texts


_ASK_JSON = "AskUserQuestion answer: "
_ASK_PAIR = re.compile(r'"[^"]*"="')                   # the start of one `"question"="answer` pair in the text form of an answer
_ASK_TEMPLATE = re.compile(r"^(Your questions have been answered:|The user answered:)|You can now continue with these answers "
                           r"in mind\.?\s*$")


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _row_words(row):
    """R-746: the words the user typed in one inbox row. `manual` rows are an agent's (never here); prompt and transcript rows are
    the user's whole (tool blocks are cut later). An answer row drops the agent's question and the option labels the user only
    picked; the user's free text, notes and rejection reason stay. Any other or future source counts whole: the gate fails closed."""
    text = row.get("text") or ""
    if not isinstance(text, str):
        return _strings(text)
    if row.get("source") != "answer":
        return [text]
    if sl.REJECTION_MARKER in text:                    # a refused tool call: the harness's wording, then the user's reason
        return [text[text.index(sl.REJECTION_MARKER) + len(sl.REJECTION_MARKER):]]
    if sl.REJECTION_PREFIX in text:                    # the harness's refusal alone, no reason typed: nothing of the user's
        return []
    if text.startswith(_ASK_JSON):
        try:
            data = json.loads(text[len(_ASK_JSON):])
            asked = {q.get("question"): {o.get("label") for o in q.get("options") or [] if isinstance(o, dict)}
                     for q in data.get("questions") or [] if isinstance(q, dict)}
            out = []
            for question, answer in (data.get("answers") or {}).items():
                labels = asked.get(question, set())
                out += [a for a in _strings(answer) if not all(p.strip() in labels for p in a.split(","))]
            out += _strings({k: v for k, v in data.items() if k not in ("questions", "answers")})
            return out
        except (ValueError, AttributeError, TypeError):
            return [text]
    return [_ASK_TEMPLATE.sub(" ", _ASK_PAIR.sub("\n", text))]


# R-746: a prompt row holds what the chat app recorded for that turn. Its tool blocks are a shell command and its output (a `!`
# command, the gate's own report, a traceback that quotes shipped code), never words the user typed; they are cut before matching.
# `command-args` (typed after a slash command) and `command-message` stay: counting too much is safe, dropping typed words is not.
_TOOL_BLOCK = re.compile(r"<(bash-input|bash-stdout|bash-stderr|local-command-stdout|local-command-stderr|command-name|"
                         r"task-notification|system-reminder)>.*?</\1>", re.S)
_QUOTE = re.compile(r"«([^«»]*)»")


def _typed(text):
    return _TOOL_BLOCK.sub("\n", text)


def _item_words(item):
    """R-746: a ledger item that names its source input (`src`) was written by an agent; the user's words are in that input row,
    and in the item only inside «…». An item with no `src` predates that link (or is hand-made): its whole text still counts."""
    text = item.get("text") or ""
    if item.get("src"):
        return _QUOTE.findall(text)                    # one text per quote: a run never spans two quotes
    return [text]


def _runs(text):
    words = [w.lower() for w in _WORD.findall(text)]
    return {" ".join(words[i:i + RUN_WORDS]) for i in range(len(words) - RUN_WORDS + 1)}


def _code_words():
    """Names that are code before they are anyone's wording: Python keywords and builtins, the names in the modules every
    script here uses, and the subprocess keyword arguments. Read from this Python at run time, never a hand list."""
    import builtins, inspect, keyword, shutil as _sh, subprocess as _sp, sys as _sys
    names = set(keyword.kwlist) | set(dir(builtins))
    for mod in (os, os.path, _sys, _sp, json, re, _sh):
        names |= set(dir(mod))
    for fn in (_sp.Popen, _sp.run):
        try:
            names |= set(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            pass
    return {n.lower() for n in names if not n.startswith("__")}


# English function words and the ledger's own vocabulary (statuses, fields).
_FUNCTION_WORDS = set("""a an the this that these those it its i you we they me us them my your our their
of to in on at by for from with without into onto over under above below about after before between through during until
and or but nor so if then else than as is are was were be been being am do does did done has have had having can could will
would shall should may might must not no yes all any each every some one two more most less only also just very too
there here when where which who whom whose what why how up down out off again once both either neither own same such""".split())
_LEDGER_WORDS = set(sl.STATUSES) | {"item", "items", "id", "phase", "status", "text", "title", "src", "ledger", "parked", "blocked"}
# Python names that are also ordinary English words: they never make a run code on their own ('print all the items and open
# the list' is wording). Only this subtraction is written by hand; the code names themselves are read from the running Python.
_PLAIN = set("""print open list all any map filter input set type range round sum max min help exit quit format next object
property file path text check error errors name names time times sleep link remove rename move copy read write close kill
wait run call match search split compile escape load dump stat walk access system pipe group user shell timeout args true
false none hash slice super vars iter zip abs float sorted reversed enumerate license credits copyright sep join exists
abort mode flags environ executable encoding output process sub template platform version pass break continue return raise
ascii audit bin bool callable complex del dup ellipsis exception fork hex implementation intern len locale major minor nice
nonlocal pattern pow prefix purge replace scanner select signal sync truncate unlink verbose warning
yield import class global lambda while with try except finally assert await async is in not and or if else for from as
case""".split())
_COMMON = None
_CODE = None


def _common_only(run):
    """True for a run that is code, not wording: every token is a code name, a function word or a ledger word, and at least
    one token is plainly code (has `_` or a digit, or is a Python name that is no ordinary English word: 'os path realpath os
    path expanduser'). A run of English words only ('print all the items and open the list') is never skipped."""
    global _COMMON, _CODE
    if _COMMON is None:
        names = _code_words()
        _CODE = names - _FUNCTION_WORDS - _LEDGER_WORDS - _PLAIN
        _COMMON = names | _FUNCTION_WORDS | _LEDGER_WORDS
    tokens = run.split()
    code = {t for t in tokens if "_" in t or any(c.isdigit() for c in t) or t in _CODE}
    return bool(code) and all(t in _COMMON or t in code for t in tokens)


def _distinctive(run):
    """False for a run that cannot be anyone's words: every token 1-2 characters ('y m dt h m s', a date format) or half or more
    of the tokens bare numbers. Never widens what counts as a quote; only drops runs that carry no wording."""
    tokens = run.split()
    return any(len(t) > 2 for t in tokens) and sum(t.isdigit() for t in tokens) * 2 < len(tokens)


RUN_ALLOW = os.path.join("tools", "export-run-allow.json")       # in the repo, never in the export


def run_allowed(root=None):
    """The reviewed phrases that are generic wording which also occurs in the user's inputs: [{"path": exported path, "phrase": the exact
    wording, 6 words or more}] -> {(path, 6-word run)}. An entry clears the runs inside its own wording, in that one file; a file that
    cannot be read stops the export."""
    path = os.path.join(os.path.dirname(root or vendor.plugin_root()), RUN_ALLOW)
    if not os.path.lexists(path):
        return set()
    try:
        if not lint._regular(path):
            raise ValueError("not a regular file")
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        cleared = set()
        for r in rows:
            runs = _runs(r["phrase"])
            if not runs:
                raise ValueError("phrase of %s has fewer than %d words" % (r["path"], RUN_WORDS))
            cleared |= {(r["path"], run) for run in runs}
        return cleared
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ExportRefused("could not read %s (%s); fix it and run the export again" % (RUN_ALLOW, exc))


def _spaced_runs(line):
    """R-746: the 6-word runs of a shipped line whose words are written as wording: at least half of the 5 joins between them hold
    a space. Words glued by code punctuation (`content="width=device-width, initial-scale`, `match(r"[A-Za-z0-9`) are identifiers
    or a pattern, not a sentence anyone typed. Residual: a phrase shipped only as a slug (`a-b-c-d-e-f`) is not matched."""
    found = list(_WORD.finditer(line))
    out = set()
    for i in range(len(found) - RUN_WORDS + 1):
        win = found[i:i + RUN_WORDS]
        spaced = sum(1 for a, b in zip(win, win[1:]) if any(c.isspace() for c in line[a.end():b.start()]))
        if spaced * 2 >= RUN_WORDS - 1:
            out.add(" ".join(m.group().lower() for m in win))
    return out


def run_hits(stage_dir, texts, allowed=()):
    """Any 6-word run of the user's own words that reached a shared file → [leak.Hit]. `allowed`: {(exported path, phrase)} pairs
    that are reviewed and cleared; the line's other runs still count."""
    runs = set()
    for t in texts:
        runs |= _runs(t)
    runs = {r for r in runs if _distinctive(r) and not _common_only(r)}
    hits = []
    if not runs:
        return hits
    for d, dirs, files in os.walk(stage_dir):
        dirs[:] = sorted(x for x in dirs if x not in lint.SKIP_DIRS)
        for f in sorted(files):
            text, _ = lint._text(os.path.join(d, f))
            rel = os.path.relpath(os.path.join(d, f), stage_dir)
            for n, line in enumerate((text or "").splitlines(), 1):
                for run in sorted(r for r in _spaced_runs(line) & runs if (rel, r) not in allowed):   # every run, never the first only
                    hits.append(leak.Hit(rel, n, "her words", run))  # voice-ok
    return hits


GIT_TIMEOUT = 10
SHIPPED_PATHS = ("plugin", "skills/budgit-kickoff", "share")     # where shipped text lives in the repo (security review round 2)


def _git_first_seen(repo, span):
    """Epoch of the oldest commit in `repo` that added the literal `span` (git log -S, not a regex), or None on any doubt: no git,
    a shallow clone, an error, a timeout or no such commit. Fixed argument list, no shell, no textconv or external diff."""
    git = shutil.which("git")
    if not git or not repo or not span:
        return None
    base = [git, "-c", "core.fsmonitor=false", "-C", repo]
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
    try:
        shallow = subprocess.run(base + ["rev-parse", "--is-shallow-repository"], capture_output=True, text=True,
                                 timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, env=env)
        if shallow.returncode != 0 or shallow.stdout.strip() != "false":
            return None
        r = subprocess.run(base + ["log", "--no-textconv", "--no-ext-diff", "--no-show-signature", "-S" + span, "--reverse",
                                   "--format=%ct", "--"] + list(SHIPPED_PATHS),
                           capture_output=True, text=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    first = r.stdout.split()[:1] if r.returncode == 0 else []
    return int(first[0]) if first and first[0].isdigit() else None


def _span(line, run):
    """The literal text of `run` in `line` (its words with the line's own punctuation and case), or None."""
    found = list(_WORD.finditer(line))
    for i in range(len(found) - RUN_WORDS + 1):
        win = found[i:i + RUN_WORDS]
        if " ".join(m.group().lower() for m in win) == run:
            return line[win[0].start():win[-1].end()]
    return None


def product_quotes(hits, stage_dir, sources, repo):
    """R-746: a hit is not the user's words when the repo held that very text in a commit BEFORE the user's earliest inbox row
    with it (the user quoted the product back). Only runs that already hit are looked up. Ledger text, a row without a time and
    any git doubt keep the hit. A line is cleared only when EVERY run that hit on it is such a quote; one run of new words keeps
    all of the line's hits. Returns (kept hits, one note per cleared line naming the rule; a date only, never the text)."""
    if not hits:
        return hits, []
    terms = {h.term for h in hits}
    earliest, unknown = {}, set()
    for text, when in sources:
        for run in _runs(text) & terms:
            if when is None:
                unknown.add(run)
            else:
                earliest[run] = min(when, earliest.get(run, when))
    by_line = {}
    for h in hits:
        by_line.setdefault((h.path, h.line), []).append(h)
    kept, notes, seen, files = [], [], {}, {}
    for (path, n), line_hits in by_line.items():
        if path not in files:
            text, _ = lint._text(os.path.join(stage_dir, path))
            files[path] = (text or "").splitlines()
        line = files[path][n - 1] if 0 < n <= len(files[path]) else ""
        dates = []
        for h in line_hits:
            mine = earliest.get(h.term)
            if mine is None or h.term in unknown:
                break
            span = _span(line, h.term)
            if span not in seen:
                seen[span] = _git_first_seen(repo, span)
            if seen[span] is None or not seen[span] < mine:
                break
            dates.append(seen[span])
        else:
            notes.append("%s:%d: her words skipped: found in the repo since %s, before her row" % (  # voice-ok
                path, n, time.strftime("%Y-%m-%d", time.gmtime(max(dates)))))
            continue
        kept += line_hits
    return kept, notes


def gate(stage_dir, config, repo=None):
    """leak.scan + the user's 6-word runs + `budgit secrets`. Returns the report text; raises on any hit."""
    allow = config.get("share_allow") or ()
    try:
        terms = leak.terms(config)
    except ValueError as exc:                      # a protected_dirs that cannot be read is never "clean"
        raise ExportRefused("could not run the leak check (%s); fix config.json and run it again" % exc)
    hits = leak.scan(stage_dir, terms, allow)
    prebuilt = os.path.join(stage_dir, "plugin", "float")
    if os.path.isdir(prebuilt):
        hits += leak.scan_binary_strings(prebuilt, terms, allow)      # the text scanners skip files with NUL bytes
    sources = _her_sources(config)
    words = [h for h in run_hits(stage_dir, [t for t, _ in sources], run_allowed())
             if ("%s:%s" % (h.path, h.term)).lower() not in {a.lower() for a in allow}]
    words, notes = product_quotes(words, stage_dir, sources, repo or os.path.dirname(vendor.plugin_root()))
    hits += words
    tail = "".join("\n" + n for n in notes)
    rules = public_rules(stage_dir) + hidden_hits(stage_dir)
    if hits or rules:
        raise ExportRefused(leak.report(hits + rules) + tail)
    vendor.scan_staged(stage_dir)
    return leak.report(hits) + tail


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
    _refuse_odd(os.path.join(vendor.plugin_root(), ".claude-plugin", "plugin.json"), deep=False)   # read before anything else
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
    return "budgit export: v%s → %s\n%s\nnext: read the README, and run `claude plugin validate --strict %s` to check the plugin" % (
        version, out, report, os.path.join(out, "plugin"))


def check(path, config=None):
    """`budgit export --check <dir>`: the gate alone over an existing folder."""
    config = sl.load_config() if config is None else config
    folder = os.path.realpath(os.path.expanduser(path))
    try:
        leak.terms(config)                                       # a config that cannot be read is refused before anything else
    except ValueError as exc:
        raise ExportRefused("could not run the leak check (%s); fix config.json and run it again" % exc)
    if not os.path.isdir(folder) or not os.listdir(folder):      # R-857: nothing to scan; never start the secrets scanner on it
        raise ExportRefused("run budgit export first; folder not found or empty: %s" % folder)
    return gate(folder, config)
