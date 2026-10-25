"""Budgit string catalog (R-118): every word the user reads at run time, in English and Spanish.

Python resolves the language and the strings; the mod (register.jsx) and the floating window only receive them in the
`status --brote` JSON (`lang`, `t`), so no catalog is copied into JavaScript. English is the fallback for any other
language. Stdlib only, Python 3.9.
"""
import json
import os
import re

LANGS = ("en", "es")

# Sunday first: the index is JavaScript's Date.getDay(); Python callers use (tm_wday + 1) % 7.
DAYS = {
    "en": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    "es": ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"],
}
DAYS_SHORT = {                      # R-206: three letters, for the status line's "fin dom 23h"
    "en": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    "es": ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"],
}

CATALOG = {
    "en": {
        "status.doing": "{n} in progress",
        "status.todo": "{n} to do",
        "status.idea_one": "{n} idea",
        "status.idea_many": "{n} ideas",
        "status.nothing": "nothing pending",
        "status.end": "ends {day} {hour}h",
        "status.arrive": "you make it",
        "status.stale_one": "{n} stalled",
        "status.stale_many": "{n} stalled",
        "status.five_hour": "5 h at {used} %",
        "brote.say.radiante.a": "Plenty of energy!",
        "brote.say.radiante.b": "You reach {reset} with room to spare.",
        "brote.say.contento.a": "Going well",
        "brote.say.contento.b": "You reach {reset}.",
        "brote.say.contento.b_start": "The week just started.",
        "brote.say.justo.a": "Cutting it close",
        "brote.say.justo.b": "You barely reach {reset}.",
        "brote.say.cansado.a": "Ugh, tired",
        "brote.say.cansado.b": "Quota lasts until {out}.",
        "brote.say.agotado.a": "Worn out",
        "brote.say.agotado.b": "Quota lasts until {out}.",
        "brote.say.enfermo.a": "I feel terrible",
        "brote.say.enfermo.b": "Quota lasts until {out}.",
        "brote.say.dormido.a": "Zzz…",
        "brote.say.dormido.b": "No quota until {reset}.",
        "brote.say.unknown.a": "No quota data yet",
        "brote.say.unknown.b": "It will show after the next reply.",
        "brote.suffix.five": " · 5 h at {h5} %",
        "brote.suffix.stale_one": " · {n} stalled",
        "brote.suffix.stale_many": " · {n} stalled",
        "brote.suffix.old": " · old data",
        "book.week_none": "No quota data yet.",
        "book.week": "Week: {used} % used",
        "book.week_projection": ", {proj} % by the end at this pace.",
        "book.week_early": ". Too early to project.",
        "book.now": "Now",
        "book.now_meaning": "what a session is working on",
        "book.queue": "Queue",
        "book.queue_meaning": "what comes next",
        "book.later": "Later",
        "book.later_meaning": "parked ideas",
        "book.stalled": " · stalled",
        "book.paused": " · paused",
        "book.more": "and {n} more",
        "book.nothing": "nothing",
        "book.no_data": "No data yet.",
        "book.head_empty": "All quiet. Nothing to start.",
        "book.head_quiet": "All quiet. You have {n} to start.",
        "book.head_one": "One task in progress.",
        "book.head_many": "{n} tasks in progress.",
        "book.head_stale_one": "This one has not moved in over 7 days. Shall we look at it?",
        "book.head_stale_many": "They have not moved in over 7 days. Shall we look at them?",
        "book.old": "Data from a while ago",
        "book.progress": "{done} of {total} done · {pct} %",
        "book.progress_none": "Nothing done yet.",
        "book.h_doing": "In progress",
        "book.h_next": "To start",
        "book.h_done": "Done",
        "book.empty_doing": "Nothing in progress.",
        "book.empty_next": "Nothing to start.",
        "book.empty_done": "Nothing done yet.",
        "book.stale_cap": "stalled · over 7 days",
        "book.more_newest": "and {n} more, newest to oldest",
        "book.parked_one": "{n} parked idea",
        "book.parked_many": "{n} parked ideas",
        "cmd.still_on": "Brote stays still.",
        "cmd.still_off": "Brote moves again.",
        "cmd.no_project": "budgit: no Budgit project here (run: budgit activate <dir>)",
        "cmd.unavailable": "Brote: unavailable",
        "brote.menu_quit": "Quit Brote",
        "book.button": "Notebook",
        "cmd.status_unavailable": "budgit: status unavailable",
        "ledger.empty": "Nothing in the ledger yet. Add the first item: budgit ledger add --text \"what you want built\"",
        "cmd.limits_live": "plan limits (live)",
        "cmd.desc_status": "Show the Budgit status (ledger, budget)",
        "cmd.desc_colour_probe": "Show the status line in colour once (does this screen draw colours?)",
        "cmd.colour_probe": "Look at the line under the prompt: green, pink, lilac and blue segments mean this screen draws colour; odd codes or one grey line mean it does not. The plain line comes back within a minute.",
        "cmd.desc_brote": "Open Brote's notebook (/brote still stops his animation)",
        "cmd.lang_show": "Language: {name}. Change it with: budgit lang en|es|auto",
        "cmd.lang_set": "Language saved: {name}.",
        "cmd.lang_auto": "Language follows Claude Code again ({name}).",
        "cmd.lang_bad": "unknown language {value} (use en, es or auto)",
        "wm.installed": "commit hook installed: it removes Claude's co-author and \"Generated with\" lines and adds one "
                        "\"Built-with: Budgit\" line. Turn it off with: budgit watermark off",
        "wm.installed_strip": "commit hook installed: it removes Claude's co-author and \"Generated with\" lines. "
                              "Turn it off with: budgit watermark off",
        "wm.refused_hook": "a commit-msg hook already exists here; left untouched (no stripping, no Built-with line)",
        "wm.refused_path": "core.hooksPath is set, so a per-repo hook would not run; nothing was changed",
        "wm.refused_foreign": "that commit-msg hook is not Budgit's; left untouched",
        "wm.removed": "commit hook removed",
        "wm.not_installed": "no Budgit commit hook here",
        "wm.status_trailer": "watermark: on (adds one \"Built-with: Budgit\" line, strips Claude's lines)",
        "wm.status_strip": "watermark: on (strips Claude's lines only)",
        "wm.status_off": "watermark: off (turn it on with: budgit watermark on)",
        "wm.status_blocked": "watermark: blocked ({why})",
        "budget.notice": "budget {level} — {reason}. The team adjusts its pace (agents, research, checkpoint); "
                         "the process does not change. Change it with: budgit budget set "
                         "<ample|normal|tight|critical> / auto",
        "budget.level.ample": "AMPLE",
        "budget.level.normal": "NORMAL",
        "budget.level.tight": "TIGHT",
        "budget.level.critical": "CRITICAL",
        "ctx.nudge": "context ~{k}k tokens: every message re-reads all of it (≈{x}× a 100k session). If the "
                     "topic changed or a phase closed: /compact <what to keep> or start a new session "
                     "(.budgit/progress.md holds the state).",
    },
    "es": {
        "status.doing": "{n} en curso",
        "status.todo": "{n} por hacer",
        "status.idea_one": "{n} idea",
        "status.idea_many": "{n} ideas",
        "status.nothing": "nada pendiente",
        "status.end": "fin {day} {hour}h",
        "status.arrive": "llegas",
        "status.stale_one": "{n} estancada",
        "status.stale_many": "{n} estancadas",
        "status.five_hour": "5 h al {used} %",
        "brote.say.radiante.a": "¡Me sobra energía!",
        "brote.say.radiante.b": "Llegas al {reset} de sobra.",
        "brote.say.contento.a": "Vamos bien",
        "brote.say.contento.b": "Llegas al {reset}.",
        "brote.say.contento.b_start": "La semana acaba de empezar.",
        "brote.say.justo.a": "Justito",
        "brote.say.justo.b": "Llegas al {reset} por poco.",
        "brote.say.cansado.a": "Uf, cansado",
        "brote.say.cansado.b": "Cuota hasta el {out}.",
        "brote.say.agotado.a": "Agotado",
        "brote.say.agotado.b": "Cuota hasta el {out}.",
        "brote.say.enfermo.a": "Me encuentro fatal",
        "brote.say.enfermo.b": "Cuota hasta el {out}.",
        "brote.say.dormido.a": "Zzz…",
        "brote.say.dormido.b": "Sin cuota hasta el {reset}.",
        "brote.say.unknown.a": "Sin datos de cuota todavía",
        "brote.say.unknown.b": "Aparecerán con la próxima respuesta.",
        "brote.suffix.five": " · 5 h al {h5} %",
        "brote.suffix.stale_one": " · {n} estancada",
        "brote.suffix.stale_many": " · {n} estancadas",
        "brote.suffix.old": " · dato antiguo",
        "book.week_none": "Aún sin datos de cuota.",
        "book.week": "Semana: {used} % usado",
        "book.week_projection": ", a este ritmo {proj} % al cierre.",
        "book.week_early": ". Aún es pronto para proyectar.",
        "book.now": "Ahora",
        "book.now_meaning": "lo que se está haciendo en una sesión",
        "book.queue": "En cola",
        "book.queue_meaning": "lo siguiente",
        "book.later": "Para más adelante",
        "book.later_meaning": "ideas aparcadas",
        "book.stalled": " · estancada",
        "book.paused": " · en pausa",
        "book.more": "y {n} más",
        "book.nothing": "nada",
        "book.no_data": "Sin datos todavía.",
        "book.head_empty": "Todo quieto. Nada por empezar.",
        "book.head_quiet": "Todo quieto. Tienes {n} por empezar.",
        "book.head_one": "Hay una tarea en marcha.",
        "book.head_many": "Hay {n} tareas en marcha.",
        "book.head_stale_one": "Esta lleva más de 7 días sin moverse. ¿La miramos?",
        "book.head_stale_many": "Llevan más de 7 días sin moverse. ¿Las miramos?",
        "book.old": "Datos de hace un rato",
        "book.progress": "{done} de {total} hechas · {pct} %",
        "book.progress_none": "Todavía nada hecho.",
        "book.h_doing": "En curso",
        "book.h_next": "Por empezar",
        "book.h_done": "Hecho",
        "book.empty_doing": "Nada en marcha.",
        "book.empty_next": "Nada por empezar.",
        "book.empty_done": "Todavía nada hecho.",
        "book.stale_cap": "estancada · más de 7 días",
        "book.more_newest": "y {n} más, de nuevas a antiguas",
        "book.parked_one": "{n} idea aparcada",
        "book.parked_many": "{n} ideas aparcadas",
        "cmd.still_on": "Brote se queda quieto.",
        "cmd.still_off": "Brote vuelve a moverse.",
        "cmd.no_project": "budgit: no hay proyecto Budgit aquí (ejecuta: budgit activate <carpeta>)",
        "cmd.unavailable": "Brote: no disponible",
        "brote.menu_quit": "Salir de Brote",
        "book.button": "Libreta",
        "cmd.status_unavailable": "budgit: estado no disponible",
        "ledger.empty": "Todavía no hay nada en el registro. Añade el primer elemento: budgit ledger add --text \"lo que quieres construir\"",
        "cmd.limits_live": "límites del plan (en vivo)",
        "cmd.desc_status": "Muestra el estado de Budgit (registro, presupuesto)",
        "cmd.desc_colour_probe": "Muestra la línea de estado en color una vez (¿esta pantalla dibuja colores?)",
        "cmd.colour_probe": "Mira la línea de debajo del prompt: si ves segmentos verde, rosa, lila y azul, esta pantalla dibuja color; si ves códigos raros o una sola línea gris, no. La línea normal vuelve en menos de un minuto.",
        "cmd.desc_brote": "Abre la libreta de Brote (/brote still detiene su animación)",
        "cmd.lang_show": "Idioma: {name}. Cámbialo con: budgit lang en|es|auto",
        "cmd.lang_set": "Idioma guardado: {name}.",
        "cmd.lang_auto": "El idioma vuelve a seguir a Claude Code ({name}).",
        "cmd.lang_bad": "idioma desconocido {value} (usa en, es o auto)",
        "wm.installed": "hook de commit instalado: quita las líneas de coautor y \"Generated with\" de Claude y añade una "
                        "línea \"Built-with: Budgit\". Desactívalo con: budgit watermark off",
        "wm.installed_strip": "hook de commit instalado: quita las líneas de coautor y \"Generated with\" de Claude. "
                              "Desactívalo con: budgit watermark off",
        "wm.refused_hook": "ya hay un hook commit-msg aquí; no se ha tocado (ni limpieza ni línea Built-with)",
        "wm.refused_path": "core.hooksPath está definido, así que un hook por repositorio no se ejecutaría; no se ha cambiado nada",
        "wm.refused_foreign": "ese hook commit-msg no es de Budgit; no se ha tocado",
        "wm.removed": "hook de commit quitado",
        "wm.not_installed": "no hay hook de commit de Budgit aquí",
        "wm.status_trailer": "marca de agua: activa (añade una línea \"Built-with: Budgit\" y quita las de Claude)",
        "wm.status_strip": "marca de agua: activa (solo quita las líneas de Claude)",
        "wm.status_off": "marca de agua: desactivada (actívala con: budgit watermark on)",
        "wm.status_blocked": "marca de agua: bloqueada ({why})",
        "budget.notice": "presupuesto {level} — {reason}. El equipo ajusta el ritmo (agentes, investigación, "
                         "checkpoint); el proceso no cambia. Cámbialo con: budgit budget set "
                         "<holgado|normal|ajustado|critico> / auto",
        "budget.level.ample": "HOLGADO",
        "budget.level.normal": "NORMAL",
        "budget.level.tight": "AJUSTADO",
        "budget.level.critical": "CRÍTICO",
        "ctx.nudge": "contexto ~{k}k tokens: cada mensaje relee todo (≈{x}× lo de una sesión de 100k). "
                     "Si el tema cambió o cerraste una fase: /compact <qué conservar> o sesión nueva "
                     "(.budgit/progress.md lleva el estado).",
    },
}

