"""R-373: Budgit used to act only where a notebook (`.budgit`) already existed, so a chat opened anywhere else got none of its rules
(skill routing, input tracking, the humanize pass). Three mechanisms, none of them a rule the model has to remember:

1. The general notebook. A chat whose folder has no notebook is bound to one in the Budgit home (`<home>/general/`), outside every
   git repository and every export, marked private (its inputs can be emails). A folder that is protected or private, or outside
   the user's home, is never bound: it keeps the old behaviour.
2. The humanize pass. A prompt that asks for text meant for a person (a cheap regex, no model) adds the stop-slop routing and leaves a
   flag; the Stop hook lints the last answer once and, on findings, blocks once with them.
3. The notebook offer. SessionStart in a folder under a development folder (the configured projects_root, ~/Dev, ~/Developer, ~/Projects,
   ~/Code, or the same under ~/Documents) that has no notebook says so once, with the command.
   It never creates anything. Test and probe copies are never offered.
"""
import json
import os
import re
import shlex
import time
import unicodedata

import studiolib as sl

GENERAL = "general"
_DEV_NAMES = ("Dev", "Developer", "Projects", "Code")
OFFER_ROOTS = _DEV_NAMES + tuple(os.path.join("Documents", n) for n in _DEV_NAMES)
# a folder made to try something: `x-prueba-3`, `test`, `probe_2`, `app-local` (a local copy)
_SKIP_NAME = re.compile(r"(?:^|[-_. ])(?:prueba|pruebas|test|tests|probe|probes)(?:[-_. \d]|$)|(?:^|[-_. ])local$", re.I)
OFFERS_KEEP = 500


def general_project():
    return os.path.join(sl.home_state(), GENERAL)


def _inside(low, base):
    return low == base or low.startswith(base.rstrip(os.sep) + os.sep)


def _real_home():
    return os.path.realpath(os.path.expanduser("~"))


def _walks_up_to_a_repo(path):
    here = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(here, ".git")):
            return True
        parent = os.path.dirname(here)
        if parent == here:
            return False
        here = parent


# Values read from the installed host, not guessed: strings in the Claude Code 2.1.308 binary (/opt/homebrew/Caskroom/claude-code/2.1.308/claude),
# `entrypoint:"cli"` and the comparisons CLAUDE_CODE_ENTRYPOINT==="claude-desktop" / ==="claude-vscode". Seen there and NOT interactive:
# sdk-ts, sdk-py, sdk-cli, print (claude -p), local-agent, claude-code-github-action, bench, claude-in-teams, mcp. Seen there and left
# out because "a person is typing" is not confirmed by the binary: claude-desktop-3p. No JetBrains value exists in it, so none is listed:
# a host that reports another value gets no notebook (the safe side) until its value is confirmed and added.
INTERACTIVE_ENTRYPOINTS = ("cli", "claude-desktop", "claude-vscode")


def interactive():
    """True only for a session a person is typing in: the host says so in CLAUDE_CODE_ENTRYPOINT with a known interactive value (the
    terminal, the desktop app, an IDE extension). A missing or unknown entrypoint (`sdk-*`, `mcp`, a scheduled run, a host that sets
    none) never binds, nor does CLAUDE_CODE_SESSION_ATTENDED=0 or a cloud session: nothing of the general notebook, including its Stop
    ledger check, reaches a session nobody is watching."""
    env = os.environ
    entry = (env.get("CLAUDE_CODE_ENTRYPOINT") or "").strip().lower()
    attended = (env.get("CLAUDE_CODE_SESSION_ATTENDED") or "").strip().lower()
    return entry in INTERACTIVE_ENTRYPOINTS and attended not in ("0", "false", "no") and env.get("CLAUDE_CODE_REMOTE") != "true"


