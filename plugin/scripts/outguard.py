"""R-506: the output guard. The last message of a chat must reach the user as a result, not as working text.

Two checks run on that message, both on prose only (code fences, inline code, links, URLs and quoted text are blanked first):
  A. working-text markers: internal ids and paths, tool narration, talk about the harness's own notices and rules, internal roles as actors;
  B. the language gate: English prose in a chat whose language is Spanish (a draft written for a third person is exempt).
The marker patterns and the language thresholds live in outguard-rules.json, not here, so the Python code and the screen layer
read one list. Everything here is a pure function of the text and the rules, except `stop_reason`, which keeps a small
per-session marker so a block happens once. Nothing in this module raises: any failure means "no finding".

Reusable by other layers (a display or send gate, SubagentStop): `check(text, lang)` -> [(kind, snippet)], `kind_label`."""
import hashlib
import json
import os
import re
import time

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outguard-rules.json")
KINDS = ("token", "narration", "rules", "actor", "actor_cs")      # actor_cs: the TM/PM patterns, upper case only; reported as actor
ASIDE_KIND = "aside"                  # English planning openers; reported as narration, skipped in an English chat
REPORTED_AS = {"aside": "narration", "actor_cs": "actor"}
LANGUAGE_KIND = "language"
WINDOW_HEAD = 12000                   # a long message is read at its head and its tail; the middle is rarely the leak
WINDOW_TAIL = 8000
COOLDOWN_S = 120                      # after a block, no new block in this chat for this long, whatever the host reports
MAX_LISTED = 6
INTERIM_COOLDOWN_S = 120              # R-520: one nudge per chat per this long
TAIL_BYTES = 262144                   # how much of the transcript tail is read to find the text before a tool call

_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"|“[^”\n]{0,400}”|«[^»\n]{0,400}»")
_BLOCKQUOTE = re.compile(r"^[ \t]*>.*$", re.M)
_NOISE = re.compile(
    r"(?<![\w/])(?:~|\.{1,2})?(?:/[\w.@+~-]+){2,}/?"                              # paths
    r"|\b[\w-]+\.(?:py|md|json|jsx?|tsx?|sh|html|css|txt|ya?ml|toml|jsonl|log)\b"      # file names
    r"|\b\w+_\w+\b|\b\w+-[0-9a-f]{8,}\b"                                            # snake_case names and hex-suffixed ids
    r"|\b[A-Z]-\d+\b"                                                                 # ledger and inbox ids (R-12, I-3)
    r"|\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")                                            # commit hashes
_WORD = re.compile(r"[^\W\d_]+")
_SINGLE_QUOTED = re.compile(r"(?<![\w’'])'[^'\n]{1,200}'(?![\w'])|‘[^’\n]{1,200}’")
_COMMIT_LINE = re.compile(
    r"^[ \t>*\u2022-]*(?:\d+[.)]\s+)?(?:(?=[0-9a-f]*\d)[0-9a-f]{7,40}\s+"                       # a short or full hash, then the title
    r"|(?:feat|fix|docs?|chore|refactor|tests?|perf|build|ci|style|revert|release)(?:\([^)\n]*\))?!?:\s"   # conventional-commit prefix
    r"|[A-Z]{1,3}-\d+:\s|release\s+v?\d[\w.]*:?\s)", re.I)
_BULLET = re.compile(r"^[ \t]*(?:[-*\u2022]|\d+[.)])\s+")
_COMMIT_WORD = re.compile(r"\bcommits?\b", re.I)

_cache = {}


def _valid(data):
    if not isinstance(data, dict) or not isinstance(data.get("markers"), dict) or not isinstance(data.get("language"), dict):
        return False
    lang = data["language"]
    for key in ("min_words", "min_hits", "min_ratio"):
        if not isinstance(lang.get(key), (int, float)) or isinstance(lang.get(key), bool):
            return False
    found = 0
    for kind, patterns in data["markers"].items():
        if not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns):
            return False
        found += len(patterns)
    return found > 0