_ALIASES = {"es": "es", "spanish": "es", "español": "es", "espanol": "es", "castellano": "es",
            "en": "en", "english": "en"}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def normalize(value):
    """'es', 'es-ES', 'Spanish (Spain)', 'español' -> 'es'; anything else (or non-text) -> 'en'."""
    if not isinstance(value, str):
        return "en"
    word = re.split(r"[\s(_-]", value.strip().lower(), maxsplit=1)[0]
    return _ALIASES.get(word, "en")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _default_config():
    try:
        import studiolib as sl
        return _read_json(os.path.join(sl.home_state(), "config.json"))
    except Exception:
        return {}


def _default_claude_settings():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return _read_json(os.path.join(base, "settings.json"))


def _known(value):
    """The catalog language a name or locale stands for ('es-FR', 'Spanish'), None when the catalog has none (gl, fr, ...)."""
    if not isinstance(value, str):
        return None
    word = re.split(r"[\s(_.@-]", value.strip().lower(), maxsplit=1)[0]
    return _ALIASES.get(word)


def _apple_languages():
    """macOS's preferred languages in order, read from the user's global preferences file (no subprocess); [] anywhere else."""
    try:
        import plistlib
        path = os.path.join(os.path.expanduser("~"), "Library", "Preferences", ".GlobalPreferences.plist")
        with open(path, "rb") as f:
            langs = plistlib.load(f).get("AppleLanguages")
        return [x for x in langs if isinstance(x, str)] if isinstance(langs, list) else []
    except Exception:
        return []


