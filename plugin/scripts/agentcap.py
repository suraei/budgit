"""Per-subagent tool-call ceiling (R-63). Briefs said "~60 calls" and agents ran 186-220; the hook
now counts every tool call a subagent makes (hook input carries agent_id only inside subagents). Zero model tokens.
cap-5 -> a note to wrap up; past cap+20 -> every tool but Write is denied so the agent saves its report and ends."""
import json
import math
import os
import re
import time

import studiolib as sl

SOFT_MARGIN, HARD_MARGIN = 5, 20
WARN_PERCENT = 80                           # R-750, R-1158: one "stop starting new work" note per round at this share of the budget
MAX_STATED = 500


def _path(agent_id):
    return os.path.join(sl.home_state(), "agent-calls", re.sub(r"[^A-Za-z0-9_-]", "_", str(agent_id))[:64])


# ONE accepted shape per wait (an allow list): `sleep N`, or cat/tail/head with at most `-n N` or `-N` and a single
# plain path ending /tasks/<name>.output. Any other character anywhere makes the call work.
_WAIT_CMD = re.compile(r"sleep [0-9]+|(cat|tail|head)( -n [0-9]+| -[0-9]+)? [A-Za-z0-9_./-]*/tasks/[A-Za-z0-9_.-]+\.output")


def is_wait(name, inp):
    """True for a call that only waits: a background-output read, a `sleep`, TaskOutput or BashOutput.
    The budget measures work, so these are counted apart (shared by the live cap and the transcript audit)."""
    if name in ("TaskOutput", "BashOutput"):
        return True
    cmd = (inp or {}).get("command") if isinstance(inp, dict) else None
    return name == "Bash" and isinstance(cmd, str) and _WAIT_CMD.fullmatch(cmd) is not None


def waits(agent_id):
    try:
        with open(_path(agent_id) + ".waits") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump(agent_id, suffix=""):
    path = _path(agent_id) + suffix
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            n = int(f.read().strip())        # an empty counter is corrupt too
    except FileNotFoundError:
        n = 0
    except (OSError, ValueError):
        n = 10 ** 4                          # unreadable counter: fail closed, never back to zero
    n += 1
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, path)
    return n


# R-1158: one JSON line per finished round, in Budgit's own state beside the counters: agent type, the first 60 chars of
# the description, stated budget, work calls used, date, owner session id. Never prompt text.
RUNS_NAME, LAUNCHES_NAME = "runs.jsonl", "launches.jsonl"
MAX_LINES = 2000
TAIL_BYTES, ROTATE_BYTES = 512 * 1024, 1024 * 1024
MIN_SIMILAR = 3
SIMILAR_WORDS = 2
SUGGEST_FACTOR = 1.2
STOP_WORDS = frozenset("with from that this have into your their there about then than them they what when will "
                       "make some more only also just over each both done work item step build fix".split())


def _runs_path(name=RUNS_NAME):
    return os.path.join(sl.home_state(), "agent-calls", name)


