"""budgit humanize lint: the mechanical half of the humanize skill (R-267). Deterministic, read-only, stdlib only.

Layer 1 is the content gate (rules H4, H5, H6, H8, H9, H11 of skills/budgit-kickoff/humanize): it flags text that tends to read as
generic. Layer 2 is word hygiene (W1-W6), adapted from stop-slop (MIT, Hardik Pandya) minus the rules that clash with layer 1
or with UI copy. A hit is a question for a person, not a verdict. The Spanish lists are UNREVIEWED by the product owner.
"""
import argparse
import re
import sys
from collections import namedtuple

Finding = namedtuple("Finding", "rule layer line col match message")

# --- layer 1 word lists (H5 vague methods, H6 empty background, H8 unbounded claims) -------------------------------------
H5 = {
    "en": r"best practices|strategies|methodolog(?:y|ies)|robust|comprehensive|holistic|cutting-edge|state-of-the-art"
          r"|leverag(?:e|es|ed|ing)",
    "es": r"mejores pr[áa]cticas|estrategias|metodolog[íi]as?|robust[oa]s?|integral(?:es)?|hol[íi]stic[oa]s?|vanguardia"
          r"|aprovech(?:a|ar|ando)",
}
H6 = {
    "en": r"over the years|throughout history|has evolved|in recent years|in today's|since time immemorial",
    "es": r"a lo largo de los a[ñn]os|en los [úu]ltimos a[ñn]os|en la actualidad|en el mundo actual"
          r"|desde tiempos inmemoriales",
}
H8 = {
    "en": r"always|never|guarantee[sd]?|forever|permanent(?:ly)?|completely|eliminat(?:e|es|ing)|perfectly"
          r"|seamless(?:ly)?|effortless(?:ly)?",
    "es": r"(?<!de )siempre|nunca|garantiza(?:n|do)?|para siempre|permanente(?:mente)?|por completo|elimina(?:r|n)?"
          r"|perfectamente|sin fricci[oó]n",
}
MESSAGES = {
    "H5": "named method or approach without its concrete step (H5)",
    "H6": "background with no real event or date (H6)",
    "H8": "unbounded claim: say when it holds, or cut it (H8)",
}

# --- layer 2 (word hygiene) ------------------------------------------------------------------------------------------------
W = {
    "en": {
        "W1": (r"here'?s (?:the thing|what|why|this|that)|the (?:uncomfortable )?truth is|let me be clear|it turns out"
               r"|i'?m going to be honest|can we talk about", "throat-clearing opener: state the point"),
        "W2": (r"let that sink in|make no mistake|really|literally|genuinely|honestly|truly|fundamentally"
               r"|inherently|inevitably|interestingly|importantly|crucially|at its core|it'?s worth noting"
               r"|at the end of the day|when it comes to", "emphasis crutch or intensifier: delete it"),
        "W3": (r"game-?changers?|deep dives?|double down|lean(?:s|ing)? into|circle back|moving forward"
               r"|unpack(?:s|ing)?|delv(?:e|es|ed|ing)|tapestry|landscape", "business jargon: use the plain word"),
        "W4": (r"(?:it|this|that)'?s not (?:just |only |merely )?[^.,;\n]{1,50}[,;.] ?(?:it'?s|but|rather)\b"
               r"|\bisn'?t (?:about )?[^.,;\n]{1,40}[.,;] ?(?:it'?s|but)\b"
               r"|\bnot (?:just|only) [^.\n]{1,50}\bbut (?:also )?",
               "'not X, it's Y' contrast: state Y directly"),
        "W5": (r"let me walk you through|in this section,? we'?ll|as we'?ll see|the rest of this (?:essay|post|article)",
               "meta-commentary: let the text move"),
        "W6": ("—", "em dash: use a comma or a full stop"),
    },
    "es": {
        "W1": (r"lleva(?:s|r)? tu \w+ al siguiente nivel|desbloquea(?:r)?|sum[eé]rgete|descubre c[oó]mo"
               r"|en el mundo actual|en el panorama actual", "calque of English marketing copy (UNREVIEWED list)"),
        "W2": (r"realmente|verdaderamente|literalmente|sinceramente|honestamente|fundamentalmente|en esencia"
               r"|cabe destacar|es importante (?:destacar|se[ñn]alar|mencionar)|vale la pena (?:destacar|mencionar)",
               "emphasis crutch or intensifier (UNREVIEWED list)"),
        "W3": (r"se (?:ha )?procede(?:ido)? a|realizaci[oó]n de (?:la|el|los|las)", "bureaucratic phrasing (UNREVIEWED list)"),
        "W4": (r"no es (?:solo |solamente |simplemente )?[^.,;\n]{1,40}[,;] ?(?:es|sino)\b"
               r"|no (?:solo|solamente) [^.\n]{1,50}\bsino (?:tambi[eé]n)?", "'no es X, es Y' contrast (UNREVIEWED list)"),
    },
}