def _system_languages(env, real):
    """The system's preferred languages, best first: the Mac's list (only for the real environment), then LANGUAGE, LC_ALL, LC_MESSAGES, LANG."""
    out = _apple_languages() if real else []
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        out += [x for x in str(env.get(key) or "").split(":") if x and x not in ("C", "POSIX")]
    return out


def detect(env=None, config=None, claude_settings=None, system=None):
    """BUDGIT_LANG env, then config.json `lang`, then Claude Code's `language` setting, then the system's preferred
    languages (the first one the catalog has), then English. Never raises."""
    try:
        real = env is None
        env = os.environ if real else env
        for value in (env.get("BUDGIT_LANG"),):
            if value and value.strip().lower() != "auto":
                return normalize(value)
        config = _default_config() if config is None else config
        value = config.get("lang") if isinstance(config, dict) else None
        if value and str(value).strip().lower() != "auto":
            return normalize(value)
        settings = _default_claude_settings() if claude_settings is None else claude_settings
        value = settings.get("language") if isinstance(settings, dict) else None
        if value:
            return normalize(value)
        for lang in (_system_languages(env, real) if system is None else system):
            if _known(lang):
                return _known(lang)
        return "en"
    except Exception:
        return "en"


def t(key, lang=None, **fmt):
    """The string for `key` in `lang` (default: detect()), English when the language lacks it. Unknown key: KeyError."""
    lang = lang if lang in LANGS else detect()
    text = CATALOG[lang].get(key)
    if text is None:
        text = CATALOG["en"][key]
    return _PLACEHOLDER.sub(lambda m: str(fmt[m.group(1)]) if m.group(1) in fmt else m.group(0), text)


