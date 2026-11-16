"""legalcheck — the mechanical half of the legal-eu-es-fr skill (R-272). Stdlib only.

  lint                 rule tables in ../rules/*.md: fields, ids, dates, tags, official hosts; figure-mismatch between rows
                       and between a row and a role or SKILL.md line that cites it
  stale                rules that must be re-checked before use (tag S/U, or past review_by)
  answer FILE          a drafted answer: disclaimer, verdict, every URL tagged; GREEN only on fresh, verified sources
                       (a rule whose source has a pending change is not fresh)
  diff [--offline]     weekly check: fetch each rule's official URL and the authority feeds, compare with the
                       snapshot, write a report. It never edits a rule: a change is a proposal for the user's yes.
  accept ID|URL ...    after the legal checker's review, move the snapshot baseline of those sources to the version last seen
  due                  prints "due" when the last diff is stale_days old or more (reference/settings.json)
  rows                 the rule rows that apply to the profile's person type, the restricted ones to ask about, the excluded ones
  filing TABLE --population FILE --verify FILE
                       filing gate: every box checked against its rule, official source, receipt, quote and recomputed figure;
                       the checklist is generated from the table and the full population; the last line is the only verdict

State (snapshot, reports) lives in $LEGAL_HOME, default ~/.budgit/legal (or ~/.studio/legal), never in the repo.
Fetches go only to the official hosts listed in ../reference/settings.json (with the freshness window and the panel cap), over https, read-only.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import namedtuple
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
RULES_DIR = os.path.join(SKILL, "rules")
FEEDS_FILE = os.path.join(SKILL, "reference", "feeds.md")

SETTINGS_FILE = os.path.join(SKILL, "reference", "settings.json")


def load_settings(path=SETTINGS_FILE):
    """R-1125 #9: the settings that drift (hosts, freshness window, panel cap, person types) are data. Fails closed: a missing,
    unreadable or empty settings file stops the script, because an empty host list would otherwise mean "nothing is official"
    in some places and "no check" in others."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        hosts = tuple(str(h).strip().lower() for h in raw["official_hosts"])
        manual = {str(k).strip().lower(): str(v) for k, v in raw["manual_hosts"].items()}
        days, cap = raw["stale_days"], raw["panel_cap"]
        types = tuple(str(t) for t in raw["person_types"])
        regimes = {str(k): [str(x) for x in v] for k, v in (raw.get("regimes") or {}).items()}
        escalation = _load_escalation(raw["escalation"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SystemExit("legalcheck: settings file %s is missing or malformed (%s)" % (path, e))
    if not hosts or not all(hosts) or not types or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 1
                                                            for n in (days, cap)):
        raise SystemExit("legalcheck: settings file %s needs hosts, person types, stale_days >= 1 and panel_cap >= 1" % path)
    return {"official_hosts": hosts, "manual_hosts": manual, "stale_days": days, "panel_cap": cap,
            "person_types": types, "regimes": regimes, "escalation": escalation}


ESCALATION_LANGS = ("es", "fr", "en")


def _fold(text):
    """Lower case, no accents, one apostrophe, single spaces: how a phrase and a draft are compared."""
    s = unicodedata.normalize("NFKD", (text or "").replace("’", "'"))
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).lower().split())


def _lower(text):
    """Like _fold but keeps the accents: for the few phrases where an accent is the word (firmé is a signing, firme is an adjective)."""
    return " ".join(unicodedata.normalize("NFC", (text or "").replace("’", "'")).lower().split())


def _load_escalation(esc):
    """R-1125 #11: SKILL.md section 2 as data. {"phrases": {kind: [folded phrase]}, "rows": {rule id: kind}}. Fails closed: a trigger
    with no phrase in a language, or a row naming an unknown trigger, is an error, never a silently smaller check."""
    triggers = esc["triggers"]
    if not isinstance(triggers, dict) or not triggers:
        raise ValueError("escalation needs triggers")
    phrases = {}
    for kind, entry in triggers.items():
        phrases[str(kind)] = []
        for lang in ESCALATION_LANGS:
            found = [("!" + _lower(str(p)[1:])) if str(p).startswith("!") else _fold(str(p)) for p in entry[lang]]
            if not found or not all(found) or "!" in found:
                raise ValueError("escalation trigger %s needs phrases in %s" % (kind, lang))
            phrases[str(kind)] += found
    rows = {str(k): str(v) for k, v in esc["rows"].items()}
    unknown = sorted(set(rows.values()) - set(phrases))
    if unknown:
        raise ValueError("escalation rows name unknown triggers %s" % ", ".join(unknown))
    return {"phrases": phrases, "rows": rows}


_SETTINGS = load_settings()
OFFICIAL_HOSTS = _SETTINGS["official_hosts"]
MANUAL_HOSTS = _SETTINGS["manual_hosts"]
STALE_DAYS = _SETTINGS["stale_days"]
PANEL_CAP = _SETTINGS["panel_cap"]   # nothing in this script reads it: tests check SKILL.md and registry-entry.json against it
PERSON_TYPES = _SETTINGS["person_types"]
ESCALATION_ROWS = _SETTINGS["escalation"]["rows"]


def _phrase_rx(phrase):
    """R-1125 #11 round 2: a trigger is caught in any common word form. Each word of a folded phrase matches itself plus an optional
    plural ending (s, es, x); a word that ends in * is a stem and matches any ending (firm* is firmo, firmé, firmamos...).
    Words stand alone at the edges, so a different word that merely shares a prefix (Crimea, firmware) does not hit."""
    exact = phrase.startswith("!")           # a leading ! keeps the accents (matched on the lower-cased draft, not the folded one)
    parts = []
    for word in phrase.lstrip("!").split(" "):
        if word.endswith("*"):
            parts.append(re.escape(word[:-1]) + r"\w*")
        else:
            parts.append(re.escape(word) + r"(?:s|es|x)?")
    return exact, re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)")