def load_rules(path=None):
    """The rules file as a dict, or None when it is missing, unreadable, malformed or holds a pattern that does not compile."""
    path = path or RULES_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not _valid(data):
            return None
        sensitive = data.get("case_sensitive") if isinstance(data.get("case_sensitive"), list) else []
        for kind, patterns in data["markers"].items():
            for pattern in patterns:
                re.compile(pattern, re.M | (0 if kind in sensitive else re.I))
        return data
    except Exception:
        return None


def _compiled(path=None):
    """(rules, {kind: [compiled]}) for the rules file, cached by path and modification time; None when the file does not load."""
    path = path or RULES_PATH
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    rules = load_rules(path)
    result = None
    if rules is not None:
        sensitive = rules.get("case_sensitive") if isinstance(rules.get("case_sensitive"), list) else []
        result = (rules, {kind: [re.compile(p, re.M | (0 if kind in sensitive else re.I)) for p in patterns]
                          for kind, patterns in rules["markers"].items()})
    _cache[path] = (stamp, result)
    return result


def _blank_indented(text):
    """Markdown indented code (4 spaces or a tab, after a blank line) is code: it is blanked like a fence."""
    out, code, blank = [], False, True
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
            blank = True                      # a blank line inside indented code does not end it
            continue
        if (line.startswith("    ") or line.startswith("\t")) and (blank or code):
            code = True
            out.append("")
        else:
            code = False
            out.append(line)
        blank = False
    return "\n".join(out)


def _blank_commits(text):
    """Commit titles are quoted history, not the model's voice: lines that start with a hash, a conventional prefix or a ledger id,
    and the bullet list under a line that mentions commits, are blanked (a title may be English inside a Spanish reply)."""
    out, after = [], False
    for line in text.split("\n"):
        if _COMMIT_LINE.match(line) or (after and _BULLET.match(line)):
            out.append("")
            continue
        if line.strip():
            after = bool(_COMMIT_WORD.search(line))
        out.append(line)
    return "\n".join(out)


def _window(text):
    if len(text) <= WINDOW_HEAD + WINDOW_TAIL:
        return text
    return text[:WINDOW_HEAD] + "\n" + text[-WINDOW_TAIL:]


def _snippet(match):
    return " ".join(match.group(0).split())[:48]


def _language(masked, rules):
    """A short description when the prose is mostly English, else None. Quotes, block quotes, paths, ids and hashes do not count."""
    import humanizelint
    plain = _NOISE.sub(" ", _QUOTED.sub(" ", _BLOCKQUOTE.sub(" ", masked)))
    cfg = rules["language"]
    if len(_WORD.findall(plain)) < cfg["min_words"]:
        return None
    en = len(humanizelint.EN_WORDS.findall(plain))
    es = len(humanizelint.ES_WORDS.findall(plain))
    if en >= cfg["min_hits"] and en >= cfg["min_ratio"] * (en + es):
        return "%d/%d" % (en, en + es)
    return None


def check(text, lang=None, skip_language=False, rules_path=None):
    """-> [(kind, snippet)], empty when the message is clean. `lang` is the user's language: the language gate runs only for 'es', and the English planning openers are skipped when it is 'en'.
    `skip_language` is for a draft written for a third person, which may be in any language. Never raises."""
    try:
        if not isinstance(text, str) or not text.strip():
            return []
        got = _compiled(rules_path)
        if got is None:
            return []
        rules, patterns = got
        import humanizelint
        masked = humanizelint.mask(_window(text))
        body = _blank_commits(_blank_indented(masked))
        prose = _SINGLE_QUOTED.sub(" ", _QUOTED.sub(" ", _BLOCKQUOTE.sub(" ", body)))
        found, seen = [], set()
        for kind in KINDS + (ASIDE_KIND,):
            if kind == ASIDE_KIND and lang == "en":           # English planning openers are ordinary prose in an English chat
                continue
            source = masked if kind == "token" else prose
            reported = REPORTED_AS.get(kind, kind)
            for rx in patterns.get(kind, ()):
                m = rx.search(source)
                if m:
                    item = (reported, _snippet(m))
                    if item not in seen:
                        seen.add(item)
                        found.append(item)
        if lang == "es" and not skip_language:
            described = _language(body, rules)
            if described:
                found.append((LANGUAGE_KIND, described))
        return found
    except Exception:
        return []


