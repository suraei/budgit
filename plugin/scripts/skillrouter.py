"""R-1358: the skill router. One data file (reference/skills-tree.json, version 2) is the single source; this module only reads it.
Pure functions: which task type a text looks like (signal words and file globs, plus the project profile), the one factual line
that names the type's `pick` skills, and the use-log record for a Skill call. Advice only: nothing here denies anything."""
import fnmatch
import json
import os
import re
import time

SHORT = 3                      # a word of this many letters or fewer matches as a whole word; a longer one as a word start
MAX_TEXT = 20000               # only the start of a long prompt is read
_FILE_TOKEN = re.compile(r"[\w./~*-]*\.\w+")


OVERLAY_NAME = "skills-tree.local.json"     # in the Budgit home: {"profiles": {"<folder name>": ["<type>", ...]}}, this user's only


def overlay_profiles():
    """The private profiles of this user (folder name -> task types). A missing or invalid file means no profiles, never an error."""
    try:
        import studiolib as sl
        with open(os.path.join(sl.home_state(), OVERLAY_NAME), encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(rows, dict):
            return {}
        return {str(k).lower(): [t for t in v if isinstance(t, str)] for k, v in rows.items() if isinstance(v, list)}
    except Exception:
        return {}


def load_tree():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference", "skills-tree.json")
    with open(path, encoding="utf-8") as f:
        tree = json.load(f)
    tree["profiles"] = dict(tree.get("profiles") or {}, **overlay_profiles())
    return tree


def pick_of(spec):
    """The 1 to 3 skills a brief asks for. A version-1 type (a plain list) has no pick: its names are all returned."""
    if isinstance(spec, dict):
        return list(spec.get("pick") or [])
    return list(spec or [])


def type_names(spec):
    """Every skill name a type holds (pick and also), for the coverage check."""
    if isinstance(spec, dict):
        return list(spec.get("pick") or []) + [r["skill"] for r in spec.get("also") or [] if isinstance(r, dict) and r.get("skill")]
    return list(spec or [])


def _word_hit(word, low):
    pat = r"(?<!\w)" + re.escape(word.lower())
    if len(word) <= SHORT:
        pat += r"(?!\w)"
    return re.search(pat, low) is not None


def _file_hit(globs, text):
    names = [os.path.basename(t).lower() for t in _FILE_TOKEN.findall(text)]
    return any(fnmatch.fnmatchcase(n, g.lower()) for n in names for g in globs)


def ranked(text, tree=None, project=None):
    """[(type, score)] best first, only types with at least one signal in the text. A word hit counts 1, a file glob hit 1.
    A type in the project's profile wins a tie (+0.5, and only when the text already matched). Tree order breaks other ties."""
    tree = tree or load_tree()
    if not isinstance(text, str) or not text.strip():
        return []
    text = text[:MAX_TEXT]
    low = text.lower()
    profile = (tree.get("profiles") or {}).get(os.path.basename(str(project or "").rstrip(os.sep)).lower()) or []
    out = []
    for order, (name, spec) in enumerate((tree.get("types") or {}).items()):
        sig = spec.get("signals") or {} if isinstance(spec, dict) else {}
        score = sum(1 for w in sig.get("words") or [] if _word_hit(w, low))
        if _file_hit(sig.get("files") or [], text):
            score += 1
        if score:
            out.append((name, score + (0.5 if name in profile else 0), -order))
    out.sort(key=lambda r: (-r[1], -r[2]))
    return [(n, s) for n, s, _ in out]


def suggest_type(text, tree=None, project=None):
    best = ranked(text, tree, project)
    return best[0][0] if best else None


def router_line(task_type, tree=None):
    tree = tree or load_tree()
    names = pick_of((tree.get("types") or {}).get(task_type))
    if not names:
        return None
    return ("Task looks like %s. Skills that serve it: %s. Invoke them with the Skill tool before starting." %
            (task_type, ", ".join(names)))


def type_for_skill(skill, tree=None):
    """The first type whose pick or also names the skill (a trailing * is a prefix), else ''."""
    tree = tree or load_tree()
    for name, spec in (tree.get("types") or {}).items():
        if any(fnmatch.fnmatchcase(skill, n) for n in type_names(spec)):
            return name
    return ""


def use_record(inp, tree=None, now=None):
    """The use-log row for a Skill PostToolUse payload: {ts, session, type, skill}. Never any prompt, argument or result text."""
    if not isinstance(inp, dict) or inp.get("tool_name") != "Skill":
        return None
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    skill = str(ti.get("skill") or ti.get("command") or "").strip().lstrip("/")[:120]
    if not skill:
        return None
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
            "session": re.sub(r"[^A-Za-z0-9_-]", "", str(inp.get("session_id") or ""))[:80],
            "type": type_for_skill(skill, tree), "skill": skill}