ESCALATION_PHRASES = {k: [_phrase_rx(p) for p in v] for k, v in _SETTINGS["escalation"]["phrases"].items()}
FIELDS = ("id", "rule", "instrument", "url", "status", "checked", "review_by", "tag")
TAGS = ("V", "S", "U")
ID_RE = re.compile(r"^(EU|ES|FR|LIC|XB)-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Rule = namedtuple("Rule", "id rule instrument url status checked review_by tag raw file line applies_to", defaults=((),))
Problem = namedtuple("Problem", "code line msg")


# ---------- tables ----------

def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(text):
    """Rows of every markdown table in text as (line_no, {header: cell})."""
    rows, header = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append((n, dict(zip(header, cells))))
    return rows


def _date(s):
    if not DATE_RE.match(s or ""):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def parse_rules(text, file="<text>"):
    out = []
    for n, row in _table(text):
        if "id" not in row or "review_by" not in row:
            continue
        get = lambda k: row.get(k, "").strip()  # noqa: E731
        out.append(Rule(get("id"), get("rule"), get("instrument"), get("url"), get("status"),
                        _date(get("checked")), _date(get("review_by")), get("tag"), row, file, n,
                        _types(get("applies_to"))))
    return out


def _types(text):
    """A comma, semicolon or space separated list of person types, lower-cased; empty text is an empty tuple."""
    return tuple(t for t in re.split(r"[,;\s]+", (text or "").strip().lower()) if t)


# ---------- person type (R-1125 #9) ----------
# A rule row may say which person types it applies to (optional column applies_to; empty means everyone). An answer uses only
# the rows that apply to the asker's profile, and asks when the profile has no person type.

def row_status(rule, person_types):
    """applies | excluded | ask. person_types is a set of known types, or None when the profile does not say."""
    if not rule.applies_to:
        return "applies"
    if person_types is None:
        return "ask"
    return "applies" if person_types & set(rule.applies_to) else "excluded"


def rows_for(rules, person_types):
    """(rows to use, restricted rows to ask about, rows that do not apply). The one filter every view calls."""
    use, ask, out = [], [], []
    for r in rules:
        {"applies": use, "ask": ask, "excluded": out}[row_status(r, person_types)].append(r)
    return use, ask, out


PROFILE_KEY_RE = {k: re.compile(r"^[\s>*-]*%s\s*:[ \t]*(.*?)[ \t]*$" % k, re.I | re.M) for k in ("person_type", "regime")}


def parse_profile(text):
    """{"person_types": set or None, "regime": free text or ""} from the profile's `person_type:` and `regime:` lines.
    Empty, unconfirmed ("(por confirmar)") or unknown values mean unknown (None): the safe direction is to ask."""
    found = {k: (rx.search(text or "").group(1) if rx.search(text or "") else "") for k, rx in PROFILE_KEY_RE.items()}
    types = set(_types(found["person_type"])) if "(" not in found["person_type"] else set()
    return {"person_types": types if types and types <= set(PERSON_TYPES) else None, "regime": found["regime"]}


def read_profile(home):
    """Read-only. A missing or unreadable profile is an unknown person type, never a crash."""
    try:
        with open(os.path.join(home, "legal-profile.md"), encoding="utf-8") as f:
            return parse_profile(f.read())
    except (OSError, ValueError):
        return parse_profile("")


def load_rules(rules_dir=RULES_DIR):
    out = []
    for name in sorted(os.listdir(rules_dir)):
        if name.endswith(".md"):
            path = os.path.join(rules_dir, name)
            with open(path, encoding="utf-8") as f:
                out += parse_rules(f.read(), name)
    return out


def validate_escalation_rows(rules, rows):
    """Fails closed: an escalation row whose id is in no rules table would silently protect nothing."""
    missing = sorted(set(rows) - {r.id for r in rules})
    if missing:
        raise SystemExit("legalcheck: escalation rows in settings name no rule row: %s" % ", ".join(missing))


validate_escalation_rows(load_rules(), ESCALATION_ROWS)


def load_feeds(path=FEEDS_FILE):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [row["url"] for _n, row in _table(f.read()) if row.get("url", "").startswith("https://")]


def official(url):
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS)


def lint_rules(rules):
    probs, seen = [], set()
    for r in rules:
        where = "%s:%s %s" % (r.file, r.line, r.id or "?")
        if any(not r.raw.get(k, "").strip() for k in FIELDS):
            probs.append(Problem("missing-field", r.line, where + ": every column must be filled"))
        if r.id and not ID_RE.match(r.id):
            probs.append(Problem("bad-id", r.line, where + ": id must look like EU-GDPR-HOUSEHOLD"))
        if r.id in seen:
            probs.append(Problem("duplicate-id", r.line, where))
        seen.add(r.id)
        if (r.raw.get("checked") and r.checked is None) or (r.raw.get("review_by") and r.review_by is None):
            probs.append(Problem("bad-date", r.line, where + ": dates are YYYY-MM-DD"))
        if r.checked and r.review_by and r.review_by < r.checked:
            probs.append(Problem("review-before-checked", r.line, where))
        if r.tag and r.tag not in TAGS:
            probs.append(Problem("bad-tag", r.line, where + ": tag is V (official page seen), S (secondary) or U"))
        if r.tag == "V" and r.url and not official(r.url):
            probs.append(Problem("non-official-url", r.line, where + ": a V rule cites an official https host"))
        unknown = [t for t in r.applies_to if t not in PERSON_TYPES]
        if unknown:
            probs.append(Problem("bad-applies-to", r.line, where + ": applies_to has unknown person type(s) %s; known: %s"
                                 % (", ".join(unknown), ", ".join(PERSON_TYPES))))
    return probs