def kind_label(kind, lang=None):
    import i18n
    return i18n.t("outguard.kind." + kind, lang)


def _cooldown_path(session, suffix=".outguard"):
    import studiolib as sl
    return os.path.join(sl.home_state(), "humanize", session + suffix)


def _arm(session, suffix=".outguard", window=COOLDOWN_S, digest=None):
    """True when this chat may be blocked now (and records the block); False inside the cooldown or when the marker cannot be kept.
    `digest` (R-520) also refuses the very same text twice, whatever the time."""
    import studiolib as sl
    if not sl.valid_session(session):
        return False
    path = _cooldown_path(session, suffix)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - float(data.get("ts") or 0) < window or (digest and data.get("h") == digest):
            return False
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sl.atomic_write(path, json.dumps({"ts": time.time(), "h": digest}) + "\n")
    except OSError:
        return False
    return True


def stop_reason(inp, lang=None, skip_language=False, rules_path=None):
    """Stop, every session. -> the block reason (in the user's language) when the last message holds working text, else None.
    Blocks at most once: a Stop already continued by a hook (stop_hook_active) passes, and so does any Stop inside the cooldown after
    a block. Fails open: any error is None."""
    try:
        if not isinstance(inp, dict) or inp.get("stop_hook_active"):
            return None
        text = inp.get("last_assistant_message")
        if not isinstance(text, str):
            return None
        import i18n
        lang = lang if lang in i18n.LANGS else i18n.detect()
        found = check(text, lang, skip_language, rules_path)
        if not found or not _arm(inp.get("session_id")):
            return None
        first = dict(reversed(found))                         # one example per kind
        listed = "; ".join('%s ("%s")' % (kind_label(kind, lang), snip) for kind, snip in first.items())
        return i18n.t("outguard.reason", lang, found=listed)
    except Exception:
        return None


# ---- R-520: the text a chat writes BETWEEN tool calls --------------------------------------------------------------------------
# It is on the user's screen before any hook runs, so it cannot be retracted. PostToolUse reads the transcript tail, finds the text block
# written just before the call that ran, and, when it holds working text, tells the model (model-only context) to stop. It never
# denies the call, nudges at most once per chat per INTERIM_COOLDOWN_S, and fails open.