LIMITS_HEADING = re.compile(
    r"^#{1,6}\s*(?:limits?|limitations?|known (?:issues|limits)|caveats|what (?:it )?(?:does not|doesn't)|trade-?offs"
    r"|l[ií]mites|limitaciones|qu[eé] no (?:hace|cubre)|problemas conocidos)\b", re.I | re.M)
COMPARATOR = {
    "en": re.compile(r"\b(?:than|from|vs\.?|versus|compared|baseline|before|after|against|per|out of|of all|of the)\b", re.I),
    "es": re.compile(r"\b(?:frente a|respecto|en comparaci[oó]n|desde|antes de|despu[eé]s de|de los|de las|sobre el)\b"
                     r"|m[aá]s [^.]{0,20} que|menos [^.]{0,20} que", re.I),
}
PERCENT = re.compile(r"\d[\d.,]*\s?(?:%|percent|por ciento)", re.I)
NUMBER = re.compile(r"(?<![\w.:/-])\d[\d.,]*\d(?![\w.:/-])")
VERSION_OR_DATE = re.compile(r"\b\d+\.\d+\.\d+\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b")
SOURCE = re.compile(r"https?://|\]\(|`|\bR-\d+\b|\[\d+\]|\((?:see|ver) ", re.I)
FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.S | re.M)
OFF = re.compile(r"<!--\s*humanizelint:\s*off\b[^>]*-->.*?(?:<!--\s*humanizelint:\s*on\s*-->|\Z)", re.S)
SPAN = re.compile(r"`[^`\n]*`|https?://\S+|<!--.*?-->|<[^>\n]+>|\]\([^)\n]*\)", re.S)
ES_WORDS = re.compile(r"\b(?:de|que|el|la|los|las|y|en|un|una|para|con|por|del|se|es)\b", re.I)
EN_WORDS = re.compile(r"\b(?:the|and|of|to|is|in|for|with|that|it|are|on|this)\b", re.I)
DOC_MIN_WORDS = 300


def _blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def mask(text):
    """Blank out code fences, inline code, URLs, HTML, link targets and `humanizelint: off` regions (a rules page that
    quotes the patterns), keeping every line and column in place."""
    return SPAN.sub(_blank, FENCE.sub(_blank, OFF.sub(_blank, text)))


def detect_lang(text):
    es, en = len(ES_WORDS.findall(text)), len(EN_WORDS.findall(text))
    return "es" if es > en else "en"


def _pos(text, index):
    line = text.count("\n", 0, index) + 1
    return line, index - (text.rfind("\n", 0, index) + 1) + 1


def _word_hits(masked, pattern, rule, layer, message, suffix, flags=re.I):
    regex = re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % pattern if pattern != "—" else pattern, flags)
    for m in regex.finditer(masked):
        line, col = _pos(masked, m.start())
        yield Finding(rule, layer, line, col, m.group(0), message + suffix)


def _paragraphs(text):
    """(start offset, paragraph text) for each blank-line separated block."""
    pos = 0
    for block in re.split(r"(\n[ \t]*\n)", text):
        if block.strip():
            yield pos + (len(block) - len(block.lstrip())), block.strip()
        pos += len(block)


ALLOW_LINE = re.compile(r"humanizelint:\s*ok\s+((?:[HW]\d+\s*,?\s*)+)", re.I)