# ---------- figure consistency (R-1125 #8) ----------
# Two places never state different figures for the same rule: rows that share an id or a topic key must hold the same numbers, and
# a role or SKILL.md item that cites a row id next to a figure (a number with % or EUR) may only use numbers that row holds.
# Code figure-mismatch. The check reads text only: it does not know what a row means, only which numbers it holds.

CITED_ID_RE = re.compile(r"\b(?:EU|ES|FR|LIC|XB)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_NUM = r"\d{1,3}(?:[ \u00a0.]\d{3})+(?:[.,]\d+)?(?!\d)|\d+(?:[.,]\d+)?"
NUMBER_RE = re.compile(r"(?<![\w.,])(?:%s)" % _NUM)
FIGURE_RE = re.compile(r"(?<![\w.,])(%s)\s?(?:%%|€|EUR\b|euros?\b|por\s+(?:ciento|100)\b)" % _NUM)
BULLET_RE = re.compile(r"\s*(?:[-*]|\d+\.)\s")


def _num(text):
    t = text.replace("\u00a0", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")   # 5.101,20 and 5 101,20: dot thousands, comma decimal
    elif re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+", t):
        t = t.replace(".", "")   # 5.101 and 1.250.000: dot thousands
    try:
        return Decimal(t).normalize()
    except InvalidOperation:
        return None


def row_numbers(text):
    """Every number in a row's text, as a set of Decimals (1 000 and 1000 and 1000.0 are the same figure)."""
    return {n for n in (_num(m.group(0)) for m in NUMBER_RE.finditer(CITED_ID_RE.sub(" ", text))) if n is not None}


def text_figures(text):
    """Numbers written with a unit (% or EUR) in a role or SKILL.md text, row ids left out."""
    return [n for n in (_num(m.group(1)) for m in FIGURE_RE.finditer(CITED_ID_RE.sub(" ", text))) if n is not None]


def text_items(text):
    """(first line number, joined text) of each bullet or numbered item and each paragraph; a wrapped line joins its item."""
    items, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            cur = None
        elif cur is None or BULLET_RE.match(line):
            cur = [n, line.strip()]
            items.append(cur)
        else:
            cur[1] += " " + line.strip()
    return [(n, t) for n, t in items]


def lint_figures(rules, texts):
    """figure-mismatch problems. texts is {file name: text} of the role files and SKILL.md."""
    probs, by_id, by_topic = [], {}, {}
    for r in rules:
        by_id.setdefault(r.id, []).append(r)
        topic = r.raw.get("topic", "").strip().lower()
        if topic:
            by_topic.setdefault(topic, []).append(r)
    for label, groups in (("id", by_id), ("topic", by_topic)):
        for key, rs in groups.items():
            if len({frozenset(row_numbers(r.rule)) for r in rs}) > 1:
                probs.append(Problem("figure-mismatch", rs[1].line,
                                     "%s: rows with the same %s state different figures: %s" % (key, label, ", ".join(
                                         "%s:%s" % (r.file, r.line) for r in rs))))
    for name, text in texts.items():
        for n, item in text_items(text):
            ids = [i for i in dict.fromkeys(CITED_ID_RE.findall(item)) if i in by_id]
            if not ids:
                continue
            allowed = set().union(*(row_numbers(r.rule) for i in ids for r in by_id[i]))
            for fig in text_figures(item):
                if fig not in allowed:
                    probs.append(Problem("figure-mismatch", n, "%s:%d: figure %s next to %s is not in that row"
                                         % (name, n, fig.normalize(), ", ".join(ids))))
    return probs


def load_texts(skill=SKILL):
    """SKILL.md and every roles/*.md as {name: text}."""
    out = {}
    paths = [os.path.join(skill, "SKILL.md")]
    roles = os.path.join(skill, "roles")
    if os.path.isdir(roles):
        paths += [os.path.join(roles, n) for n in sorted(os.listdir(roles)) if n.endswith(".md")]
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                out[os.path.relpath(p, skill)] = f.read()
        except OSError:
            continue
    return out


UNKNOWN_SNAPSHOT ={"sources": {}, "unknown": True}   # a snapshot that cannot be read: "pending unknown", nothing is fresh


def pending(rule, snapshot):
    """True when the snapshot holds an unaccepted change for the rule's source (R-1125 #6): the rule may be out of date."""
    if (snapshot or {}).get("unknown"):
        return True
    src = ((snapshot or {}).get("sources") or {}).get(rule.url)
    return bool(src) and "pending" in src


def fresh(rule, today, snapshot=None):
    return (rule.tag == "V" and rule.checked is not None and rule.review_by is not None and today <= rule.review_by
            and not pending(rule, snapshot))


def stale(rules, today):
    out = []
    for r in rules:
        if r.tag != "V":
            out.append((r, "tag %s: confirm on the official source before use" % (r.tag or "?")))
        elif r.review_by is None or today > r.review_by:
            out.append((r, "review_by %s passed" % (r.review_by or "?")))
    return out


# ---------- answers ----------

DISCLAIMER_RE = re.compile(r"no (?:es )?asesoramiento jur[ií]dico|pas un conseil juridique|not legal (?:or tax )?advice", re.I)
VERDICT_RE = re.compile(r"^\W*(?:veredicto|verdict)\W*:\W*(verde|[áa]mbar|rojo|vert|orange|rouge|green|yellow|amber|red)\b",
                        re.I | re.M)
GREEN = ("verde", "vert", "green")
VERIFIED_RE = re.compile(r"\[(?:verificado hoy|v[ée]rifi[ée] aujourd'hui|verified today)\s+(\d{4}-\d{2}-\d{2})\]", re.I)
RULE_TAG_RE = re.compile(r"\[(?:ficha|fiche|rule)\s+([A-Z]{2,3}-[A-Z0-9-]+)(?:\s+\d{4}-\d{2}-\d{2})?\]")
UNVERIFIED_RE = re.compile(r"\[(?:sin verificar|non v[ée]rifi[ée]|unverified|de memoria[^\]]*|de m[ée]moire[^\]]*|from memory[^\]]*)\]",
                           re.I)
SPLIT_RE = re.compile(r"^\W*(?:dividido|divis[ée]|split)\b", re.I | re.M)
URL_RE = re.compile(r"https?://[^\s<>\]\)\"'`]+")


# R-1125 #5: a money answer hunts for what saves the user money. "Money" is the narrowest notion that works:
# the draft holds a euro amount or one word of a small tax list. The disclaimer line is cut first (it says "tax").
MONEY_RE = re.compile(
    r"\d\s?(?:€|eur\b|euros?\b)|€\s?\d"
    r"|\b(?:irpf|iva|vat|tva|impuestos?|imp[oô]ts?|tax(?:es)?|deduc\w*|d[ée]ducti\w*|cotisations?|urssaf|aeat|dgfip|hacienda)\b",
    re.I)
SAVINGS_RE = re.compile(r"^\W*(?:pase de ahorro|savings pass|passe d['’]\W?[ée]conomies)\b", re.I | re.M)


def is_money(text):
    return bool(MONEY_RE.search(DISCLAIMER_RE.sub(" ", text)))


SOFT_CODES = ("ask-person-type",)    # a note, not a failure: the draft only has to stay AMBER (a GREEN one fails on its own)


def hard_problems(probs):
    return [p for p in probs if p.code not in SOFT_CODES]


def escalation_hits(text, cited):
    """The escalation triggers (SKILL.md section 2, data in reference/settings.json) a draft hits: a trigger phrase anywhere in it
    (the Pregunta line included; the fixed disclaimer is cut first) or a cited rule row that is a trigger by nature."""
    body = DISCLAIMER_RE.sub(" ", text)
    folded, lowered = _fold(body), _lower(body)
    hits = [kind for kind, rxs in ESCALATION_PHRASES.items()
            if any(rx.search(lowered if exact else folded) for exact, rx in rxs)]
    for rid in cited:
        kind = ESCALATION_ROWS.get(rid)
        if kind and kind not in hits:
            hits.append(kind)
    return hits


def check_answer(text, rules, today, snapshot=None, person_types=None):
    """person_types: the asker's types from the profile (a set), or None when the profile does not say."""
    by_id = {r.id: r for r in rules}
    probs, weak, strong, cites, cited = [], 0, 0, 0, []
    if not DISCLAIMER_RE.search(text):
        probs.append(Problem("no-disclaimer", 0, "the fixed disclaimer is missing"))
    if is_money(text) and not SAVINGS_RE.search(text):
        probs.append(Problem("no-savings-pass", 0, "a money answer needs its 'Pase de ahorro' block (deductions, regime choice, deadlines, costly errors)"))
    verdicts = VERDICT_RE.findall(text)
    if not verdicts:
        probs.append(Problem("no-verdict", 0, "no 'Veredicto: VERDE|ÁMBAR|ROJO' line"))
    for n, line in enumerate(text.splitlines(), 1):
        urls = [u.rstrip(".,;:") for u in URL_RE.findall(line)]
        verified = VERIFIED_RE.findall(line)
        ruled = RULE_TAG_RE.findall(line)
        unver = UNVERIFIED_RE.findall(line)
        cites += len(verified) + len(ruled) + len(unver)
        if urls and not (verified or ruled or unver):
            probs.append(Problem("bare-url", n, "every source carries a tag: [verificado hoy DATE], [ficha ID] or [sin verificar]"))
        for day in verified:
            if not urls:
                probs.append(Problem("verified-without-url", n, "a verified citation names its URL"))
                weak += 1
            elif not all(official(u) for u in urls):
                probs.append(Problem("non-official-url", n, "verified today, but not on an official host"))
                weak += 1
            elif day != today.isoformat():
                weak += 1
            else:
                strong += 1
        for rid in ruled:
            r = by_id.get(rid)
            if r is None:
                probs.append(Problem("unknown-rule", n, rid))
                weak += 1
                continue
            cited.append(rid)
            if row_status(r, person_types) == "excluded":
                probs.append(Problem("row-not-applicable", n, "rule %s applies to %s, not to this profile (%s)"
                                     % (rid, ", ".join(r.applies_to), ", ".join(sorted(person_types)))))
                weak += 1
            elif row_status(r, person_types) == "ask":
                probs.append(Problem("ask-person-type", n, "AMBER: rule %s applies only to %s; ask the person type before relying on it"
                                     % (rid, ", ".join(r.applies_to))))
                weak += 1
            elif fresh(r, today, snapshot):
                strong += 1
            else:
                weak += 1
        weak += len(unver)
    if cites == 0:
        probs.append(Problem("no-citation", 0, "no tagged source"))
    if verdicts and any(v.lower() in GREEN for v in verdicts):
        if weak:
            probs.append(Problem("green-unverified", 0, "GREEN rests on an unverified, stale or other-day source"))
        if SPLIT_RE.search(text):
            probs.append(Problem("green-split", 0, "the specialists split: the verdict is at least ÁMBAR"))
        if strong == 0 and not weak:
            probs.append(Problem("green-without-source", 0, "GREEN needs a source verified today or a fresh rule"))
        hits = escalation_hits(text, cited)
        if hits:
            probs.append(Problem("escalation-trigger", 0, "GREEN hits an escalation trigger (SKILL.md section 2: %s): the verdict is "
                                 "at least ÁMBAR and the answer says whom to ask" % ", ".join(hits)))
    return probs


# ---------- weekly diff ----------

DATE_MARKERS = (
    re.compile(r"[ÚU]ltima actualizaci[óo]n publicada el (\d{2}/\d{2}/\d{4})"),
    re.compile(r"Version en vigueur (?:depuis le|au|du) (\d{1,2}(?:er)? [a-zéû]+ \d{4})", re.I),
)
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
_BOE_ID = re.compile(r"boe\.es/buscar/act\.php\?id=(BOE-[A-Z]-\d{4}-\d+)")
_CELEX = re.compile(r"CELEX(?::|%3A)([0-9A-Z()]+)", re.I)
_ELI = re.compile(r"eur-lex\.europa\.eu/eli/(reg|dir|dec)/(\d{4})/(\d+)")
_ELI_LETTER = {"reg": "R", "dir": "L", "dec": "D"}


def _celex(url):
    m = _CELEX.search(url)
    if m:
        return m.group(1).upper()
    m = _ELI.search(url)
    if m:
        return "3%s%s%04d" % (m.group(2), _ELI_LETTER[m.group(1)], int(m.group(3)))
    return None


def _sparql_url(query):
    return SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})