def _read_lines(name):
    try:                                         # only the tail is read; bad bytes never raise (R-1158 round 2)
        with open(_runs_path(name), "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read(TAIL_BYTES)
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]                        # the first line of a tail may be cut in half
    out = []
    for line in lines[-MAX_LINES:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append(name, rec):
    path = _runs_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > ROTATE_BYTES:
            os.replace(path, path + ".1")        # one old copy, replaced each time: the record stays bounded
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _kind(value):
    return sl.canon_agent(value) if isinstance(value, str) and value else "general-purpose"


def note_launch(inp):
    """At the Agent tool's PreToolUse: remember type and description for the round-end record (the stop event has no
    description of its own). Bounded; a failure is the caller's to log."""
    ti = inp.get("tool_input")
    if not isinstance(ti, dict) or not isinstance(ti.get("description"), str):
        return
    rows = _read_lines(LAUNCHES_NAME)[-49:] + [{"session": str(inp.get("session_id") or ""), "type": _kind(ti.get("subagent_type")),
                                                 "desc": ti["description"][:60]}]
    path = _runs_path(LAUNCHES_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _description(inp, kind):
    """The subagent's description: its own meta file when it has one, else the oldest launch noted for this session and type."""
    t = _transcript(inp)
    if t:
        try:
            with open(t[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
                d = json.load(f).get("description")
            if isinstance(d, str) and d:
                return d[:60]
        except (OSError, ValueError, AttributeError):
            pass
    rows, session = _read_lines(LAUNCHES_NAME), str(inp.get("session_id") or "")
    for i in range(len(rows) - 1, -1, -1):       # the newest match
        r = rows[i]
        if r.get("session") == session and r.get("type") == kind:
            rest = rows[:i] + rows[i + 1:]
            path = _runs_path(LAUNCHES_NAME)
            tmp = path + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rest))
            os.replace(tmp, path)
            return str(r.get("desc") or "")[:60]
    return ""


def _read_count(path):
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def record_round(inp):
    """R-1158: append the finished round to the past-run record. Call it BEFORE the counters are removed. A round with no
    work call writes nothing."""
    agent_id = inp.get("agent_id")
    n = _read_count(_path(agent_id))
    if n <= 0:
        return
    stated = 0
    try:
        with open(_path(agent_id) + ".round") as f:
            stated = int(json.load(f)["b"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    stated = stated or _read_count(_path(agent_id) + ".budget")
    kind = inp.get("agent_type")
    if not kind:
        t = _transcript(inp)
        try:
            with open(t[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
                kind = json.load(f).get("agentType")
        except (OSError, ValueError, AttributeError, TypeError):
            kind = None
    kind = _kind(kind)
    _append(RUNS_NAME, {"type": kind, "desc": _description(inp, kind), "budget": min(stated, MAX_STATED), "calls": n,
                        "date": time.strftime("%Y-%m-%d"), "owner": str(inp.get("session_id") or "")})


def _words(text):
    return {w for w in re.findall(r"[^\W\d_]{4,}", str(text or "").lower()) if w not in STOP_WORDS}


def advice(tool_input):
    """R-1158: one line before a start when 3 or more past runs of the same agent type share 2 content words with this
    description. Advice only. None otherwise."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("description"), str):
        return None
    kind, words = _kind(tool_input.get("subagent_type")), _words(tool_input["description"])
    calls = sorted(int(r["calls"]) for r in _read_lines(RUNS_NAME)
                   if r.get("type") == kind and isinstance(r.get("calls"), int) and len(_words(r.get("desc")) & words) >= SIMILAR_WORDS)
    if len(calls) < MIN_SIMILAR:
        return None
    n = len(calls)
    median = calls[n // 2] if n % 2 else math.ceil((calls[n // 2 - 1] + calls[n // 2]) / 2)
    p80 = calls[math.ceil(n * 0.8) - 1]
    m = _BRIEF_BUDGET.search(str(tool_input.get("prompt") or ""))
    stated = int(m.group(1)) if m else None
    text = "budgit: similar past runs (%d) used median %d, p80 %d calls; your brief says %s" % (n, median, p80, stated if stated else "none")
    if stated is None or stated < p80:
        text += "; suggest Budget: %d" % math.ceil(p80 * SUGGEST_FACTOR)
    return text


def over_budget_by_owner(days=7, today=None):
    """R-1158: {owner session id: (runs over their stated budget, runs)} for the last `days` days of the record."""
    cutoff = time.strftime("%Y-%m-%d", time.localtime((today or time.time()) - days * 86400))
    out = {}
    for r in _read_lines(RUNS_NAME):
        if str(r.get("date") or "") < cutoff or not r.get("owner"):
            continue
        over, total = out.get(r["owner"], (0, 0))
        b, c = r.get("budget"), r.get("calls")
        out[r["owner"]] = (over + bool(isinstance(b, int) and b > 0 and isinstance(c, int) and c > b), total + 1)
    return out


def reset(agent_id):
    for suffix in ("", ".waits", ".budget", ".round", ".reports"):
        try:
            os.remove(_path(agent_id) + suffix)
        except OSError:
            pass


# R-750: a line `Budget: N`, `Call budget: N`, optionally followed by `tool calls`, `calls` or `more` (a resume's
# `Budget: N more` is a round of N). The colon is required: `Budget 2026 notes` and `Budget: 2 hours` set nothing.
# MAX_STATED is the ceiling.
_BRIEF_BUDGET = re.compile(r"(?im)^\W*(?:call )?budget(?:\*\*)?:(?:\*\*)?[ \t]*(\d{1,4})(?:[ \t]+more)?(?:[ \t]+(?:tool calls?|calls?))?(?:[ \t]*[.;,(].*)?[ \t]*$")


def _transcript(inp):
    """The subagent's own transcript: the hook's agent path when given, else next to the session transcript."""
    p = inp.get("agent_transcript_path")
    if isinstance(p, str) and p.endswith(".jsonl"):
        return p
    t = inp.get("transcript_path")
    if isinstance(t, str) and t.endswith(".jsonl") and inp.get("agent_id"):
        return "%s/subagents/agent-%s.jsonl" % (t[:-len(".jsonl")], inp["agent_id"])
    return None


def brief_budget(inp):
    """R-849: N from the `Call budget: N` line of the brief (the subagent's first user message), read once
    per agent and cached beside its counter. None when the brief states none."""
    agent_id = inp.get("agent_id")
    cache = _path(agent_id) + ".budget"
    try:
        with open(cache) as f:
            v = int(f.read().strip() or 0)
        return min(v, MAX_STATED) or None
    except (OSError, ValueError):
        pass
    path, found = _transcript(inp), 0
    try:
        with open(path, encoding="utf-8") as f:
            for _, line in zip(range(40), f):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                if rec.get("type") != "user" or not isinstance(msg, dict):
                    continue
                c = msg.get("content")
                if isinstance(c, list):
                    c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                m = _BRIEF_BUDGET.search(c) if isinstance(c, str) else None
                found = int(m.group(1)) if m else 0
                break
    except (OSError, TypeError):
        return None                          # transcript not readable yet: ask again next call, do not cache
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as f:
            f.write(str(found))
    except OSError:
        pass
    return min(found, MAX_STATED) or None      # a stated number is capped: "Call budget: 99999" is no budget


def _user_text(rec):
    """The text of a real user turn (the brief, or a SendMessage resume); None for tool results and meta records."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return None
    origin = rec.get("origin")
    resume = isinstance(origin, dict) and origin.get("kind") == "coordinator"   # a SendMessage resume is recorded isMeta
    if any(rec.get(k) for k in ("isCompactSummary", "isVisibleInTranscriptOnly")):
        return None
    if rec.get("isMeta") and not resume:
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    if isinstance(c, list):
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        c = "\n".join(texts) if texts else None
    if not isinstance(c, str) or c.startswith("[Request interrupted by user"):
        return None                          # tool results, interrupt markers: not a round
    return c


def _read_round(inp):
    """R-868: a round starts at each real user message in the subagent transcript (the brief, then every resume).
    Read incrementally (byte offset kept beside the counter). Returns the round budget stated by the LATEST
    message, or None. A new round zeroes the work and wait counters."""
    agent_id = inp.get("agent_id")
    path, state_path = _transcript(inp), _path(agent_id) + ".round"
    if not path:
        return None
    try:
        with open(state_path) as f:
            st = json.load(f)
        off, k, b = int(st["off"]), int(st["k"]), int(st["b"])
        if min(off, k, b) < 0:
            raise ValueError("negative")
    except FileNotFoundError:
        off, k, b = 0, 0, 0
    except (OSError, ValueError, KeyError, TypeError):
        return None                           # corrupt or unreadable state: fail closed, never a reset to zero
    try:
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
    except OSError:
        return min(b, MAX_STATED) if b else None
    end = data.rfind(b"\n") + 1               # a half-written last line waits for the next call
    new_k = k
    for line in data[:end].splitlines():
        try:
            text = _user_text(json.loads(line))
        except ValueError:
            continue
        if text is None:
            continue
        new_k += 1
        m = _BRIEF_BUDGET.search(text)
        b = int(m.group(1)) if m else 0       # the resume's own line applies; none means the original one
    if new_k > k and k > 0:                   # k == 0 is the first sight of the brief: nothing to zero
        try:
            record_round(inp)                 # R-1158: the round that just ended, before its counters go
        except Exception:
            sl.log_error("agent round record")
        for suffix in ("", ".waits"):
            try:
                os.remove(_path(agent_id) + suffix)
            except OSError:
                pass
    if new_k != k or end:
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            tmp = state_path + ".tmp%d" % os.getpid()
            with open(tmp, "w") as f:
                json.dump({"off": off + end, "k": new_k, "b": b}, f)
            os.replace(tmp, state_path)
        except OSError:
            pass
    return min(b, MAX_STATED) if b else None


AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "agents")


def max_turns(kind, agents_dir=None):
    """maxTurns from plugin/agents/<name>.md frontmatter for a subagent_type like `studio:security-reviewer`, else None."""
    name = str(kind or "").split(":")[-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return None
    try:
        with open(os.path.join(agents_dir or AGENTS_DIR, name + ".md"), encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return None
    m = re.search(r"(?m)^maxTurns:\s*(\d+)\s*$", head)
    return int(m.group(1)) if m else None


def launch_warning(tool_input, agents_dir=None):
    """R-904: one line when the brief's budget is larger than the agent type's own maxTurns, else None."""
    if not isinstance(tool_input, dict):
        return None
    m = _BRIEF_BUDGET.search(str(tool_input.get("prompt") or ""))
    limit = max_turns(tool_input.get("subagent_type"), agents_dir)
    if m and limit and int(m.group(1)) > limit:
        return ("budgit: %s has maxTurns %d but the brief budgets %d calls; it will stop at %d turns. Lower the budget "
                "or split the work." % (tool_input.get("subagent_type"), limit, int(m.group(1)), limit))
    return None


EXAMPLE_MARKERS = ("por ejemplo", "es un ejemplo", "ejemplo", "p. ej.", "e.g.", "for example", "such as")
RULE_PREFIXES = ("rule:", "general case:", "regla:", "caso general:")
_QUOTED = re.compile("«([^«»]*)»")          # R-929 r2: never crosses a second «, so the scan stays linear


def example_only_warning(tool_input, markers=None, prefixes=RULE_PREFIXES):
    """R-929: one line when the brief quotes the user's words («...») with an example marker inside the quote but has
    no `Rule:`/`Regla:`/`General case:`/`Caso general:` line. A marker outside the quotes does not count. Else None."""
    if not isinstance(tool_input, dict):
        return None
    markers = EXAMPLE_MARKERS if markers is None else markers
    text = str(tool_input.get("prompt") or "")
    if not any(m in q.lower() for q in _QUOTED.findall(text) for m in markers):
        return None
    if any(ln.strip().lstrip("-*> \t").lower().startswith(prefixes) for ln in text.splitlines()):
        return None
    return ("R-929: the brief quotes the user's example but has no Rule:/Regla: line — state the general case, "
            "not only that example.")


# R-992: one connector list ("and", "y", "e", "plus", "&", comma, ", and") voids the warning on every alternative.
_DET = r"(?:the|my|your|our|their|its|su|sus|el|la|los|las)"
_DOC_NOUN = (r"(?:specs?|specifications?|especificaci(?:ón|on|ones)|docs?|documents?|documentation|documentos?"
             r"|documentaci(?:ón|on)|reports?|informes?|reportes?)\b")
_CONN = (r"(?:\s+(?:and|y|e|plus)\s+(?:" + _DET + r"\s+)?\w"
         r"|\s*&\s*(?:" + _DET + r"\s+)?\w"
         r"|\s*,\s*(?:(?:and|y|e|plus)\s+)?(?:" + _DET + r"\s+\w|" + _DOC_NOUN + r"))")
# "the report from QA and the logs" is two sources, so an origin phrase may sit before the connector.
_SECOND = r"(?!(?:\s+(?:from|by|de|por)\s+\S+?)?" + _CONN + r")"
_NOT_PLACE = r"(?!\s+(?:folders?|dir(?:ectory)?|page|pages|carpeta|p[aá]ginas?|fees?)\b)"
_ONE_SOURCE = re.compile(r"\b(?:rules?|criteria|reglas?|criterios?)\s+(?:from|del?|seg[uú]n)\s+(?!(?:" + _DET + r"\s+)?\S+" + _CONN + r")\S"
                         r"|\bseg[uú]n\s+(?:el|la|los|las|su|sus)\s+(?:\w+\s+)?" + _DOC_NOUN + _NOT_PLACE + _SECOND +
                         # R-984: per/from/según [lo que dice] [possessive] <document noun>; a second source voids it
                         r"|\b(?:per|from|seg[uú]n)\s+(?:lo\s+que\s+dice\s+)?(?:" + _DET + r"\s+)?"
                         r"" + _DOC_NOUN + _NOT_PLACE + _SECOND, re.I)
# R-974 r2: exact user-source phrases only; a document noun or file name within 3 words after it voids the phrase.
_NOT_DOC = (r"(?![\w\-'’])"
            r"(?!(?:\s+[\w.\-]+){0,3}?\s+[\w\-]*(?:report|spec|doc|file|informe|fichero|pdf)\w*)"
            r"(?!(?:\s+[\w\-]+){0,3}?\s+\w[\w\-]*\.\w{2,4}\b)")
_USER_PHRASE = (r"(?:the\s+user(?:['’]s\s+(?:words|rules))?|(?:my|your|our|their)\s+(?:words|rules)"
                r"|sus\s+(?:palabras|reglas)|(?:el\s+usuario|la\s+usuaria|usuari[oa]))")
_USER_SOURCE = re.compile(r"\b(?:rules?|criteria|reglas?|criterios?)\s+(?:from|del?|seg[uú]n)\s+" + _USER_PHRASE + _NOT_DOC
                          + r"|\bseg[uú]n\s+(?:sus\s+(?:palabras|reglas)|lo\s+que\s+dice\s+el\s+usuario)" + _NOT_DOC, re.I)
_EARLIER_ID = re.compile(r"\bI-\d+")
EARLIER_RULES = ("approved design", "diseño aprobado", "briefs.md", "earlier rules", "reglas anteriores", "existing rules")


def _card_text(tool_input):
    """Prompt of a brief, or the question and option text of an AskUserQuestion card."""
    parts = [str(tool_input.get("prompt") or "")]
    qs = tool_input.get("questions")
    for q in qs if isinstance(qs, list) else []:
        if isinstance(q, dict):
            parts.append(str(q.get("question") or ""))
            for o in q.get("options") if isinstance(q.get("options"), list) else []:
                parts.append(" ".join(str(o.get(k) or "") for k in ("label", "description")) if isinstance(o, dict) else str(o))
    return "\n".join(parts)


# R-1152: the one place for the words of a file-like thing and for what counts as a path or link.
# Strong words always point at a thing to look at; generic words (file, page, image, design) only after a pointing word.
FILE_WORDS = (r"mock-?ups?|mocks?|maquetas?|bocetos?|capturas?(?:\s+de\s+pantalla)?|screenshots?|pantallazos?|previews?"
              r"|opci[oó]n\s+\w+\s+dibujada|option\s+\w+\s+drawn")
GENERIC_WORDS = (r"im[aá]genes|imagen|images?|archivos?|ficheros?|files?|captures?|p[aá]ginas?|pages?"
                 r"|dise[nñ]os?|designs?")
_POINT = (r"(?:this|that|these|those|the|my|our|your|a|an|which|esta|este|estos|estas|esa|ese|esos|esas|aquel|aquella|aquellos|aquellas"
          r"|el|la|los|las|al|del|un|una|unos|unas|mi|tu|nuestro|nuestra)\s+")
_GENERIC = re.compile(r"(?<![\w/.-])" + _POINT + r"((?:" + GENERIC_WORDS + r")\b)(?!\s+(?:format|type|system|decisions?|choices?|questions?|name|size|limit))", re.I)
_STRONG = re.compile(r"(?<![\w/.-])(?:" + FILE_WORDS + r")(?![\w/-])", re.I)
_CARD_REF = re.compile(r"(?:https?://|file://)\S+|(?<![\w:.\-/])~/\S*|\b[A-Za-z]:\\\S+"
                       r"|(?<![\w:.\-/])/[\w.~-]+(?:/[\w.~-]+)+/?", re.I)


def card_ref_missing(tool_input):
    """R-1152: the file-like word a card names when its whole text holds no path or link; else None."""
    if not isinstance(tool_input, dict):
        return None
    qs = tool_input.get("questions")
    parts = []
    for q in qs if isinstance(qs, list) else []:
        if not isinstance(q, dict):
            continue
        parts += [str(q.get("header") or ""), str(q.get("question") or "")]
        for o in q.get("options") if isinstance(q.get("options"), list) else []:
            parts.append(" ".join(str(o.get(k) or "") for k in ("label", "description", "preview")) if isinstance(o, dict) else str(o))
    text = "\n".join(parts)
    hit = _STRONG.search(text) or _GENERIC.search(text)
    if not hit or _CARD_REF.search(text):
        return None
    return " ".join(hit.group(0).split())


def single_source_warning(tool_input):
    """One line when a brief or card takes its rules from ONE named source and cites no earlier
    rules (a ledger note id, an approved design, briefs.md, earlier/existing rules, or a second named source). Else None."""
    if not isinstance(tool_input, dict):
        return None
    text = _card_text(tool_input)
    hits = len(_ONE_SOURCE.findall(_USER_SOURCE.sub(" ", text)))   # R-974: the user's own rules are the right source
    if not hits:
        return None
    low = text.lower()
    if hits > 1 or _EARLIER_ID.search(text) or any(p in low for p in EARLIER_RULES):
        return None
    return ("R-929: the brief takes its rules from one source; name the user's earlier rules on this subject "
            "(I-n words, approved designs, code tables) too.")


_TASK_LINE = re.compile(r"^[ \t>*\-]*task:[ \t]*(.+)$", re.I | re.M)
_SKILLS_LINE = re.compile(r"^[ \t>*\-]*skills:[ \t]*(.*)$", re.I | re.M)
_MD_PATH = re.compile(r"(?<![\w:.\-/])(/[\w.~@+ -]+(?:/[\w.~@+ -]+)*\.md)\b")


def _skills_tree():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference", "skills-tree.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_TASK_SYNONYMS = {"verification": "verify", "tests": "write-tests", "bugfix": "fix-bug"}   # R-1360: a word for a type, not a new type


def task_synonym(name, line=""):
    """R-1360: the canonical task type for a synonym (`review` is security-review when the word security is in the `task:` line, else
    plan-review); any other word comes back unchanged, so an unknown word still gets the warning with the type list."""
    key = str(name or "").strip().lower()
    if key == "review":
        return "security-review" if "security" in str(line or "").lower() else "plan-review"
    return _TASK_SYNONYMS.get(key, name)


def skillrouter_pick(spec):
    """R-1358: the 1 to 3 names a type asks for in a brief (its `pick`), never every name the type holds."""
    import skillrouter
    return skillrouter.pick_of(spec)


def _split_names(text):
    return [x.strip().strip("`'\"") for x in re.split(r"[,;]", text) if x.strip().strip("`'\"")]


def skills_warning(tool_input):
    """R-1211: one advisory line for a studio:* launch. With `task:` in the brief (prompt or a named absolute .md file)
    and a `skills:` line that misses a skill the tree gives that type: the expected `skills:` line. Without `task:`:
    the task types. Names come from the tree only, never from file content. Else None."""
    import fnmatch
    if not isinstance(tool_input, dict) or not str(tool_input.get("subagent_type") or "").startswith("studio:"):
        return None
    prompt = str(tool_input.get("prompt") or "")
    text = prompt
    for m in _MD_PATH.finditer(prompt):
        p = m.group(1)
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= 262144:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text += "\n" + f.read()
        except OSError:
            continue
    try:
        tree = _skills_tree()
    except (OSError, ValueError):
        return None
    types = tree.get("types") or {}
    task = _TASK_LINE.search(text)
    if not task:
        line = "R-1211: no `task:` line in the brief; add `task: <type>` with one of: " + ", ".join(types) + "."
        import skillrouter                      # R-1358: a type from the brief's own words (signals in the tree), never from file names
        guess = skillrouter.suggest_type(text, tree)
        if guess:
            line += " The brief's words look like `%s`: `task: %s`, `skills: %s`." % (guess, guess, ", ".join(skillrouter.pick_of(types[guess])))
        return line
    wanted = []
    for t in _split_names(task.group(1)):
        if t not in types:
            t = task_synonym(t, task.group(1))          # R-1360: before the type check, so a synonym gets no warning
        for name in skillrouter_pick(types.get(t)):
            if name not in wanted and not name.endswith("*"):
                wanted.append(name)
    if not wanted:
        return "R-1211: `task:` names no known type; use one of: " + ", ".join(types) + "."
    have = []
    for m in _SKILLS_LINE.finditer(text):
        have += _split_names(m.group(1))
    missing = [w for w in wanted if not any(fnmatch.fnmatchcase(h, w) or h == w for h in have)]
    if not missing:
        return None
    return "R-1211: the brief's `skills:` line misses " + ", ".join(missing) + "; expected `skills: " + ", ".join(wanted) + "`."


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _branch(wt):
    """The checked-out branch of a worktree (plain or linked), or None when detached or unreadable."""
    try:
        g = os.path.join(wt, ".git")
        if os.path.isfile(g):
            with open(g) as f:
                line = f.read().strip()
            gitdir = line[len("gitdir:"):].strip() if line.startswith("gitdir:") else ""
            gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(wt, gitdir)
        else:
            gitdir = g
        with open(os.path.join(gitdir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


_FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _target(inp):
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    p = ti.get("file_path") or ti.get("notebook_path")
    return os.path.realpath(p) if isinstance(p, str) and p else None


def protected_write(inp):
    """R-868: a file tool may never touch the agent's own transcript, any subagents/ dir or the counter state.
    The transcript is thereby outside every path an agent may write, so a forged user line cannot start a round."""
    if inp.get("tool_name") not in _FILE_TOOLS:
        return False
    t = _target(inp)
    if t is None:
        return False
    state = os.path.realpath(os.path.join(sl.home_state(), "agent-calls"))
    mine = _transcript(inp)
    return (_inside(t, state) or (os.sep + "subagents" + os.sep) in t
            or (mine is not None and t == os.path.realpath(mine)))


REPORT_MARGIN = 10                           # R-908: a verifier is told to write its report at cap - 10
_VERIFY_NAME = re.compile(r"R-\d+\.md|(?:RELEASE|RC)-[A-Za-z0-9_.-]*\.md")


def is_verifier(inp):
    return sl.canon_agent(inp.get("agent_type")) == sl.VERIFIER


def verify_report_write(inp):
    """R-908: the ONE call a verifier may still make past its limit: Write or Edit of <project>/.budgit/verify/<NAME>.md
    (or .studio). Only the verifier may write there, so refusing it would lose the verdict for good."""
    if inp.get("tool_name") not in ("Write", "Edit") or not is_verifier(inp) or protected_write(inp):
        return False
    t, cwd = _target(inp), inp.get("cwd")
    root = main_root(cwd) if isinstance(cwd, str) and cwd else None
    if not t or not root:
        return False
    d, name = os.path.split(t)
    d2, last = os.path.split(d)
    return (last == "verify" and os.path.basename(d2) in sl.DATA_NAMES and os.path.realpath(os.path.dirname(d2)) == root
            and _VERIFY_NAME.fullmatch(name) is not None and name != sl.SEALS_FILE)


MAX_REPORT_WRITES = 3                         # Write/Edit calls of a verify report allowed past the limit, per agent


def main_root(cwd):
    """The main checkout root of the project holding cwd: the nearest ancestor with a .git folder, or, for a linked
    worktree (.git file), the checkout its gitdir belongs to. No git call. None when cwd is in no repository."""
    d = os.path.realpath(cwd)
    while True:
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return d
        if os.path.isfile(g):
            try:
                with open(g) as f:
                    line = f.read().strip()
            except OSError:
                return None
            gitdir = os.path.realpath(line[len("gitdir:"):].strip()) if line.startswith("gitdir:") else ""
            marker = os.sep + ".git" + os.sep + "worktrees" + os.sep
            return gitdir.split(marker)[0] if marker in gitdir + os.sep else None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _report_note(n, limit):
    return ("budgit: %d calls of %d: write your verdict now to <project>/.budgit/verify/<item>.md (Write), then hand back. "
            "Past the limit only that write is allowed." % (n, limit))


def _allowed_past_limit(inp):
    """Past the limit only the final message, Write/Edit of <cwd>/progress*.md (linked worktree root on a branch other
    than main or master; no git call, the caller commits) and a verifier's own verify report pass."""
    if inp.get("tool_name") == "SubagentHandback":
        return True
    if verify_report_write(inp):                # a few writes only: a bounded count, never an open door
        return _bump(inp.get("agent_id"), ".reports") <= MAX_REPORT_WRITES
    if inp.get("tool_name") in ("Write", "Edit"):
        t, cwd = _target(inp), inp.get("cwd")
        if not (t and isinstance(cwd, str) and cwd):
            return False
        cwd = os.path.realpath(cwd)
        base = os.path.basename(t)
        return (os.path.dirname(t) == cwd and base.startswith("progress") and base.endswith(".md")
                and os.path.isfile(os.path.join(cwd, ".git")) and _branch(cwd) not in (None, "main", "master")
                and not protected_write(inp))
    return False


_LIMIT_TEXT = "limit reached: save notes in progress*.md and hand back; your caller commits"


def check(inp, cap):
    """None, ("note", text) or ("deny", reason) for this tool call. An error inside the check fails closed for an
    agent already past its limit (refuse, one log line) and lets one under the limit through."""
    try:
        return _check(inp, cap)
    except Exception:
        sl.log_error("agent call cap")
        agent_id = inp.get("agent_id")
        try:
            with open(_path(agent_id)) as f:
                n = int(f.read().strip())
            stated = 0
            for suffix in (".round", ".budget"):         # the round's own budget first, then the brief's
                try:
                    with open(_path(agent_id) + suffix) as f:
                        raw = f.read()
                    stated = min(int(json.loads(raw)["b"] if suffix == ".round" else raw.strip()), MAX_STATED)
                except (OSError, ValueError, KeyError, TypeError):
                    stated = 0
                if stated:
                    break
            hard = stated + (stated + 3) // 4 if stated else cap + HARD_MARGIN
        except FileNotFoundError:
            n, hard = 0, cap + HARD_MARGIN
        except (OSError, ValueError):
            n, hard = 10 ** 4, cap + HARD_MARGIN
        if n >= hard and not _allowed_past_limit(inp):
            return ("deny", "budgit: call check failed past the limit: %s" % _LIMIT_TEXT)
        return None


def _check(inp, cap):
    agent_id = inp.get("agent_id")
    if not agent_id:
        return None
    if protected_write(inp):
        return ("deny", "budgit: the call counter and the transcripts are not yours to write; use your worktree.")
    round_b = _read_round(inp)           # R-868: a resume zeroes the counters before this call is counted
    if is_wait(inp.get("tool_name"), inp.get("tool_input")):
        _bump(agent_id, ".waits")          # a wait is free: it never moves the work count
        return None
    n = _bump(agent_id)
    stated = round_b or brief_budget(inp)
    if stated:                             # R-849: the brief's own number wins; report at N, refuse past N + 25 %
        hard = stated + (stated + 3) // 4
        if n > hard and not _allowed_past_limit(inp):
            return ("deny", "budgit: call budget %d (+25%% = %d) is used up (%d calls this round): %s; then send your "
                            "report (done, evidence, what is left) as your final message." % (stated, hard, n - 1, _LIMIT_TEXT))
        if n == stated or (stated < n <= hard and (n - stated) % 5 == 0):
            return ("note", "budgit: call budget %d reached (%d calls, work only): report now. Commit green work now as a "
                            "checkpoint (git add, then commit) and hand back. Refused from call %d."
                    % (stated, n, hard + 1))
        notes = ["budgit: budget read: %d" % stated] if n == 1 else []     # R-750: the agent sees which number applies
        if n == (stated * WARN_PERCENT) // 100 and n < stated:      # R-750: once per round, every subagent
            notes.append("budgit: %d %% of your budget used (%d of %d): stop starting new work; finish the current step, "
                         "commit green work, and report what is done and what is left." % (WARN_PERCENT, n, stated))
        if is_verifier(inp) and n == stated - REPORT_MARGIN:
            notes.append(_report_note(n, stated))
        return ("note", " ".join(notes)) if notes else None
    soft, hard = cap - SOFT_MARGIN, cap + HARD_MARGIN
    if n == 1:
        return ("note", "budgit: budget read: none, brief default %d" % cap)
    if is_verifier(inp) and n == cap - REPORT_MARGIN:
        return ("note", _report_note(n, cap))
    if n > hard and not _allowed_past_limit(inp):
        return ("deny", "budgit: tool-call ceiling reached (%d calls, budget ~%d): %s; then send your final report "
                        "(done, evidence, what is left) as your last message." % (n - 1, cap, _LIMIT_TEXT))
    if n == soft or (n > soft and (n - soft) % 10 == 0 and n <= hard):
        return ("note", "budgit: work %d, waits %d (budget ~%d, hard stop at %d): commit your green work now (a checkpoint) and "
                        "report what is left instead of starting new work." % (n, waits(agent_id), cap, hard))
    return None