def _allowed(finding, lines, allow):
    """True when an `ok RULE` marker on the finding's own line, or an allow entry `RULE` / `RULE:text`, covers it."""
    marker = ALLOW_LINE.search(lines[finding.line - 1]) if finding.line <= len(lines) else None
    if marker and finding.rule in re.findall(r"[HW]\d+", marker.group(1).upper()):
        return True
    for entry in allow or ():
        rule, _, word = entry.partition(":")
        if rule.upper() == finding.rule and (not word or word.lower() == finding.match.lower()):
            return True
    return False


def scan(text, lang="auto", kind="doc", limits=False, allow=()):
    """Findings for a text. kind 'doc' runs the whole gate; kind 'ui' (short strings) skips H4, H6, H9 and H11.
    H9 (limits section) runs only with limits=True, for a README or feature page; a FAQ or an excerpt has none to find.
    A `<!-- humanizelint: ok H8 -->` marker on a line, or allow=("H8:never",), records a hit someone reviewed and kept."""
    if lang == "auto":
        lang = detect_lang(text)
    suffix = " (UNREVIEWED Spanish list)" if lang == "es" else ""
    masked = mask(text)
    out = []
    for rule, table in (("H5", H5), ("H8", H8)) + ((("H6", H6),) if kind == "doc" else ()):
        out += _word_hits(masked, table[lang], rule, 1, MESSAGES[rule], suffix)
    for rule, (pattern, message) in W[lang].items():
        out += _word_hits(masked, pattern, rule, 2, message, "")
    if kind == "doc":
        out += _numbers(text, masked, lang)
        words = len(re.findall(r"\w+", masked))
        if limits and words >= DOC_MIN_WORDS and not LIMITS_HEADING.search(masked):
            out.append(Finding("H9", 1, 1, 1, "", "no limits section: name a cost, limit or open problem (H9)"))
    lines = text.split("\n")
    out = [f for f in out if not _allowed(f, lines, allow)]
    return sorted(out, key=lambda f: (f.line, f.col, f.rule))


def _numbers(text, masked, lang):
    """H4: a percentage with no comparison in its sentence. H11: a number with no source in its paragraph."""
    found = []
    for start, para in _paragraphs(masked):
        if para.startswith(("|", "#")):
            continue
        for sent in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", para):
            for m in PERCENT.finditer(sent.group(0)):
                if not COMPARATOR[lang].search(sent.group(0)):
                    line, col = _pos(masked, start + sent.start() + m.start())
                    found.append(Finding("H4", 1, line, col, m.group(0), "number with no baseline or comparison (H4)"))
        original = text[start:start + len(para)]
        plain = VERSION_OR_DATE.sub(" ", para)
        m = NUMBER.search(plain) or PERCENT.search(plain)
        if m and not SOURCE.search(original):
            line, col = _pos(masked, start + m.start())
            found.append(Finding("H11", 1, line, col, m.group(0).strip(), "number with no source a reader could find (H11)"))
    return found


def report(findings, path):
    if not findings:
        return "%s: no findings" % path
    return "\n".join("%s:%d:%d %s L%d %r: %s" % (path, f.line, f.col, f.rule, f.layer, f.match, f.message)
                     for f in findings)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="humanizelint", description="Mechanical checks of the humanize skill (R-267).")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--lang", choices=("auto", "en", "es"), default="auto")
    ap.add_argument("--kind", choices=("doc", "ui"), default="doc")
    ap.add_argument("--require-limits", action="store_true", help="also check H9: a README or feature page needs a limits section")
    ap.add_argument("--allow", action="append", default=[], metavar="RULE[:text]", help="keep a reviewed hit, e.g. H8:nunca")
    args = ap.parse_args(argv)
    gate = False
    for path in args.paths:
        with open(path, encoding="utf-8") as f:
            findings = scan(f.read(), args.lang, args.kind, args.require_limits, args.allow)
        print(report(findings, path))
        gate = gate or any(f.layer == 1 for f in findings)
    return 1 if gate else 0


if __name__ == "__main__":
    sys.exit(main())