def probe(url):
    """(url to fetch, headers, kind) for a rule's citation URL. EUR-Lex and the BOE pages answer scripts badly
    (bot wall, HTTP 400 without Accept), so their official open-data endpoints are asked instead."""
    m = _BOE_ID.search(url)
    if m:
        return ("https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/%s/metadatos" % m.group(1),
                {"Accept": "application/json"}, "boe")
    celex = _celex(url) if "eur-lex.europa.eu" in url else None
    if celex:
        # Anchored on the act's own CELEX literal (indexed, under a second); a FILTER scan over all CELEX
        # numbers took 40-50 s live and timed out.
        head = "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> " \
               "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> SELECT DISTINCT ?celex WHERE { " \
               '?w cdm:resource_legal_id_celex "%s"^^xsd:string . ' % celex
        if celex[0] == "3":   # legislation: list its consolidated versions; a new one means the act was amended
            q = head + "?c cdm:act_consolidated_based_on_resource_legal ?w . " \
                       "?c cdm:resource_legal_id_celex ?celex } LIMIT 500"
            return _sparql_url(q), {"Accept": "application/sparql-results+json"}, "eu-consolidated"
        q = head + "?w cdm:resource_legal_id_celex ?celex } LIMIT 5"
        return _sparql_url(q), {"Accept": "application/sparql-results+json"}, "eu-exists"
    return url, {}, "page"


