"""R-849: an Agent spawn of an all-tools type starts at 65-79k fixed context (Budgit agents at 21-27k), so it is refused
unless the prompt carries a line `ALL-TOOLS: <reason>`. R-1125: a brief with a tax or legal word is refused unless it carries
the line `LEGAL: legal-eu-es-fr panel checker` or `LEGAL: none - <reason>`. The decision comes from the payload alone and
nothing here writes a file: the audit text of a `LEGAL: none` brief is appended to the caller's `notes` list, and the hook
writes it with its other bookkeeping."""
import re
import unicodedata

import studiolib as sl

INSTEAD = ("builder for code and fixes, verifier for verification, researcher for research, plan-critic for plans, "
           "security-reviewer for security, scout-reader for reading")
_LINE = re.compile(r"(?m)^\s*ALL-TOOLS:\s*\S")
_DASH = re.compile(r"^(?:budgit|studio)-(.+)$")


def is_budgit_agent(kind):
    """True only for a canonical Budgit agent: `budgit:X` or `studio:X` (X in AGENT_NAMES), or the cloud `budgit-X` /
    `studio-X` copies. Anything else (other plugins, odd suffixes, other case) is an all-tools type."""
    if not isinstance(kind, str):
        return False
    canon = sl.canon_agent(kind)
    if canon.startswith("budgit:") and canon[len("budgit:"):] in sl.AGENT_NAMES:
        return True
    m = _DASH.match(kind)
    return bool(m and m.group(1) in sl.AGENT_NAMES)


def has_reason(prompt):
    return bool(_LINE.search(str(prompt or "")))


class LegalCheckError(Exception):
    """The legal check itself failed (not the all-tools check): the hook names it in its notice."""


def _nocf(text):
    return "".join(c for c in str(text or "") if unicodedata.category(c) != "Cf")


def _clean(text, cap):
    """One audit field: whitespace controls (newline, CR, tab) become a space, every other Unicode Cc and Cf character (ESC,
    BEL, DEL, NUL, zero-width, soft hyphen) is dropped, whitespace is collapsed, the length is capped."""
    kept = "".join(" " if c.isspace() else c for c in str(text or "") if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(kept.split())[:cap]


def _fold(text):
    """Lower case, accents and every Unicode format character (category Cf: zero-width characters, the soft hyphen) removed, so
    «Autónomo», «AUTONOMO», «déductible» / «deductible» and «T<zero-width>VA» compare equal."""
    d = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in d if not unicodedata.combining(c) and unicodedata.category(c) != "Cf").casefold()


_SPELLED = ("iva", "tva", "vat")       # also matched with a dot or hyphen between the letters: I.V.A., T-V-A, V.A.T.


def _word(term):
    folded = _fold(term)
    if folded in _SPELLED:
        body = r"[.\-]?".join(re.escape(c) for c in folded)
    else:
        body = r"\s+".join(re.escape(_fold(p)) for p in term.split())
    return r"(?<!\w)" + body + r"(?:e?s)?(?!\w)"


# R-1125: the tax and legal words of the legal-eu-es-fr skill. One place: a brief with any of them needs a LEGAL: line. Whole
# words, case and accents folded (a plain plural is accepted: taxes, impuestos). Plain numbers and years never trigger. Not
# listed on purpose: «factura» (only with IVA or impuesto), «filing» (only with tax or VAT) and a bare «2035»: the partner word
# is already in this list, so each of those triggers through it; only the 2035 form names are listed. Not listed either:
# «autónomo» and «RETA» alone (autonomous agent, the verb «retar»); only the tax phrases with «autónomo» are.
LEGAL_WORDS = (
    "IVA", "IRPF", "trabajador autónomo", "cuota de autónomo", "alta de autónomo", "impuesto", "Hacienda", "AEAT",
    "retención", "deducible", "cotización", "residencia fiscal", "LOPDGDD", "RGPD",
    "TVA", "BNC", "URSSAF", "CA3", "CA12", "déductible", "impôt", "micro-entrepreneur", "auto-entrepreneur", "autoentrepreneur",
    "SIRET", "liasse", "CFE", "2035-A", "2035-B", "formulaire 2035", "déclaration 2035", "déclaration de revenus", "CNIL",
    "tax", "VAT", "income tax", "self-employed", "self employed", "GDPR",      # EN «deductible» folds to FR «déductible» above
)
# Every form of the Spanish tax-model names, on folded text: «modelo 303», «modelo nº 130» (º folds to o), «modelo n.º 100»,
# «modelo no 036», «modelo 037». A year or a longer number never matches.
_MODELO = r"(?<!\w)modelo\s+(?:n\.?o\s*)?(?P<num>303|130|100|036|037)(?!\w)"
_LEGAL = re.compile("|".join("(?P<w%d>%s)" % (i, _word(w)) for i, w in enumerate(LEGAL_WORDS)) + "|(?P<modelo>%s)" % _MODELO)
_ROUTE = re.compile(r"\s*legal:\s*legal-eu-es-fr\s+panel\s+checker\W*")       # the whole line, in this order, case folded
_LEGAL_LINE = re.compile(r"\s*legal:[ \t]*(.*)")
_NONE = re.compile(r"none[ \t]*[-–—][ \t]*(.*\w.*)")        # `none - <reason>`: the reason must hold a letter or digit