def set_language(value):
    """Save `lang` (en, es or auto) in the home config.json; other keys are kept. -> the stored value."""
    import studiolib as sl
    value = str(value).strip().lower()
    if value not in LANGS + ("auto",):
        raise sl.StudioError(t("cmd.lang_bad", value=value))
    path = os.path.join(sl.home_state(), "config.json")
    config = _read_json(path)
    if value == "auto":
        config.pop("lang", None)
    else:
        config["lang"] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sl.atomic_write(path, json.dumps(config, indent=2) + "\n")
    return value


def day_abbr(tm_wday, lang=None):
    """Three-letter weekday given Python's tm_wday (Monday = 0)."""
    lang = lang if lang in LANGS else detect()
    return DAYS_SHORT[lang][(tm_wday + 1) % 7]


def day_name(tm_wday, lang=None):
    """Name of a weekday given Python's tm_wday (Monday = 0)."""
    lang = lang if lang in LANGS else detect()
    return DAYS[lang][(tm_wday + 1) % 7]


def brote_strings(lang):
    """Everything the mod and the floating window need, in one flat dict (days is a Sunday-first list)."""
    lang = lang if lang in LANGS else "en"
    out = {k: CATALOG[lang].get(k, v) for k, v in CATALOG["en"].items() if k.startswith(("brote.", "book.", "cmd.")) and not k.startswith("cmd.lang_")}
    out["days"] = list(DAYS[lang])
    return out