def marker_for(kind, url, body):
    if kind == "boe":
        m = re.search(r"\"fecha_actualizacion\"\s*:\s*\"([0-9TZ]+)\"", body)
        if not m:
            raise ValueError("BOE metadata without fecha_actualizacion")
        return "date:" + m.group(1)
    if kind in ("eu-consolidated", "eu-exists"):
        values = sorted(b["celex"]["value"] for b in json.loads(body)["results"]["bindings"])
        if kind == "eu-exists":
            return "exists" if values else "missing"
        return "consolidated:" + (values[-1] if values else "none")
    return marker(url, body)
_DROP_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def marker(url, body):
    for rx in DATE_MARKERS:
        m = rx.search(body)
        if m:
            return "date:" + m.group(1)
    text = " ".join(_TAG_RE.sub(" ", _DROP_RE.sub(" ", body)).split())
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def empty_snapshot():
    return {"version": 1, "last_run": None, "sources": {}, "feeds": {}}


_ITEM_RE = re.compile(r"<(item|entry)\b.*?</\1>", re.I | re.S)


def _field(block, name):
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (name, name), block, re.I | re.S)
    if m:
        return re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)).strip()
    m = re.search(r"<%s\b[^>]*href=\"([^\"]+)\"" % name, block, re.I)
    return m.group(1) if m else ""


def feed_items(body):
    out = []
    for m in _ITEM_RE.finditer(body):
        block = m.group(0)
        title, link = _field(block, "title"), _field(block, "link")
        key = _field(block, "guid") or _field(block, "id") or link or title
        out.append({"key": key, "title": title, "link": link})
    return out


def diff(rules, feeds, snapshot, fetch, today, offline=False):
    rep = {"date": today.isoformat(), "offline": offline, "sources": [], "feed_items": [], "feed_errors": [],
           "stale": [{"id": r.id, "why": why, "file": r.file} for r, why in stale(rules, today)]}
    if offline:
        return rep
    urls = {}
    for r in rules:
        if r.url:
            urls.setdefault(r.url, []).append(r.id)
    for url, ids in urls.items():
        entry = {"url": url, "rules": ids}
        if not official(url):
            entry["status"] = "skipped (not an official host)"
            rep["sources"].append(entry)
            continue
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        manual = [why for h, why in MANUAL_HOSTS.items() if host == h or host.endswith("." + h)]
        if manual:
            entry["status"] = manual[0]
            rep["sources"].append(entry)
            continue
        target, headers, kind = probe(url)
        try:
            m = marker_for(kind, url, fetch(target, headers))
        except Exception as exc:  # network, HTTP or decode error: report, never fatal
            entry.update(status="unreachable", error=str(exc)[:120])
            rep["sources"].append(entry)
            continue
        prev = snapshot["sources"].get(url)
        if prev is None:
            snapshot["sources"][url] = {"marker": m, "seen": today.isoformat()}
            entry["status"] = "new"
        elif prev["marker"] == m:
            prev["seen"] = today.isoformat()
            prev.pop("pending", None)
            entry["status"] = "same"
        else:
            prev["pending"] = m          # the baseline moves only on accept(), after the legal checker's review
            entry.update(status="changed", was=prev["marker"], now=m)
        rep["sources"].append(entry)
    for url in feeds:
        try:
            items = feed_items(fetch(url, {}))
        except Exception:
            rep["feed_errors"].append(url)
            continue
        seen = snapshot["feeds"].setdefault(url, [])
        for it in items:
            if it["key"] not in seen:
                seen.append(it["key"])
                rep["feed_items"].append(dict(it, feed=url))
        del seen[:-500]
    snapshot["last_run"] = today.isoformat()
    return rep