def legal_word(prompt):
    """The first listed tax or legal word the prompt contains (as the list spells it), else None."""
    m = _LEGAL.search(_fold(prompt))
    if not m:
        return None
    return "modelo " + m.group("num") if m.lastgroup == "modelo" else LEGAL_WORDS[int(m.lastgroup[1:])]


def legal_route(prompt):
    """What the LEGAL: lines of a prompt say: True for the exact line `LEGAL: legal-eu-es-fr panel checker`, the reason text
    for `LEGAL: none - <reason>` (non-empty), else None. A route line wins over a none line."""
    reason = None
    for line in str(prompt or "").splitlines():
        folded = _fold(line)
        if _ROUTE.fullmatch(folded):
            return True
        m = _LEGAL_LINE.fullmatch(folded)
        n = _NONE.fullmatch(m.group(1).strip()) if m else None
        if n and reason is None:
            shown = re.search(r"(?i)none[ \t]*[-–—][ \t]*(.*)", _nocf(line))
            reason = _clean(shown.group(1) if shown else n.group(1), 200)
    return reason


def legal_reason(tool_input, notes=None):
    """R-1125: the deny text when the brief holds a tax or legal word and has no valid LEGAL: line; else None. A fork is not
    checked. `LEGAL: none - <reason>` passes and, when `notes` is a list, appends one audit text to it (both fields cleaned of
    control and format characters): the caller writes it. Raises on a broken catalog: the hook logs it and says so."""
    import budget
    import i18n
    if not isinstance(tool_input, dict) or budget.is_fork(tool_input.get("subagent_type")):
        return None
    prompt = tool_input.get("prompt")
    word = legal_word(prompt)
    if word is None:
        return None
    route = legal_route(prompt)
    if route is True:
        return None
    if route:
        if notes is not None:
            kind = _clean(tool_input.get("subagent_type") or "general-purpose", 40)
            notes.append("legal-none: %s (%s: %s)" % (kind, word, route))
        return None
    return i18n.t("agentgate.legal", word=word)


def deny_reason(tool_input, notes=None):
    """None when the spawn may go ahead, else the deny text. A payload that is not a dict is denied (never fail open). An error
    in the legal check is raised as LegalCheckError."""
    if not isinstance(tool_input, dict):
        kind = "unknown"
    else:
        kind = tool_input.get("subagent_type") or "general-purpose"   # no type means general-purpose
        if is_budgit_agent(kind) or has_reason(tool_input.get("prompt")):
            try:
                return legal_reason(tool_input, notes)
            except Exception as e:
                raise LegalCheckError(str(e)) from e
    return ("budgit token guard: %s is an all-tools agent (65-79k tokens of fixed context per spawn). Use a Budgit agent "
            "instead (%s). If an all-tools type is really needed, add a line "
            "`ALL-TOOLS: <reason>` to the prompt; its reason must name the tool no Budgit agent has."
            % (str(kind)[:60], _instead_for(tool_input)))


_AGENT_FOR_TASK = {"build-code": "studio:builder", "fix-bug": "studio:builder", "verify": "studio:verifier",
                   "research": "studio:researcher", "plan-review": "studio:plan-critic",
                   "security-review": "studio:security-reviewer"}


def _instead_for(tool_input):
    """R-1360: the Budgit agent for the brief's `task:` type (a synonym counts); with no task line, or a type with no agent
    here, the whole list. Text only: the decision never reads this."""
    prompt = str(tool_input.get("prompt") or "") if isinstance(tool_input, dict) else ""
    try:
        import agentcap
        m = agentcap._TASK_LINE.search(prompt)
        if m:
            for t in agentcap._split_names(m.group(1)):
                t = t.lower()
                t = t if t in _AGENT_FOR_TASK else agentcap.task_synonym(t, m.group(1))
                if t in _AGENT_FOR_TASK:
                    return "%s for task: %s" % (_AGENT_FOR_TASK[t], t)
    except Exception:
        pass
    return INSTEAD