def _entries(path):
    """[(role, message id, blocks)] for the transcript lines in the last TAIL_BYTES (main chat only); [] on any problem."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) and not entry.get("isSidechain") else None
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
        else:
            continue
        out.append((message.get("role") or entry.get("type"), message.get("id"), blocks))
    return out


def _texts(blocks, stop_at=None):
    """The text blocks of one message, up to the tool_use block `stop_at` when given."""
    got = []
    for b in blocks:
        if stop_at and b.get("type") == "tool_use" and b.get("id") == stop_at:
            break
        if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
            got.append(b["text"])
    return "\n".join(got)


def interim_text(inp):
    """The assistant text written just before the tool call of this PostToolUse payload, or "". With a tool_use_id the call is found
    in the transcript and only its own message is read; without one, the newest text of the current turn is used."""
    path = inp.get("transcript_path")
    if not isinstance(path, str):
        return ""
    entries = _entries(path)
    tid = inp.get("tool_use_id")
    if isinstance(tid, str) and tid:
        idx = next((i for i in range(len(entries) - 1, -1, -1)
                    if entries[i][0] == "assistant" and any(b.get("type") == "tool_use" and b.get("id") == tid for b in entries[i][2])), None)
        if idx is None:
            return ""
        _, mid, blocks = entries[idx]
        text = _texts(blocks, tid)
        j = idx - 1
        while not text and j >= 0:                              # parallel calls of one message sit on earlier lines
            role, other, blocks = entries[j]
            if role != "assistant" or other != mid:
                return ""
            text = _texts(blocks)
            j -= 1
        return text
    for role, _, blocks in reversed(entries):
        if role == "assistant":
            text = _texts(blocks)
            if text:
                return text
        elif not (role == "user" and any(b.get("type") == "tool_result" for b in blocks)):
            return ""                                           # a real prompt of the user: nothing of this turn before it
    return ""


# ---- the ready claim: "ready", "complete", "correct" about outward work needs an independent check from today ------------------
# Scope: only a project whose `.budgit/project.json` lists `protected_paths` (the marker the real-data guard relies on). Trigger:
# a ready or completeness word in Spanish, English or French, whole word, case-folded, unless a negation stands in the same clause.
# Pass: a verify file that `verifyfile.check` accepts, with the reply stating its exact N and M. Blocks once per user turn, with no
# time window; the rest of that turn it only leaves a note.

_READY_WORDS = re.compile(
    r"(?<![\w-])(?:"
    r"listos?|(?:est[aá]n?|estamos|ya|todo)\s+list[oa]s?|list[oa]s?\s+para|"
    r"(?:est[aá]n?|todo|ya)\s+(?:terminad|completad)[oa]s?|"
    r"(?:ya\s+)?puedes\s+(?:enviar|presentar|firmar|pagar|validar|mandar)|"
    r"est[aá]\s+todo|(?:est[aá]n?|es|todo)\s+complet[oa]s?|sin\s+errores|todo\s+correcto|"
    r"todo\s+hecho|ya\s+est[aá]\s+hecho|all\s+done|tout\s+est\s+fait|"
    r"revisad[oa]s?|verificad[oa]s?|ok\s+para\s+enviar|"
    r"ready(?:\s+(?:to|for)\s+(?:send|sending|file|filing|pay|payment|sign|signing|submit|submission|submit))?|all\s+set|"
    r"(?:is|are|now|fully|all|it['’]s|that['’]s|looks|seems)\s+(?:complete|completed)|verified|"
    r"nothing\s+(?:is\s+)?missing|no\s+errors|"
    r"pr[eê]t(?:e|s|es)?(?:\s+[àa]\s+\w+)?|tout\s+est\s+pr[eê]t|c['’]est\s+complet|v[ée]rifi[ée]e?s?"
    r")(?![\w-])", re.I)
_CLAUSE = re.compile(r"[.;:!?,\n—]|\s(?:pero|but|mais|aber|sino)\s", re.I)
_NEGATION = re.compile(
    r"(?<![\w-])(?:no|ni|nunca|jam[aá]s|not|never|nothing\s+is\s+ready|ne|pas|jamais|nicht)(?![\w-])|n['’]t(?![\w])|(?<![\w-])n['’]", re.I)


def _ready_hit(text):
    """True when the prose holds a ready or completeness word that no negation in its clause cancels."""
    import humanizelint
    masked = _blank_indented(humanizelint.mask(_window(text)))
    for clause in _CLAUSE.split(masked):
        for m in _READY_WORDS.finditer(clause):
            if m.group(0).lower().startswith(("nothing", "no errors")) or not _NEGATION.search(clause):
                return True
    return False


def _marked(project):
    import studiolib as sl
    return bool(sl.load_project_config(project).get("protected_paths"))


def _verified(project, reply, session):
    import studiolib as sl
    import verifyfile
    root = sl.main_project_root(project)
    seen = set()
    for folder in sl.verify_dirs(project):
        if folder in seen or not os.path.isdir(folder):
            continue
        seen.add(folder)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue                                        # one unreadable folder never hides the others
        for name in names:
            if name.endswith(".md") and verifyfile.check(os.path.join(folder, name), root, session, reply=reply)[0]:
                return True
    return False


def ready_claim(project, text, session, lang=None, active=False, turn=None):
    """Stop, every session. -> (reason, note): `reason` blocks the turn once per user turn (`turn` is `turn_key(inp)`; in the user's language), `note` is the system message
    when the turn is let go. (None, None) for an unmarked project, a reply without a ready word, or a passing verify file. Fails open."""
    try:
        if not project or not isinstance(text, str) or not _marked(project) or not _ready_hit(text):
            return None, None
        if _verified(project, text, session):
            return None, None
        import i18n
        lang = lang if lang in i18n.LANGS else i18n.detect()
        if callable(turn):                                  # computed only now: marker, word hit and a failed check come first
            turn = turn()
        if turn is None:                                    # no transcript: one block per distinct text
            turn = "text:" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        if active or _turn_seen(session, turn):
            return None, i18n.t("ready.note", lang)
        return i18n.t("ready.reason", lang), None
    except Exception:
        return None, None


def turn_key(inp):
    """A key for the user's current turn: a hash of the last real user entry's uuid (its timestamp when it has none) plus its text,
    so the same prompt typed in two turns still gives two keys. None when the transcript cannot be read."""
    try:
        path = inp.get("transcript_path")
        if not isinstance(path, str):
            return None
        for entry in reversed(_raw_tail(path)):
            message = entry.get("message") if isinstance(entry, dict) and not entry.get("isSidechain") else None
            if not isinstance(message, dict) or (message.get("role") or entry.get("type")) != "user":
                continue
            content = message.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else \
                [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            if any(b.get("type") == "tool_result" for b in blocks):
                continue
            prompt = _texts(blocks)
            if prompt:
                ident = entry.get("uuid") or entry.get("timestamp") or ""
                return "turn:" + hashlib.sha1((str(ident) + "\0" + prompt).encode("utf-8", "replace")).hexdigest()[:16]
        return None
    except Exception:
        return None


def _raw_tail(path):
    """The parsed transcript lines in the last TAIL_BYTES; [] on any problem."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _turn_seen(session, turn):
    """True when this chat was already blocked in this turn (or the state cannot be kept); otherwise records the turn and returns False.
    A new turn is blocked again at once: there is no time window."""
    import studiolib as sl
    if not sl.valid_session(session):
        return True
    path = _cooldown_path(session, ".ready")
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("turn") == turn:
                return True
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sl.atomic_write(path, json.dumps({"turn": turn}) + "\n")
    except OSError:
        return True
    return False


def _count(found):
    """Counts only, never the text: a small json in the machine state folder, read by the retro pass."""
    try:
        import studiolib as sl
        path = os.path.join(sl.home_state(), "outguard-counts.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["interim"] = int(data.get("interim") or 0) + 1
        kinds = data.get("interim_kinds") if isinstance(data.get("interim_kinds"), dict) else {}
        for kind in {k for k, _ in found}:
            kinds[kind] = int(kinds.get(kind) or 0) + 1
        data["interim_kinds"] = kinds
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sl.atomic_write(path, json.dumps(data, sort_keys=True) + "\n")
    except Exception:
        pass


def interim_note(inp, lang=None, rules_path=None):
    """PostToolUse, every session. -> model-only context (in the user's language) when the text written just before this tool call
    held working text, else None. Once per chat per INTERIM_COOLDOWN_S, and never twice for the same text. Fails open."""
    try:
        if not isinstance(inp, dict) or inp.get("agent_id"):    # a subagent's text is not on the user's screen
            return None
        text = interim_text(inp)
        if not text:
            return None
        import i18n
        lang = lang if lang in i18n.LANGS else i18n.detect()
        found = check(text, lang, False, rules_path)
        if not found:
            return None
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        if not _arm(inp.get("session_id"), ".interim", INTERIM_COOLDOWN_S, digest):
            return None
        _count(found)
        first = dict(reversed(found))
        listed = "; ".join('%s ("%s")' % (kind_label(kind, lang), snip) for kind, snip in first.items())
        return i18n.t("outguard.interim", lang, found=listed)
    except Exception:
        return None