def accept(snapshot, refs, rules):
    urls = set()
    for ref in refs:
        if ref.startswith("https://"):
            urls.add(ref)
        urls.update(r.url for r in rules if r.id == ref)
    done = []
    for url in sorted(urls):
        src = snapshot["sources"].get(url)
        if src and "pending" in src:
            src["marker"] = src.pop("pending")
            done.append(url)
    return done


def due(snapshot, today):
    last = _date(snapshot.get("last_run") or "")
    return last is None or (today - last).days >= STALE_DAYS


def render_report(rep):
    lines = ["# Legal sources check, %s%s" % (rep["date"], " (offline: nothing fetched)" if rep["offline"] else ""), "",
             "No rule was edited. Each line below is a proposal: re-read the source, then change the rule only after the legal checker's review,",
             "and run `legalcheck.py accept <id>` so the baseline moves.", ""]
    changed = [e for e in rep["sources"] if e["status"] == "changed"]
    other = [e for e in rep["sources"] if e["status"] == "unreachable" or e["status"].startswith(("skipped", "manual"))]
    lines.append("## Sources that changed (%d)" % len(changed))
    lines += ["- %s: %s (%s -> %s)" % (", ".join(e["rules"]), e["url"], e["was"], e["now"]) for e in changed] or ["- none"]
    lines += ["", "## Rules to re-check before use (%d)" % len(rep["stale"])]
    lines += ["- %s (%s): %s" % (s["id"], s["file"], s["why"]) for s in rep["stale"]] or ["- none"]
    lines += ["", "## New items from authority feeds (%d)" % len(rep["feed_items"])]
    lines += ["- %s: %s" % (i["title"] or i["key"], i["link"]) for i in rep["feed_items"]] or ["- none"]
    if other or rep["feed_errors"]:
        lines += ["", "## Not reached"]
        lines += ["- %s (%s): %s" % (e["url"], ", ".join(e["rules"]), e["status"]) for e in other]
        lines += ["- feed %s" % u for u in rep["feed_errors"]]
    new = sum(1 for e in rep["sources"] if e["status"] == "new")
    if new:
        lines += ["", "%d source(s) seen for the first time: baseline recorded." % new]
    return "\n".join(lines) + "\n"


# ---------- io ----------

def legal_home():
    explicit = os.environ.get("LEGAL_HOME")
    if explicit:
        explicit = os.path.expanduser(explicit)
        if not os.path.isabs(explicit):
            raise SystemExit("LEGAL_HOME must be an absolute path: %s" % explicit)    # a relative one could land in a repo
        return explicit
    # R-1106: Budgit's real home, decided as studiolib.home_state() / resolve_data_dir() do (copied, because `python3 -I` cannot import
    # studiolib; a parity test in tests/test_r1106_legal_profile_save.py fails on any drift).
    explicit = os.environ.get("BUDGIT_HOME")
    if explicit is None:
        explicit = os.environ.get("STUDIO_HOME")
    if explicit:
        return os.path.join(os.path.expanduser(explicit), "legal")
    parent = os.path.expanduser("~")
    new, old = os.path.join(parent, ".budgit"), os.path.join(parent, ".studio")

    def real(p):
        return os.path.isdir(p) or os.path.islink(p)
    marker = os.path.join(new, "MIGRATED")
    if real(new) and (os.path.isfile(marker) or os.path.isfile(os.path.join(new, "ledger.json"))):
        if real(old) and not os.path.isfile(marker) and os.path.isfile(os.path.join(old, "ledger.json")):
            return os.path.join(old, "legal")
        return os.path.join(new, "legal")
    return os.path.join(old if real(old) else new, "legal")


def load_snapshot(home):
    path = os.path.join(home, "snapshot.json")
    if not os.path.exists(path):
        return empty_snapshot()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def snapshot_for_answer(home):
    """Read-only. A corrupt or unreadable snapshot.json is "pending unknown", never a crash and never "nothing pending"."""
    try:
        snap = load_snapshot(home)
        if not isinstance(snap, dict) or not isinstance(snap.get("sources"), dict):
            return UNKNOWN_SNAPSHOT
        return snap
    except (OSError, ValueError):
        return UNKNOWN_SNAPSHOT