def _marked_private(real, home):
    """True when this folder or one above it (up to the home folder) holds a project.json that says `private`, whether or not it has
    a ledger yet, or one that cannot be read: a folder somebody marked private never takes the general notebook."""
    here = real
    while True:
        for name in (".budgit", ".studio"):
            cfg = os.path.join(here, name, "project.json")
            if os.path.lexists(cfg):
                try:
                    with open(cfg, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    return True
                if not isinstance(data, dict) or data.get("private") not in (None, False):
                    return True
        parent = os.path.dirname(here)
        if sl.fold_path(here) == sl.fold_path(home) or parent == here:
            return False
        here = parent


def _candidate(cwd):
    """The real path of a folder that has no notebook, is the user's own (inside the home folder, outside Budgit's) and is neither
    protected nor private; else None. Any doubt, any error: None (fail closed)."""
    try:
        import livework
        if not isinstance(cwd, str) or not cwd.strip():
            return None
        real = os.path.realpath(cwd)
        if not os.path.isdir(real):
            return None
        low = sl.fold_path(real)
        if not _inside(low, sl.fold_path(_real_home())) or _inside(low, sl.fold_path(os.path.realpath(sl.home_state()))):
            return None
        if sl.find_project(real) or not sl.config_readable() or not sl.protected_dirs_readable() or _marked_private(real, _real_home()):
            return None
        if sl.protected_dir(real) or livework.is_protected(real) or livework.is_private(real):
            return None
        return real
    except Exception:
        return None


def eligible(cwd):
    """True when a chat in `cwd` gets the general notebook. Never in a cloud or scripted session, never when the notebook would sit in a repo."""
    if not interactive() or not _candidate(cwd):
        return False
    try:
        return not _walks_up_to_a_repo(general_project())
    except Exception:
        return False


def _keep_private(project):
    sdir = sl.studio_dir(project)
    cfg = sl.load_project_config(project)
    if cfg.get("private") is not True:
        cfg["private"] = True
        sl.atomic_write(os.path.join(sdir, "project.json"), json.dumps(cfg, indent=2) + "\n")
    for d in (project, sdir):
        os.chmod(d, 0o700)


def bind(inp):
    """Bind the session of a hook payload to the general notebook when its folder qualifies. -> the notebook's path or None. Only the
    payload's own `cwd` counts: no cwd, no binding."""
    sid = inp.get("session_id")
    if not sl.valid_session(sid) or not eligible(inp.get("cwd")):
        return None
    project = general_project()
    try:
        os.makedirs(project, mode=0o700, exist_ok=True)
        sl.activate(project, sid, include_history=False)       # since-now: another chat's private inputs never nag this one
        _keep_private(project)
    except Exception:
        sl.log_error("general notebook")
        return None
    return project


# ---------------------------------------------------------------- the notebook offer

def _offered_path():
    return os.path.join(sl.home_state(), "offers.json")


def _already_offered(real):
    try:
        with open(_offered_path(), encoding="utf-8") as f:
            return real in (json.load(f).get("folders") or [])
    except (OSError, ValueError, AttributeError):
        return False


def _remember_offer(real):
    try:
        with open(_offered_path(), encoding="utf-8") as f:
            folders = [x for x in json.load(f).get("folders") or [] if isinstance(x, str)]
    except (OSError, ValueError, AttributeError):
        folders = []
    folders = (folders + [real])[-OFFERS_KEEP:]
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.atomic_write(_offered_path(), json.dumps({"folders": folders}) + "\n")


def is_general(project):
    return bool(project) and os.path.realpath(project) == os.path.realpath(general_project())


def offerable(project):
    """The offer is for a chat that has no notebook of its own: unbound, or bound only to the general one."""
    return not project or is_general(project)


ASKED_SESSIONS = 40       # sessions whose already-asked items are remembered
ASKED_PER_SESSION = 200


def forget_asked(state, session):
    """Nothing is left to ask about in this chat: a problem that comes back later is asked about again."""
    if isinstance(state.get("asked"), dict):
        state["asked"].pop(str(session), None)


def stop_reason(state, session, problems):
    """The Stop ledger check of every notebook asks ONCE per item and chat: -> the block reason for the problems this session has not
    been asked about yet (and remembers them in `state`), else None. An item left untracked stays flagged in the notebook (`budgit
    status`, the next session's summary) but never blocks again, so a host that never says the Stop was continued cannot loop it."""
    asked = state.get("asked") if isinstance(state.get("asked"), dict) else {}
    state["asked"] = asked
    key = str(session)
    seen = asked.pop(key, None)
    seen = [p for p in seen if isinstance(p, str) and p in problems] if isinstance(seen, list) else []   # a cleared problem is forgotten
    new = [p for p in problems if p not in seen]
    asked[key] = (seen + new)[-ASKED_PER_SESSION:]
    while len(asked) > ASKED_SESSIONS:
        asked.pop(next(iter(asked)))
    if not new:
        return None
    hints = []
    if any(" untracked" in p for p in new):
        hints.append("Triage each untracked input in one line, then finish your reply: do it now (a small fix in files already open), "
                     "fold it into an existing item (`budgit ledger link R-n --src I-n`), file it (`budgit ledger add --src I-n`, with a "
                     "version and a score) or drop it (`budgit ledger ack I-n --note \"covered by R-n\"`).")
    if any(" untracked" not in p for p in new):
        hints.append("Fix the others with the `budgit ledger` CLI.")
    return ("budgit ledger check: %d item(s) need an answer before this reply ends:\n- %s\n%s This asks once per item; an item left as "
            "it is stays flagged in the notebook." % (len(new), "\n- ".join(new), " ".join(hints)))


def offer(cwd):
    """-> (line, command) once for a folder under a development folder (OFFER_ROOTS, or the configured projects_root) with no notebook,
    else None. The folder is remembered when this answers, so the next session there hears nothing. Never creates a notebook."""
    real = _candidate(cwd) if interactive() else None
    if not real:
        return None
    low = sl.fold_path(real)
    home = _real_home()
    bases = [os.path.join(home, r) for r in OFFER_ROOTS]
    try:
        configured = sl.load_config().get("projects_root")
        if isinstance(configured, str) and configured.strip():
            bases.append(os.path.realpath(os.path.expanduser(configured)))
    except Exception:
        pass
    for base in bases:
        if low.startswith(sl.fold_path(base) + os.sep):
            parts = real.split(os.sep)[len(os.path.normpath(base).split(os.sep)):]
            break
    else:
        return None
    if any(_SKIP_NAME.search(p) for p in parts) or _already_offered(real):
        return None
    _remember_offer(real)
    import i18n
    command = "budgit activate %s" % shlex.quote(real)
    return i18n.t("general.offer", command=command), command


# ---------------------------------------------------------------- the humanize pass

def _plain(text):
    """Accents off (`prepárame` -> `preparame`), so one spelling of each word covers how people type it."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_V = (r"(?:write|draft|compose|reply|respond|answer|prepare|rewrite|redraft|word|polish"
      r"|escr[ií]b\w*|red[aá]ct\w*|respond\w*|contest\w*|prepar\w*|reescr[ií]b\w*|mej\w+|haz\w*|reecri\w*|verfass\w*"
      r"|[ée]cri\w*|r[ée]dig\w*|r[ée]pond\w*|schreib\w*|antwort\w*|scriv\w*|rispond\w*|escrev\w*)")
_N = (r"(?:e-?mails?|mails?|correos?|courriels?|cartas?|letters?|lettres?|lettera|lettere|mensajes?|messages?|nachricht(?:en)?|mensage(?:ns|m)|briefe?|messaggi\w*"
      r"|whatsapps?|dms?|tweets?|posts?|publicaci[óo]n(?:es)?|replies|reply|respuestas?|r[ée]ponses?|linkedin|newsletter"
      r"|comentarios?|comments?|bios?|cover letter|carta de presentaci[óo]n)")
_INTENT = re.compile(r"(?<!\w)(?:%s(?!\w)[^.\n]{0,60}?(?<!\w)%s|%s(?!\w)[^.\n]{0,60}?(?<!\w)%s)(?![\w-])" % (_V, _N, _N, _V), re.I)
# a code or tooling word inside the match means the message belongs to the program, not to a person
_CODE_WORDS = re.compile(r"(?<!\w)(?:tests?|pruebas?|function|funci[óo]n|handlers?|queues?|endpoints?|api|script|class|schema|hooks?"
                         r"|commits?|logs?|errors?|exceptions?|c[óo]digo|code|bugs?|parser|template|plantilla|stdout|stderr|console|terminal|funktion|funcao|testes?|erreurs?|errores?|errori|errore|fonction|funzione|funcion)(?!\w)", re.I)
# "the message of the commit", "un mensaje de error", "a message to stdout": the code word right after the noun
_AMBIGUOUS = {"api", "class", "schema", "script", "template", "plantilla", "hook", "hooks"}     # also modify people and firms ("the API vendor")
_CODE_AFTER = re.compile(r"\s*(?:de|del|d'|di|of|for|para|pour|von|to|a|en|in|into|on|al|per)\s*(?:the\s+|el\s+|la\s+|le\s+|les\s+|un\s+|una\s+|une\s+)?(?:%s)(?!\w)"
                         % "|".join(w for w in _CODE_WORDS.pattern[len("(?<!\\w)(?:"):-len(")(?!\\w)")].split("|") if w not in _AMBIGUOUS), re.I)
# the noun is itself a modifier of a program ("a mail server", "an email.py module")
_TECH_NEXT = re.compile(r"(?:\s+(?:server|servers|client|queue|parser|library|module|service|sender|validator|handler|app|endpoint|bot|daemon|template)\b|\.(?:py|js|ts|go|rb|rs|java|sh)\b)", re.I)
# the sentence before the request is about fixing or building code ("fix the bug where the reply message is empty")
_CODE_BEFORE = re.compile(r"(?<!\w)(?:fix|fixes|debug|refactor|implement|patch|arregla|corrige|depura|repara|implementa)(?!\w)|%s" % _CODE_WORDS.pattern, re.I)
HUMANIZE_ROUTE = ("budgit: this prompt asks for text meant for a person. Draft it, then humanize it before you show it: use the "
                  "stop-slop skill (Skill tool, skill `stop-slop`), keep the user's language, and show only the final text. A check "
                  "at the end of this turn lints the draft once.")
FLAG_MAX_AGE_S = 6 * 3600
MIN_WORDS = 25
MAX_LISTED = 8


# where one clause of a prompt ends and the request starts: punctuation, "then", "and", "y luego" ...
_CLAUSE_END = re.compile(r"[.;:!?,\n]|(?<!\w)(?:and|then|also|after that|y|e|et|und|puis|luego|despues|depois|poi|dann|ahora|now)(?!\w)", re.I)


def wants_person_text(prompt):
    """True when the prompt asks for an email, message, post or reply (any of en/es/fr/pt/de/it). A regex: no model, no file."""
    if not isinstance(prompt, str) or len(prompt) > 8000:
        return False
    text = _plain(prompt)
    m = _INTENT.search(text)
    if not m or _CODE_WORDS.search(m.group(0)) or _CODE_AFTER.match(text, m.end()) or _TECH_NEXT.match(text, m.end()):
        return False
    before = _CLAUSE_END.split(text[max(0, m.start() - 60):m.start()])[-1]
    return not _CODE_BEFORE.search(before)


# R-375: text for a client or another third party. Read on the accent-free prompt, after wants_person_text() said it is a text request.
_CLIENT = re.compile(r"(?<!\w)(?:clients?(?![\s-]+(?:librar|side|app\b|sdk|code|script|server|cert|config))|customers?|client[ea]s?|propuestas?|"
                     r"proposals?|kunden?|prospects?|stakeholders?|vendors?|proveedor(?:es)?|supplier|suppliers|proveedora|lawyers?|abogad[oa]s?)(?!\w)", re.I)
# R-375: an assessment of versions, support dates or vulnerabilities: a lifecycle word AND a version (a number or the word)
_LIFE = re.compile(r"(?<!\w)(?:eol|end[- ]of[- ]life|end[- ]of[- ]support|fin de vida|support(?:ed|s)?|soporte|soportad[oa]s?|lifecycle|"
                   r"life cycle|ciclo de vida|cves?|vulnerab\w*|rhsa|advisor(?:y|ies)|patch\w*|parche\w*|compatib\w*|compatible)(?!\w)", re.I)
_VERSION = re.compile(r"(?<!\w)(?:versions?|versiones|versao|versoes|ubi|rhel|jdk|jre|python|java|node(?:\.?js)?|\w+\s?v?\d+(?:\.\d+)+|\w{2,}\s\d{1,2})(?!\w)", re.I)
CLIENT_ROUTE = ("budgit: this text goes to a client or another third party, so it passes a gate before delivery: stop-slop, every date "
                "and figure checked at its primary source in this session, and a fresh-context verifier. The check at the end of the "
                "turn tells you exactly what it needs; do not skip the verifier and do not deliver from memory.")
LENS_ROUTE = ("budgit versions/EOL lens: an assessment of versions or support covers dates AND vulnerabilities (latest quarterly patch, "
              "emergency alerts, patch level of the image or package). Read each date at the primary source in this session. Cite the "
              "exact section and row of a lifecycle table and warn when the same version has another date in another variant (RHEL "
              "8/9/10, Red Hat vs Oracle JDK, portable vs package). Say where to look at the link; a table a script fills in is not "
              "visible. Write what depends on a contract or is unconfirmed as a condition. Say fixed/patched/resolved only with an "
              "advisory the reader can open (RHSA, GHSA, USN id or URL); otherwise describe the mechanism and name the tracking id.")


def wants_client_text(prompt):
    """True when the prompt asks for text meant for a person AND names a client or other third party (client, customer, formal, proposal)."""
    return bool(wants_person_text(prompt) and _CLIENT.search(_plain(prompt)))


def wants_versions_lens(prompt):
    """True when the prompt is about versions, support dates or vulnerabilities (a lifecycle word and a version). Any chat; a regex."""
    if not isinstance(prompt, str) or len(prompt) > 8000:
        return False
    text = _plain(prompt)[:2000]                    # a prompt about versions says so early; a long run of digits is not scanned whole
    return bool(_LIFE.search(text) and _VERSION.search(text))


def _flag_path(session):
    return os.path.join(sl.home_state(), "humanize", session + ".flag")


def _client_path(session):
    return os.path.join(sl.home_state(), "humanize", session + ".client")


def _gate_path(session):
    return os.path.join(sl.home_state(), "humanize", session + ".gate")


def _done_path(session):
    return os.path.join(sl.home_state(), "humanize", session + ".done")


def _clear(path):
    try:
        os.remove(path)
    except OSError:
        pass


def on_prompt(inp):
    """UserPromptSubmit, every session. -> the routing text when the prompt asks for text for a person (and leaves the flag), else None."""
    session = inp.get("session_id")
    if not sl.valid_session(session):
        return None
    flag = _flag_path(session)
    armed = _armed(flag)                            # a client request still waiting for its draft (the user answered a question)
    _clear(_done_path(session))                     # a new request: the humanize check may block once again
    _clear(_gate_path(session))                     # ... and so may the client-text gate
    prompt = inp.get("prompt")
    routes = [LENS_ROUTE] if wants_versions_lens(prompt) else []
    if not wants_person_text(prompt):
        if armed:                                   # "para el equipo técnico de Acme": the answer is not a new request, the old one goes on
            routes.insert(0, CLIENT_ROUTE)
        else:
            _clear(flag)
        return "\n".join(routes) or None
    client = wants_client_text(prompt)
    ts, left = time.time(), CLIENT_WINDOW
    if armed and not client:
        client, ts, left = True, float(armed.get("ts") or ts), min(int(armed["left"]), CLIENT_WINDOW)
    os.makedirs(os.path.dirname(flag), exist_ok=True)
    sl.atomic_write(flag, json.dumps({"ts": ts, "client": client, "left": left}) + "\n")
    if client:                                      # sticky: "make it shorter" is not a new request, but it is still client text
        sl.atomic_write(_client_path(session), json.dumps({"ts": time.time()}) + "\n")
        routes.insert(0, CLIENT_ROUTE)
    return "\n".join([HUMANIZE_ROUTE] + routes)


def _last_assistant_text(inp):
    msg = inp.get("last_assistant_message")
    if isinstance(msg, str) and msg.strip():
        return msg
    path = inp.get("transcript_path")
    if not isinstance(path, str):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 262144))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict) or (message.get("role") or entry.get("type")) != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        if text.strip():
            return text
    return ""


_SUBJECT = re.compile(r"^[ \t>*_#-]*(?:subject|asunto|objet|betreff|oggetto|assunto)[ \t*_]*:", re.I | re.M)
_GREETING = re.compile(r"^[ \t>*_#-]*(?:dear|hi|hello|hey|hola|estimad[oa]s?|buenos dias|buenas tardes|buenas noches|bonjour|cher|chere|hallo|"
                       r"sehr geehrte\w*|liebe\w*|ciao|gentile|caro|cara|ola|prezad[oa]s?|querid[oa]s?)\b[^\n]{0,60}$", re.I)
_SIGNOFF = re.compile(r"^[ \t>*_#-]*(?:best(?: regards| wishes)?|kind regards|warm regards|regards|sincerely|yours (?:truly|sincerely|faithfully)|thanks|thank you|cheers|"
                      r"saludos|un saludo|atentamente|cordialmente|cordiales saludos|un abrazo|gracias|cordialement|bien cordialement|"
                      r"mit freundlichen grue?(?:ss|\u00df)en|viele grue?(?:ss|\u00df)e|liebe grue?(?:ss|\u00df)e|cordiali saluti|distinti saluti|atenciosamente|abracos)\b[^\n]{0,40}$", re.I)


def letter_shape(text):
    """True when an answer reads like a letter or email: a Subject/Asunto line, or a greeting near the top with a sign-off near the
    bottom. Decided on the answer itself, so a request the prompt check missed still gets the lint."""
    import humanizelint
    plain = _plain(humanizelint.mask(text))
    if _SUBJECT.search(plain):
        return True
    lines = [ln for ln in plain.splitlines() if ln.strip()]
    return (any(_GREETING.match(ln.rstrip(" ,:")) for ln in lines[:5])
            and any(_SIGNOFF.match(ln.rstrip(" ,.!")) for ln in lines[-6:]))


CLIENT_WINDOW = 3            # replies after a client request during which a clarifying reply does not use the request up
SHORT_REPLY_WORDS = 80
QUESTION_REPLY_WORDS = 150


QUESTION_RISKY_WORDS = 50    # a question mentioning a version is a question; a paragraph of 50+ words with dates that ends in "?" is a draft


def _armed(flag):
    """-> the client flag when the request is still armed: a clarifying reply already left it standing (so the prompt now is the user's
    answer), it is fresh and it has replies left. Else None."""
    data = _read_json(flag)
    try:
        if data and data.get("client") and 0 < int(data.get("left")) < CLIENT_WINDOW and time.time() - float(data.get("ts") or 0) <= FLAG_MAX_AGE_S:
            return data
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def clarifying(text):
    """True for a reply that is not the draft yet: a question to the user or a short note, with no letter shape and (when short) nothing in it
    that can be wrong. R-375: the client request stays armed through such replies."""
    import humanizelint
    if letter_shape(text):
        return False
    words = len(re.findall(r"\w+", humanizelint.mask(text)))
    if text.rstrip().endswith(("?", "？")):
        return words < QUESTION_REPLY_WORDS and not (words >= QUESTION_RISKY_WORDS and risky_content(text))
    return words < SHORT_REPLY_WORDS and not (words >= MIN_WORDS and risky_content(text))


def _defer_client(flag, data, text):
    """The client flag survives a clarifying reply, up to CLIENT_WINDOW replies in all. -> True when this Stop leaves the request alone
    (armed, or at the end of the window with nothing to check in a clarifying reply)."""
    if not clarifying(text):
        return False
    try:
        left = min(int(data.get("left", CLIENT_WINDOW)), CLIENT_WINDOW) - 1
    except (TypeError, ValueError, OverflowError):
        left = 0
    if left <= 0:
        _clear(flag)                                            # the window is over; a question at its end is not gated
        return True
    data["left"] = left
    sl.atomic_write(flag, json.dumps(data) + "\n")
    return True


def humanize_stop(inp, info=None):
    """Stop, every session. Lints the last answer once when the prompt asked for text for a person (the flag it left, consumed here) or
    the answer itself is shaped like a letter or email. -> the block reason, or None. It never blocks a second time: the flag is gone
    after the first Stop, and a Stop already continued by a hook (stop_hook_active) is left alone."""
    session = inp.get("session_id")
    if not sl.valid_session(session):
        return None
    flag = _flag_path(session)
    flagged = False
    try:
        with open(flag, encoding="utf-8") as f:
            data = json.load(f)
            flagged = time.time() - float(data.get("ts") or 0) <= FLAG_MAX_AGE_S
            if flagged and data.get("client") and _defer_client(flag, data, _last_assistant_text(inp)):
                return None                                     # a clarifying reply: the flag stays armed for the real draft
            if flagged and info is not None:
                info["client"] = bool(data.get("client"))       # the client-text gate reads this: the flag is consumed here
                info["ts"] = float(data.get("ts") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        os.remove(flag)
    except OSError:
        pass
    if inp.get("stop_hook_active") or os.path.lexists(_done_path(session)):     # once per request, whatever the host reports
        return None
    if flagged:
        text = _last_assistant_text(inp)
    else:                                   # an ordinary turn: only the message the host hands over, no file read
        text = inp.get("last_assistant_message")
        text = text if isinstance(text, str) else ""
    import humanizelint
    if len(re.findall(r"\w+", humanizelint.mask(text))) < MIN_WORDS or not (flagged or letter_shape(text)):
        return None
    seen, rows = set(), []
    for f in humanizelint.scan(text, "auto", "ui"):
        key = (f.rule, f.match.lower())
        if key not in seen:
            seen.add(key)
            rows.append("- %s %r (line %d): %s" % (f.rule, f.match, f.line, f.message))
    if not rows:
        return None
    try:
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        sl.atomic_write(_done_path(session), json.dumps({"ts": time.time()}) + "\n")
    except OSError:
        pass
    more = "" if len(rows) <= MAX_LISTED else "\n(+%d more)" % (len(rows) - MAX_LISTED)
    return ("budgit humanize check: the text you drafted for a person has %d writing finding(s):\n%s%s\n"
            "Revise the draft with the stop-slop skill and show only the final text. This check runs once; do not mention it."
            % (len(rows), "\n".join(rows[:MAX_LISTED]), more))


# ---------------------------------------------------------------- R-375 / R-378: the client-text gate

GATE_MAX_BLOCKS = 2
REPORT_LABELS = ("Dates and figures", "Lifecycle rows", "Visible at link", "Conditions", "Fixed claims")
# "fixed" is a claim the reader can check only with an advisory: a sentence that says it must carry one (R-378)
_FIXED = re.compile(r"(?<!\w)(?:fix(?:ed|es)?(?![\s-]+(?:price|fee|rate|cost|term|amount|date|deadline|asset|income|salary|budget|charge|"
                    r"period|precio|tarifa|plazo|importe))|patch(?:ed|es)?|resolved|remediated|mitigated|corregid[oa]s?|corrige|corrigio|"
                    r"parchead[oa]s?|parchea|solucionad[oa]s?|soluciona|solucion[oa]|resuelt[oa]s?|se arregl[oa]n?|arreglad[oa]s?|subsanad[oa]s?|"
                    r"mitigad[oa]s?|behoben|behebt)(?!\w)", re.I)
# an advisory id, a URL, or a version number (a trailing full stop is fine; a date such as 2025.10.27 or 27.10.2025 is not a version)
_ADVISORY = re.compile(r"RH[SBE]A-\d{4}:\d+|GHSA(?:-[a-z0-9]{4}){3}|USN-\d+-\d+|DSA-\d+(?:-\d+)?|ALAS\d?-\d{4}-\d+|https?://|"
                       r"(?<![\d.])(?!(?:19|20)\d\d\.\d{1,2}\.\d{1,2}(?!\d))(?!\d{1,2}\.\d{1,2}\.(?:19|20)\d\d(?!\d))"
                       r"\d+\.\d+\.\d+(?:\.\d+)*(?!\d|\.\d)", re.I)
_FLAGGED = re.compile(r"^[ \t>*_#-]*(?:flagged|partial|unconfirmed)[ \t*_]*:[ \t]*(\S.*)$", re.I | re.M)
GATE_INSTRUCTIONS = (
    "budgit client-text gate: this answer is text for a client or another third party. Before it goes out:\n"
    "1. stop-slop has run on it (the humanize check).\n"
    "2. Every date, version and figure is checked at its primary source in this session (the vendor's lifecycle page or advisory), never "
    "from memory. A lifecycle table is cited by exact section and row, with a warning when the same version has another date in another "
    "variant (RHEL 8/9/10, Red Hat vs Oracle JDK, portable vs package). Each cited date is visible at the link and the text says where "
    "to look (a table a script fills in does not count). What depends on a contract or is unconfirmed is written as a condition. "
    "Fixed/patched/resolved appears only with an advisory the reader can open (RHSA, GHSA, USN id or URL); otherwise describe the "
    "mechanism and name the tracking id.\n"
    "3. A fresh-context verifier (Agent tool, subagent_type budgit:verifier) reads the exact draft against those sources and writes "
    "%(report)s with the Write tool: first line PASS or FAIL, then one non-empty line for each of %(labels)s (each as `Label: ...`), "
    "and a line `- flagged: <the sentence, verbatim>` for every sentence that is partial or unconfirmed.\n"
    "4. Reword each flagged sentence (or turn it into a condition), then show only the final text.\n"
    "This gate blocks at most %(max)d times.")
GATE_ITEMS = {
    "nonotebook": "this folder has no notebook, so a sealed verifier report cannot exist: say in your reply what the verifier checked",
    "report": "no sealed verifier report written after this request (a report written any other way is not sealed)",
    "verdict": "the verifier did not answer PASS",
    "labels": "the report does not answer",
    "flagged": "the verifier flagged these sentences and they are still in the text",
    "fixed": "'fixed/patched/resolved' without an advisory the reader can open",
}


# R-375 trigger B: the reply itself reads like a letter, whatever the prompt said. With something in it that can be wrong (a date, a
# version, a CVE or support word, a "fixed" claim) it gets the full gate; without, one light question and no verifier.
_MONTHS = (r"january|february|march|april|may|june|july|august|september|october|november|december|enero|febrero|marzo|abril|mayo|junio|julio|"
           r"agosto|septiembre|setiembre|octubre|noviembre|diciembre|janvier|fevrier|mars|avril|juin|juillet|aout|septembre|decembre|"
           r"januar|februar|maerz|marz|mai|juli|dezember|janeiro|fevereiro|maio|junho|julho|setembro|outubro|novembro|dezembro|"
           r"gennaio|febbraio|aprile|maggio|giugno|luglio|settembre|ottobre|dicembre|"
           r"jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec|ene|abr|ago|dic")      # abbreviations count only next to a day number
# a date or version, an advisory id, a quarter, a strong lifecycle word on its own (EOL, CVE, end of life/support)
_RISKY = re.compile(r"(?<!\w)(?:(?:19|20)\d{2}|cve-\d{4}-\d+|rh[sbe]a-\d{4}:\d+|ghsa(?:-[a-z0-9]{4}){3}|\d+\.\d+\.\d+|"
                    r"\d{1,2}[/.-]\d{1,2}[/.-](?:\d{2}|\d{4})|\d{1,2}/\d{1,2}(?![\d/])|q[1-4]|"
                    r"\d{1,2}(?:st|nd|rd|th)?\.?\s+(?:de\s+|of\s+)?(?:%s)|(?:%s)\.?\s+\d{1,2}(?:st|nd|rd|th)?|"
                    r"(?:java(?:\s+se)?|jdk|jre|python|node(?:\.?js)?|rhel|ubi|ubuntu|debian|windows|macos|ios|android|php|ruby|openssl|kernel|"
                    r"linux|tomcat|spring boot|log4j|django|rails|postgres(?:ql)?|mysql|nginx|jboss|wildfly|temurin|"
                    r"version|versions|versiones)\s?v?\d+(?:\.\d+)*|v\d+(?:\.\d+)*|"
                    r"eol|end[- ]of[- ](?:life|support)|fin de (?:vida|soporte)|cves?|rhsa|lts|trimestre|quarter)(?!\w)" % (_MONTHS, _MONTHS), re.I)
LIGHT_REASON = ("budgit client-text check: this reply reads like a letter or email. If it goes to someone other than the user, check every "
                "date, figure and claim in it at its source before delivering it, and write what you could not confirm as a condition; if it "
                "is for the user, carry on. This question is asked once per request.")


def risky_content(text):
    """True when the text holds something that can be wrong in front of a client: a year or date, a version, an advisory or CVE id, a
    lifecycle word with a version, or a 'fixed' claim."""
    plain = _plain(text[:20000])
    return bool(_RISKY.search(plain) or _FIXED.search(plain) or wants_versions_lens(plain))


def classify_reply(text):
    """What trigger B does with a reply when the prompt did not mark client text: 'full', 'light' or None."""
    if not isinstance(text, str) or not letter_shape(text):
        return None
    return "full" if risky_content(text) else "light"


def _norm_words(text):
    return " ".join(re.findall(r"\w+", _plain(text).lower()))


def fixed_without_advisory(text):
    """Sentences that say something is fixed, patched or resolved without an advisory the reader can open (R-378)."""
    import humanizelint
    rows = []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)           # the raw text: a URL or `17.0.9` is the advisory, masking would hide it
    for i, sentence in enumerate(sentences):
        # the advisory may sit in the sentence itself or in the one right after it ("... fixed. See RHSA-2025:1234.")
        if _FIXED.search(_plain(humanizelint.mask(sentence))) and not _ADVISORY.search(" ".join(sentences[i:i + 2])):
            rows.append(" ".join(sentence.split())[:160])
    return rows


def _read_report(path, since):
    """-> (verdict, labels present, flagged sentences), or None when there is no sealed report written after `since`."""
    try:
        sealed = sl.sealed_at(path)               # when the verifier's write was sealed: a touch of an old sealed report changes nothing
        if os.path.islink(path) or sealed is None or sealed < since:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read(65536)
    except OSError:
        return None
    verdict = (raw.split("\n", 1)[0] or "").strip().strip("*#` ").upper()
    labels = {lab for lab in REPORT_LABELS
              if re.search(r"^[ \t>*_#-]*%s[ \t*_]*:[ \t]*\S" % re.escape(lab), raw, re.I | re.M)}
    flagged = [m.group(1).strip().strip("\"'«»“”` ") for m in _FLAGGED.finditer(raw)]
    return verdict, labels, flagged


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save_gate(session, state):
    try:
        os.makedirs(os.path.dirname(_gate_path(session)), exist_ok=True)
        sl.atomic_write(_gate_path(session), json.dumps(state) + "\n")
    except OSError:
        pass


def gate_stop(inp, project, info):
    """Stop, every session. For text that goes to a client (the prompt named one, or a follow-up to such a request still reads like a
    letter), it needs a sealed verifier report written after the request: PASS, the five checklist lines answered, none of the
    sentences it flagged still in the text, and no 'fixed' without an advisory. -> (block reason, user note); at most one of the two.
    It blocks at most GATE_MAX_BLOCKS times (a counter in its own file, whatever the host reports), then lets the text go with a note
    to the user saying what was not done. A session with no notebook has nowhere for the sealed report: it is told once and noted."""
    session = inp.get("session_id")
    if not sl.valid_session(session):
        return None, None
    state = _read_json(_gate_path(session))
    sticky = _read_json(_client_path(session))
    try:
        fresh_sticky = bool(sticky) and time.time() - float(sticky.get("ts") or 0) <= FLAG_MAX_AGE_S
    except (TypeError, ValueError):
        fresh_sticky = False
    light = state is not None and bool(state.get("light"))
    if state is not None or info.get("client") or fresh_sticky:
        text = _last_assistant_text(inp)
    else:                                                   # trigger B: no client word in the prompt, only the message the host hands over
        text = inp.get("last_assistant_message")
        text = text if isinstance(text, str) else ""
        if len(text) > 6000:                                # a letter's shape is in its head and tail: never scan a huge reply whole
            text = text[:4000] + "\n" + text[-2000:]
    import humanizelint
    risky_letter = letter_shape(text) and risky_content(text)       # a one-line email with a date is the case the gate exists for
    if len(re.findall(r"\w+", humanizelint.mask(text))) < MIN_WORDS and not risky_letter:
        return None, None
    if light:
        if not risky_letter:
            return None, None                               # the one light question of this request was already asked
        state = {"since": time.time(), "blocks": 0}         # the text now carries a date, a version or a claim: the full gate applies
    elif state is None:
        if info.get("client"):
            state = {"since": float(info.get("ts") or time.time()), "blocks": 0}
        elif letter_shape(text):
            if not (fresh_sticky or risky_content(text)):   # a letter with nothing in it that can be wrong: one question, no verifier
                _save_gate(session, {"light": True, "blocks": 1, "since": time.time()})
                return LIGHT_REASON, None
            state = {"since": time.time(), "blocks": 0}
        else:
            return None, None
    try:
        since = float(state.get("since") or 0)
        blocks = int(state.get("blocks") or 0)
    except (TypeError, ValueError):
        since, blocks = 0.0, GATE_MAX_BLOCKS
    report = os.path.join(sl.resolve_data_dir(project), "verify", "client-%s.md" % session) if project else None
    missing, concrete = [], False
    parsed = _read_report(report, since) if report else None
    if report is None:
        missing.append(("nonotebook", ""))
    elif parsed is None:
        missing.append(("report", ""))
        concrete = os.path.exists(report)         # it tried: a file is there, but unsealed or older than this request
    else:
        verdict, labels, flagged = parsed
        concrete = True
        if verdict != "PASS":
            missing.append(("verdict", verdict[:20] or "?"))
        absent = [lab for lab in REPORT_LABELS if lab not in labels]
        if absent:
            missing.append(("labels", ", ".join(absent)))
        body = _norm_words(text)
        # whole words, any case; a flagged value of under 3 words ("none", "n/a", "2") is not a sentence to look for
        still = [s for s in flagged if len(_norm_words(s).split()) >= 3 and (" %s " % _norm_words(s)) in (" %s " % body)]
        if still:
            missing.append(("flagged", "; ".join("«%s»" % s[:100] for s in still[:3])))
    fixed = fixed_without_advisory(text)
    if fixed:
        concrete = True
        missing.append(("fixed", "; ".join("«%s»" % s[:100] for s in fixed[:3])))
    if not missing:
        _save_gate(session, state)
        return None, None
    if blocks < GATE_MAX_BLOCKS and (blocks == 0 or concrete):
        state["blocks"] = blocks + 1
        _save_gate(session, state)
        lines = ["- %s%s" % (GATE_ITEMS[code], (": " + detail) if detail else "") for code, detail in missing]
        head = ""
        if blocks == 0:
            head = GATE_INSTRUCTIONS % {"report": report or "its verdict in your reply (this folder has no notebook to seal a report in)",
                                        "labels": ", ".join(REPORT_LABELS), "max": GATE_MAX_BLOCKS} + "\n\n"
        return head + "Still missing:\n" + "\n".join(lines), None
    _save_gate(session, state)
    import i18n
    items = "; ".join(i18n.t("gate.item." + code) + ((": " + detail) if detail else "") for code, detail in missing)
    return None, i18n.t("gate.note", items=items)


def stop_checks(inp, project=None):
    """Stop, every session: the humanize check, then the client-text gate. -> (block reason or None, note for the user or None)."""
    info = {}
    reason = humanize_stop(inp, info)
    gate_reason, note = gate_stop(inp, project, info)
    if gate_reason:
        reason = (reason + "\n\n" + gate_reason) if reason else gate_reason
    guard = _output_guard(inp, info)
    if guard:
        reason = (reason + "\n\n" + guard) if reason else guard
    return reason, (None if reason else note)


def _output_guard(inp, info):
    """R-506: working text in the last message (the reason, or None). A draft for a third person may be in any language. Fails open."""
    try:
        import outguard
        text = inp.get("last_assistant_message")
        return outguard.stop_reason(inp, skip_language=bool(info) or (isinstance(text, str) and letter_shape(text)))
    except Exception:
        return None
