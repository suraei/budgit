"""R-788 steps 2-3: what an update still has to ask. Answers live in <Budgit home>/setup-answers.json, written atomically,
never through sl.setup and never by installstep. Nothing is stored until the user answers.

State: {"rules": {"enabled": true, "off": [], "told": ""}, "answers": {key: "yes"|"no"|"later"}}.
A yes or no is final; unset and later are asked again. The machine's own state counts as yes (structure: a portfolio TM
or PM; cleanup, keepalive, standup: their launch agent)."""
import json
import os

import studiolib as sl

KEYS = ("structure", "cleanup", "keepalive", "standup")
EXTRA_KEYS = ("away",)      # R-583: stored but never asked; away mode is on unless away=no
DEFAULT_ON = ("keepalive",)  # R-583: on by default, never asked; only an answer no stops it
VALUES = ("yes", "no", "later")
AGENT_LABELS = {"cleanup": "com.budgit.housekeeping", "keepalive": "com.budgit.keepalive", "standup": "com.budgit.standup"}
ASK_WINDOW_S = 120     # deliberate: two sessions starting together are one start; a later session may ask again if still unanswered


def path():
    return os.path.join(sl.home_state(), "setup-answers.json")


def load():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    off = rules.get("off") if isinstance(rules.get("off"), list) else []
    return {"rules": {"enabled": rules.get("enabled", True) is not False, "off": [x for x in off if isinstance(x, str)],
                      "told": rules.get("told") if isinstance(rules.get("told"), str) else ""},
            "answers": {k: v for k, v in answers.items() if k in KEYS + EXTRA_KEYS and (v in VALUES or (k == "structure" and v == "single"))}}


def save(data):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.atomic_write(path(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def update(fn):
    with sl.home_lock("setup-answers", strict=True):
        data = load()
        fn(data)
        save(data)
    return data


def away_enabled():
    """R-583: away mode («me voy») is ON unless the user answered away=no."""
    return load()["answers"].get("away") != "no"


def default_keepalive_due():
    """R-583: keep-alive is ON by default: due when no answer is stored (or an old 'later') and no launch agent exists yet.
    A stored yes or no is final; stand-up and cleanup are not defaults."""
    if load()["answers"].get("keepalive") in ("yes", "no"):
        return False
    return not _has_agent("keepalive")


def answer(key, value):
    if key not in KEYS + EXTRA_KEYS or not (value in VALUES or (key == "structure" and value == "single")):
        raise sl.StudioError("answer %s=%s: keys %s, values %s (structure also takes single)" % (key, value, "/".join(KEYS + EXTRA_KEYS), "/".join(VALUES)))
    return update(lambda d: d["answers"].__setitem__(key, value))


def set_topic(topic_id, off):
    def fn(d):
        offs = [x for x in d["rules"]["off"] if x != topic_id]
        d["rules"]["off"] = offs + [topic_id] if off else offs
    return update(fn)


def mark_told(version):
    return update(lambda d: d["rules"].__setitem__("told", version))


def _has_structure():
    try:
        import portfolio
        if portfolio.tm_session():
            return True
        data = portfolio.load() or {}
        projects = data.get("projects")
        entries = projects.values() if isinstance(projects, dict) else (projects or [])
        return any(isinstance(p, dict) and (p.get("pm") or p.get("pm_session")) for p in entries)
    except Exception:
        return False


def _has_agent(key):
    import housekeeping
    return os.path.exists(os.path.join(housekeeping.agents_dir(), AGENT_LABELS[key] + ".plist"))


def state(key):
    stored = load()["answers"].get(key)
    if stored in ("yes", "no", "single"):     # single (structure only) is a recorded pick, offered again like later
        return stored
    existing = _has_structure() if key == "structure" else _has_agent(key)
    return "yes" if existing else (stored or "unset")


def unanswered():
    return [k for k in KEYS if k not in DEFAULT_ON and state(k) in ("unset", "later", "single")]


def claim(session, now):
    """One asker at a time. Taken by the same session, or by another within ASK_WINDOW_S: stay silent. True = we ask."""
    claim_path = os.path.join(sl.home_state(), "setup-asked.json")
    with sl.home_lock("setup-asked", strict=True):
        try:
            with open(claim_path, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        if isinstance(old, dict) and (old.get("session") == session or now - float(old.get("ts") or 0) < ASK_WINDOW_S):
            return False
        sl.atomic_write(claim_path, json.dumps({"session": session, "ts": now}) + "\n")
    return True


def question_line(keys, with_setup_first=False):
    first = "After the setup call, " if with_setup_first else "At the first natural moment, "
    return (first + "ask the user once, with AskUserQuestion in their language and a recommended default each, about: "
            + ", ".join(keys) + ". Follow the budgit-kickoff skill's reference/setup.md; record each answer with "
            "`budgit setup --answer <key>=yes|no|later`. Do not ask again this session.")