def save_snapshot(home, snap):
    os.makedirs(home, exist_ok=True)
    tmp = os.path.join(home, "snapshot.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, os.path.join(home, "snapshot.json"))


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only while it stays on an official host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official(newurl):
            raise ValueError("redirect to a non-official host: %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_fetch(url, headers=None):
    if not official(url):
        raise ValueError("not an official host")
    h = {"User-Agent": "budgit-legalcheck/0.1 (read-only)",
         "Accept": "text/html,application/xml,application/json;q=0.9,*/*;q=0.5"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.build_opener(_OfficialRedirects).open(req, timeout=40) as resp:
        return resp.read(3_000_000).decode("utf-8", "replace")


# ---------- filing gate ----------
# "Ready to file" is said by this script, never typed by an agent. The checklist is generated from the table rows and the
# full population, not written by whoever did the work. Verify-file format shared with the Stop-time check in the plugin.

FILING_COLUMNS = ("box", "amount", "rule_id", "url", "receipt", "quote", "recomputed")
VERIFY_NM_RE = re.compile(r"^\s*(\d+)\s+(?:of|de)\s+(\d+)\b", re.I)
VERIFY_POP_RE = re.compile(r"^population:\s*(\S.*?)\s+rows=(\d+)\s*$", re.I)
VERIFY_REVIEWER_RE = re.compile(r"^reviewer:\s*(\S.*?)\s*$", re.I)


def parse_amount(text):
    """Decimal from «1.234,56», «1,234.56», «12,5», «12.50»; None when it is not a finite number."""
    from decimal import Decimal, InvalidOperation
    s = re.sub(r"\s", "", text or "")   # every kind of space, no-break ones included
    if not s:
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def read_table(path):
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
        missing = [c for c in FILING_COLUMNS if c not in names]
        if missing:
            raise ValueError("table lacks column(s): %s" % ", ".join(missing))
        rows = []
        for raw in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
            if any(row.values()):
                rows.append(row)
    return rows


def population_rows(path):
    """Rows of the full source export: .csv non-empty lines minus the header, .jsonl non-empty lines."""
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return lines[1:]
    if ext == ".jsonl":
        return lines
    raise ValueError("population must be .csv or .jsonl: %s" % path)


def check_row(row, rules, today, snapshot):
    """Problems of one table row; empty means the row passes."""
    why = []
    by_id = {r.id: r for r in rules}
    rule = by_id.get(row.get("rule_id", ""))
    if rule is None:
        why.append("rule %s is not in the rule tables" % (row.get("rule_id") or "?"))
    elif not fresh(rule, today, snapshot):
        why.append("rule %s is not fresh (tag %s, review_by %s, pending change: %s)"
                   % (rule.id, rule.tag or "?", rule.review_by or "?", "yes" if pending(rule, snapshot) else "no"))
    if not official(row.get("url", "")):
        why.append("url is not an official https source")
    if not row.get("receipt"):
        why.append("no receipt")
    if not row.get("quote"):
        why.append("no quote")
    amount, recomputed = parse_amount(row.get("amount")), parse_amount(row.get("recomputed"))
    if amount is None or recomputed is None:
        why.append("amount or recomputed figure is not a number")
    elif amount != recomputed:
        why.append("recomputed %s differs from amount %s" % (row.get("recomputed"), row.get("amount")))
    return why


def build_checklist(table, population):
    """One line per table row (box, rule, what is checked) plus one per population row. Generated, never written by hand."""
    lines = []
    for row in table:
        lines.append("[ ] box %s | rule %s | amount %s: rule fresh, official url, receipt, quote, recomputed figure equals amount"
                     % (row.get("box") or "?", row.get("rule_id") or "?", row.get("amount") or "?"))
    for i, text in enumerate(population, 1):
        # No row content here: the checklist file sits in the verify folder, and the row stays only in the source export.
        lines.append("[ ] population row %d | sha256:%s: seen and matched to a box or set aside with a reason"
                     % (i, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]))
    return lines


def read_findings(verify_path):
    """Problems from <verify name>.findings.jsonl next to the verify file. A missing file is zero findings."""
    path = os.path.splitext(verify_path)[0] + ".findings.jsonl"
    if not os.path.exists(path):
        return []
    probs = []
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().splitlines()
    except (OSError, ValueError):
        return ["findings file cannot be read: %s" % path]
    for n, line in enumerate(raw, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            probs.append("findings line %d does not parse" % n)
            continue
        if not isinstance(row, dict) or row.get("status") not in ("open", "closed") or not row.get("id"):
            probs.append("findings line %d is not a finding row (id, item, text, status, evidence)" % n)
        elif row["status"] == "open":
            probs.append("finding %s is open" % row["id"])
        elif not str(row.get("evidence") or "").strip():
            probs.append("finding %s is closed with no evidence" % row["id"])
    return probs


def read_verify(verify_path, population_path, pop_count, today):
    """(problems, N from the verify file or None, M from the verify file or None)."""
    probs = []
    try:
        with open(verify_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        mtime = dt.date.fromtimestamp(os.path.getmtime(verify_path))
    except (OSError, ValueError):
        return ["verify file cannot be read: %s" % verify_path], None, None
    if mtime != today:
        probs.append("verify file is from %s, not today" % mtime.isoformat())
    if not lines or lines[0].strip() != "PASS":
        probs.append("verify file line 1 is not PASS")
    if not any(VERIFY_REVIEWER_RE.match(ln.strip()) for ln in lines):
        probs.append("verify file has no reviewer line")
    nm = next((m for m in (VERIFY_NM_RE.match(ln) for ln in lines) if m), None)
    n = m = None
    if nm is None:
        probs.append("verify file has no N of M line")
    else:
        n, m = int(nm.group(1)), int(nm.group(2))
        if n != m:
            probs.append("verify file says %d of %d: not all checked" % (n, m))
    pop = next((x for x in (VERIFY_POP_RE.match(ln.strip()) for ln in lines) if x), None)
    if pop is None:
        probs.append("verify file has no population line")
    else:
        named = pop.group(1)
        cands = {os.path.realpath(named), os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(verify_path)), named))}
        if os.path.realpath(population_path) not in cands:
            probs.append("verify file names another population: %s" % named)
        if int(pop.group(2)) != pop_count:
            probs.append("verify file says population rows=%s, the file has %d" % (pop.group(2), pop_count))
    probs += read_findings(verify_path)
    return probs, n, m


def check_filing(table_path, population_path, verify_path, rules, today, snapshot):
    """Returns (checklist, problems, n, m). The verdict is n == m and no problems; n is counted here, not typed by anyone."""
    probs, table, population = [], [], []
    try:
        table = read_table(table_path)
    except (OSError, ValueError) as e:
        probs.append("table cannot be read: %s" % e)
    try:
        population = population_rows(population_path)
    except (OSError, ValueError) as e:
        probs.append("population cannot be read: %s" % e)
    if not table and not probs:
        probs.append("the table has no rows")
    if not population and not any(p.startswith("population") for p in probs):
        probs.append("the population has no rows")
    ok_rows = 0
    for row in table:
        why = check_row(row, rules, today, snapshot)
        if why:
            probs.append("box %s: %s" % (row.get("box") or "?", "; ".join(why)))
        else:
            ok_rows += 1
    m = len(table) + len(population)
    vprobs, vn, vm = read_verify(verify_path, population_path, len(population), today)
    probs += vprobs
    if vm is not None and vm != m:
        probs.append("verify file says M=%d, the table and population hold %d" % (vm, m))
    # Population rows count only as far as a structurally sound verify file credits them.
    credited = max(0, min(len(population), vn - len(table))) if (not vprobs and vn is not None) else 0
    n = ok_rows + credited
    return build_checklist(table, population), probs, n, m


def _print_problems(probs):
    for p in probs:
        print("%s%s: %s" % (p.code, " (line %d)" % p.line if p.line else "", p.msg))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="legalcheck")
    ap.add_argument("--rules", default=RULES_DIR)
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint")
    sub.add_parser("stale")
    a = sub.add_parser("answer")
    a.add_argument("file")
    d = sub.add_parser("diff")
    d.add_argument("--offline", action="store_true")
    c = sub.add_parser("accept")
    c.add_argument("refs", nargs="+")
    sub.add_parser("due")
    sub.add_parser("home")
    sub.add_parser("rows")
    f = sub.add_parser("filing")
    f.add_argument("table")
    f.add_argument("--population", required=True)
    f.add_argument("--verify", required=True)
    # --today is accepted after the subcommand too
    for p in (a, d, c, f):
        p.add_argument("--today", type=dt.date.fromisoformat, dest="today_sub")
    args = ap.parse_args(argv)
    today = getattr(args, "today_sub", None) or args.today
    rules = load_rules(args.rules)

    if args.cmd == "lint":
        probs = lint_rules(rules) + lint_figures(rules, load_texts())
        _print_problems(probs)
        print("%d rules, %d problem(s)" % (len(rules), len(probs)))
        return 1 if probs else 0
    if args.cmd == "stale":
        for r, why in stale(rules, today):
            print("%s (%s): %s" % (r.id, r.file, why))
        return 0
    if args.cmd == "answer":
        home = legal_home()
        with open(args.file, encoding="utf-8") as f:     # read-only: pending sources are not fresh, the profile sets the person type
            probs = check_answer(f.read(), rules, today, snapshot_for_answer(home), read_profile(home)["person_types"])
        _print_problems(probs)
        hard = hard_problems(probs)
        notes = len(probs) - len(hard)
        print(("ok" if not hard else "%d problem(s)" % len(hard)) + (" (%d note(s))" % notes if notes else ""))
        return 1 if hard else 0
    if args.cmd == "rows":
        prof = read_profile(legal_home())
        use, ask, out = rows_for(rules, prof["person_types"])
        print("person type: %s" % (", ".join(sorted(prof["person_types"])) if prof["person_types"] else "unknown: ask the person type"))
        for label, group in (("ask", ask), ("excluded", out)):
            for r in group:
                print("%s %s (applies to %s)" % (label, r.id, ", ".join(r.applies_to)))
        print("%d rule(s) apply, %d to ask about, %d excluded" % (len(use), len(ask), len(out)))
        return 0
    if args.cmd == "home":
        print(legal_home())
        return 0
    if args.cmd == "filing":
        lines, probs, n, m = check_filing(args.table, args.population, args.verify, rules, today,
                                          snapshot_for_answer(legal_home()))
        # The full checklist (one line per table row and per population row) goes to a file; stdout keeps the table rows only.
        list_path = os.path.splitext(args.verify)[0] + ".checklist.txt"
        try:
            with open(list_path, "w", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")
        except OSError as e:
            probs.append("checklist file cannot be written: %s" % e)
        box_lines = [ln for ln in lines if ln.startswith("[ ] box ")]
        print("Checklist (generated: %d line(s), full list in %s):" % (len(lines), list_path))
        print("\n".join(box_lines))
        print("población: %d filas en %s" % (len(lines) - len(box_lines), args.population))
        _print_problems([Problem("blocked", 0, p) for p in probs])
        ready = not probs and n == m and m > 0
        print("Listo para presentar: %s (%d de %d)" % ("sí" if ready else "no", n, m))
        return 0 if ready else 1
    home = legal_home()
    snap = load_snapshot(home)
    if args.cmd == "diff":
        rep = diff(rules, load_feeds(), snap, http_fetch, today, offline=args.offline)
        save_snapshot(home, snap)
        text = render_report(rep)
        os.makedirs(os.path.join(home, "reports"), exist_ok=True)
        path = os.path.join(home, "reports", "%s%s.md" % (today.isoformat(), "-offline" if args.offline else ""))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stdout.write(text)
        print("report: %s" % path)
        return 0
    if args.cmd == "accept":
        done = accept(snap, args.refs, rules)
        save_snapshot(home, snap)
        print("\n".join(done) if done else "nothing pending for %s" % " ".join(args.refs))
        return 0
    if args.cmd == "due":
        print("due" if due(snap, today) else "not due (last run %s)" % snap.get("last_run"))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
