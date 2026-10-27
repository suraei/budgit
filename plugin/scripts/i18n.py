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
        "skills.why.code.continuity": "The project's author skill continues the work.",
        "skills.why.code.default": "Default for a tie on evidence in this kind of job.",
        "skills.why.code.score": "Highest score ({score}).",
        "skills.why.code.band": "Within one point of {top}, with lower risk or cost.",
        "skills.why.code.gap": "No skill has enough fit for this job: the project rules apply.",
        "skills.why.beaten": "{skill} (score {score} against {pick})",
        "skills.no_registry": "No skills registry found in the kickoff skill folder (reference/skills-registry.json).",
        "skills.registry_bad": "The skills registry at {path} cannot be read ({why}).",
        "skills.reason.unregistered": "installed but not in the registry",
        "skills.reason.status": "status {arg}: not a selectable skill",
        "skills.reason.disabled": "turned off or limited to the first security layer",
        "skills.reason.hold": "security review says HOLD",
        "skills.reason.drifted": "files changed since the reviewed version",
        "skills.reason.missing": "not installed on this machine",
        "skills.reason.update_pending": "an update waits for a security review",
        "skills.reason.requires": "needs {arg}, which is not available here",
        "skills.reason.not_for": "not for {arg}",
        "skills.reason.project_rule": "clashes with the project rule {arg}",
        "skills.reason.same_skill": "the author cannot check its own work",
        "skills.reason.agent_cap": "would run more agents than this budget level allows",
        "skills.reason.read_description": "only its description was read, so it is never the main pick",
        "skills.why.head": "Job {job} at budget {level}, stakes {stakes}.",
        "skills.why.primary": "Pick: {skill}. {why}",
        "skills.why.load_name_only": "  {id} loads by name only: invoke it explicitly.",
        "skills.why.load_user_only": "  {id} only loads when you invoke it yourself.",
        "skills.why.load_off": "  {id} is switched off in the load plan.",
        "skills.why.gap": "No registered skill fits. Use: {fallback}. Watch row: {row}.",
        "skills.why.checker": "Checker for {job}: {skill}",
        "skills.why.provisional": "Provisional pick from the default table: two skills tie on evidence. Run a bake-off before relying on it.",
        "skills.why.bakeoff": "Two skills tie on evidence: a bake-off is needed.",
        "skills.why.debate": "Debate: the author and a checker argue, a judge decides.",
        "skills.why.runs": "Run the simulated user {n} time(s), one after another; report the spread and the disagreements, not one answer, and say that every persona comes from one model.",
        "skills.why.runs_none": "No simulated-user run at this budget: say the evidence was not simulated.",
        "skills.why.recommend": "Policy: {text}",
        "skills.why.needs_d39": "(printed only; the debate and agent runs are not built yet)",
        "skills.why.rejected_head": "Left out:",
        "skills.why.beat_head": "Beaten by the pick:",
        "skills.why.fallback": "If it fails: {chain}",
        "skills.why.logged": "Logged in {path}",
        "skills.why_need_job": "Say which job: budgit skills why --job design/build [--traits surface=app-ui,stakes=pillar]",
        "skills.none": "No skill matches.",
        "security.head": "Security plan for {trigger} at {level} budget ({kind})",
        "security.kind_relevant": "security-relevant change",
        "security.kind_routine": "routine change",
        "security.classes": "  risk classes: {classes}",
        "security.protected": "  protected paths touched: {paths}",
        "security.none": "  no reviewer for this trigger",
        "security.reviewer": "  {id}  {name}  -> {run}",
        "security.dropped": "  left out {id}: {reason}",
        "security.reason.critical": "critical budget: no Claude agent now",
        "security.reason.protected_path": "a protected path is touched, so the diff does not leave the machine",
        "security.reason.codex_off": "the Codex lane is off or not installed",
        "security.queued": "  queued for later: {ids}",
        "security.foot": "Printed only: nothing was run.",
        "security.logged": "Logged in {path} with its trigger and reviewers.",
        "security.bad_trigger": "Unknown trigger {trigger}. Known: {known}",
        "security.unknown_action": "Unknown action {action}. Use: budgit security plan --trigger T1..T4 --paths ...",
        "hyp.none": "No hypotheses.",
        "hyp.head": "Hypotheses: {open} open, {confirmed} confirmed, {refuted} refuted, {unclear} unclear (a simulated user's claim is never a result until real data scores it)",
        "hyp.evidence": "scored {date}: {text}",
        "hyp.added": "Filed {id} as open.",
        "hyp.scored": "{id} is now {status}.",
        "hyp.imported": "{n} hypothesis(es) filed from the report: {ids}",
        "hyp.bad_status": "Cannot score as {status}. Use: {known}",
        "hyp.need_evidence": "Say which real signal settled {id}: --evidence \"...\" (only unclear needs none).",
        "hyp.missing": "No hypothesis {id}.",
        "hyp.unreadable": "Cannot read {path} ({why}).",
        "hyp.not_a_report": "{path} is not a persona report: its first line must start with HYPOTHESIS.",
        "hyp.need_both": "Give both: --claim \"...\" --confirm-with \"...\"",
        "hyp.need_score_args": "Usage: budgit hypotheses score H-n confirmed|refuted|unclear --evidence \"...\"",
        "hyp.need_report": "Usage: budgit hypotheses import REPORT_FILE",
        "hyp.no_project": "This folder is not a Budgit project.",
        "hyp.unknown_action": "Unknown action {action}. Use: list, add, score, import",
        "skills.count": "{n} skills, registry {version}",
        "skills.list.head": "  id                                 status     on   review  cost  fit",
        "skills.show_missing": "No skill with the id '{id}' in the registry.",
        "skills.check.clean": "Skills: nothing to report.",
        "skills.check.head": "Skills: {n} finding(s).",
        "skills.check.env": "{id}: the setting {var} is missing",
        "skills.check.binary_sha256": "{id}: the bundled program differs from the reviewed one",
        "skills.check.drifted": "{id}: files changed since the review",
        "skills.check.missing": "{id}: registered but not installed",
        "skills.check.unregistered": "{id}: installed but not in the registry",
        "skills.check.flags_changed": "{id}: new hooks, servers or hosts: {detail}",
        "skills.load.none": "Load plan: nothing to change.",
        "skills.load.head": "Load plan: {n} skill(s) would load by name only, saving about {chars} characters (about {tokens} tokens) per session.",
        "skills.load.block": "Add this to the skillOverrides block of your settings (nothing was written):",
        "skills.load.foot": "Plugin skills cannot be switched this way; they are listed above for the record.",
        "skills.build.done": "Registry rebuilt: {n} unread skill(s) added. Written: {path}",
        "skills.unknown_action": "Unknown skills action '{action}'. Use list, show, why, check, build or load-plan.",
        "status.doing": "{n} in progress",
        "status.todo": "{n} to do",
        "status.idea_one": "{n} idea",
        "status.idea_many": "{n} ideas",
        "status.nothing": "nothing pending",
        "status.no_project": "\u25cc no ledger here",
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
        "book.now_session": "Session in progress",
        "book.waiting_permission": "waiting for your permission",
        "book.waiting_question": "has a question for you",
        "book.waiting_other": "waiting for you",
        "book.quiet_sessions": "{n} sessions resting",
        "book.project_other": "other",
        "book.no_live_data": "No live-session data.",
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
        "book.badge_wait_one": "waits for you",
        "book.badge_wait_many": "wait for you",
        "book.badge_working": "working",
        "book.badge_resting": "resting",
        "book.h_week": "This week",
        "book.week_done": "{n} done",
        "book.week_letters": "MTWTFSS",
        "book.h_wait_one": "Waits for you",
        "book.h_wait_many": "Wait for you",
        "book.h_working": "Working",
        "book.needs": "needs {what}",
        "book.needs_permission": "your permission",
        "book.needs_question": "an answer",
        "book.needs_other": "something from you",
        "book.no_item": "no task claimed",
        "book.rest_one": "{n} session resting",
        "book.rest_paused_one": "{n} task of theirs paused",
        "book.rest_paused_many": "{n} tasks of theirs paused",
        "book.group_next": "{n} to start",
        "book.fold_quiet": "nobody working \u00b7 {n} to start",
        "book.more_in": "and {n} more in {projects}",
        "book.join_and": "and",
        "book.today": "Today",
        "book.yesterday": "Yesterday",
        "book.ago_s": "{n} s ago",
        "book.ago_min": "{n} min ago",
        "book.ago_h": "{n} h ago",
        "book.since_at": "since {hm}",
        "book.since_yesterday": "since yesterday",
        "book.since_day": "since {day}",
        "book.live_chip": "live \u00b7 last change {ago}",
        "cmd.still_on": "Brote stays still.",
        "cmd.still_off": "Brote moves again.",
        "cmd.no_project": "budgit: no Budgit project here (run: budgit activate <dir>)",
        "cmd.unavailable": "Brote: unavailable",
        "brote.menu_quit": "Quit Brote",
        "brote.stuck": "Claude stopped with an error",
        "brote.update_title": "Update Budgit to {v}?",
        "brote.update_body": "Chats that are already open keep the old version until you reopen them.",
        "brote.update_yes": "Update",
        "brote.update_no": "Not now",
        "brote.update_done": "Budgit {v} is installed. Reopen your chats to use it.",
        "brote.update_fail": "The update did not finish. Run these in a terminal:\n{commands}",
        "brote.update_menu": "Update Budgit to {v}\u2026",
        "book.button": "Notebook",
        "cmd.status_unavailable": "budgit: status unavailable",
        "ledger.empty": "Nothing in the ledger yet. Add the first item: budgit ledger add --text \"what you want built\"",
        "cmd.limits_live": "plan limits (live)",
        "cmd.desc_status": "Show the Budgit status (ledger, budget)",
        "cmd.desc_colour_probe": "Show the status line in colour once (does this screen draw colours?)",
        "cmd.desc_bar_probe": "Show what this screen and folder do with the Budgit line under the prompt",
        "cmd.desc_brotewin": "Brote's floating window: on, off, status, uninstall",
        "cmd.brote_usage": "Use /budgit-brote on, off, stop, status, uninstall.",
        "cmd.brote_unavailable": "Brote's floating window needs macOS and the marketplace plugin.",
        "cmd.desc_float": "Old name of /budgit-brote",
        "cmd.float_usage": "Use /budgit-brote on, off, stop, status, uninstall.",
        "cmd.float_unavailable": "Brote's floating window needs macOS and the marketplace plugin.",
        "cmd.bar_probe_none": "none (no ledger found)",
        "cmd.chat_link_layout": "budgit: could not read the chat records of the desktop app (its folder layout may have changed); no link printed.",
        "release.head": "Release check: {built} built, {blocking} unbuilt, {deferred} deferred.",
        "release.built": "Built (with evidence):",
        "release.blocking": "Unbuilt (open or in progress, not deferred):",
        "release.deferred": "Deferred (parked by the user, stays out of this release):",
        "release.next": "Not ready: build each unbuilt item, or ask the user to defer it (`budgit ledger phase later R-n`), then run this again.",
        "release.clear": "Every item is built or deferred.",
        "release.hidden": "Advice: {n} hidden character(s) in {files} tracked file(s). List them with `budgit lint --tracked --hidden`; `budgit lint --tracked --fix` removes them.",
        "graph.head": "Graph check (advice only, it never blocks): {n} finding(s).",
        "graph.unverified": "Built, but no PASS receipt (ask budgit:verifier):",
        "graph.orphans": "In progress, but the session that claimed it ended or nobody owns it:",
        "graph.cites": "Built, but it names an item that is still unbuilt:",
        "graph.unlinked": "Commits with no item id:",
        "graph.commit_open": "Commits that name an item still marked open (not in progress):",
        "graph.bad_ref": "--since is not a usable git ref; commits were not checked.",
        "graph.git_failed": "git could not read that commit range; commits were not checked.",
        "graph.state_dead": "claimant ended",
        "graph.state_unclaimed": "no owner",
        "cmd.desc_migrate": "Move this install to the public marketplace (shows the commands, runs them only on your yes)",
        "cmd.migrate_nothing": "Nothing to migrate: this plugin is not installed from a live folder.",
        "cmd.migrate_need_repo": "budgit: give the public repo with --repo OWNER/NAME (it looks like owner/name)",
        "cmd.migrate_plan": "This plugin runs from a live folder ({old}). To move to the public marketplace:\n{commands}\nThe old plugin is only disabled, not deleted; your projects and ledgers stay as they are. To go back: {undo}",
        "cmd.migrate_terminal": "budgit: the migration needs you at a terminal: run this command there and read the steps before answering.",
        "cmd.migrate_no_claude": "budgit: the claude command was not found on this terminal; run the commands above by hand.",
        "cmd.migrate_ask": "Run these commands now? [y/N] ",
        "cmd.migrate_declined": "Nothing was changed.",
        "cmd.migrate_failed": "budgit: this step failed: {step}. Your old plugin is still enabled unless the disable step ran. To go back: {undo}",
        "cmd.migrate_done": "Done. Reopen your chats so they load the new plugin.",
        "cmd.desc_colour": "Show or set the colour of the line under the prompt (on, off, auto)",
        "cmd.colour_on": "on",
        "cmd.colour_off": "off",
        "cmd.colour_show": "Line colour: {state}. Change it with: budgit colour on|off|auto",
        "cmd.colour_set": "Line colour: {state}.",
        "status.update_reopen": "\u21bb reopen",
        "cmd.desc_update": "How to update Budgit (this command only prints the commands and runs nothing)",
        "cmd.update_commands": "To update Budgit, run these two commands, then reopen the chat:\n{commands}\nThis command only prints them; it runs nothing.",
        "cmd.update_check_new": "A newer Budgit is out: {latest} (you have {current}).",
        "cmd.update_check_same": "Budgit is up to date ({current}).",
        "cmd.update_check_failed": "budgit: could not reach the public repo to check for updates (are you offline?)",
        "cmd.bar_probe": "Budgit bar probe\n  screen: {surface}\n  Claude Code: {engine}\n  Budgit: {plugin} {version}\n  folder: {folder}\n  project: {project}\n  line set: {line}\nIf that line is not under the prompt box, copy this text and send it back.",
        "cmd.colour_probe": "Look at the line under the prompt: green, pink, lilac and blue segments mean this screen draws colour. Odd codes or one grey line mean it does not: run budgit colour off. The usual line comes back within a minute.",
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
        "skills.why.code.continuity": "La skill de autoría del proyecto sigue con el trabajo.",
        "skills.why.code.default": "Opción por defecto cuando hay empate en pruebas en este tipo de trabajo.",
        "skills.why.code.score": "Mayor puntuación ({score}).",
        "skills.why.code.band": "A un punto de {top}, con menos riesgo o coste.",
        "skills.why.code.gap": "Ninguna skill encaja lo bastante en este trabajo: valen las reglas del proyecto.",
        "skills.why.beaten": "{skill} (puntuación {score} frente a {pick})",
        "skills.no_registry": "No encuentro el registro de skills en la carpeta del kickoff (reference/skills-registry.json).",
        "skills.registry_bad": "No se puede leer el registro de skills en {path} ({why}).",
        "skills.reason.unregistered": "instalada pero sin entrada en el registro",
        "skills.reason.status": "estado {arg}: no es una skill elegible",
        "skills.reason.disabled": "apagada o limitada a la primera capa de seguridad",
        "skills.reason.hold": "la revisión de seguridad dice HOLD",
        "skills.reason.drifted": "sus ficheros cambiaron desde la versión revisada",
        "skills.reason.missing": "no está instalada en esta máquina",
        "skills.reason.update_pending": "hay una actualización esperando revisión de seguridad",
        "skills.reason.requires": "necesita {arg}, que aquí no está disponible",
        "skills.reason.not_for": "no sirve para {arg}",
        "skills.reason.project_rule": "choca con la regla del proyecto {arg}",
        "skills.reason.same_skill": "quien hace el trabajo no puede revisarlo",
        "skills.reason.agent_cap": "lanzaría más agentes de los que permite este nivel de presupuesto",
        "skills.reason.read_description": "solo se leyó su descripción, así que nunca es la elección principal",
        "skills.why.head": "Trabajo {job} con presupuesto {level} y riesgo {stakes}.",
        "skills.why.primary": "Elegida: {skill}. {why}",
        "skills.why.load_name_only": "  {id} se carga solo por el nombre: invócala de forma explícita.",
        "skills.why.load_user_only": "  {id} solo se carga cuando la invocas tú.",
        "skills.why.load_off": "  {id} está apagada en el plan de carga.",
        "skills.why.gap": "Ninguna skill registrada encaja. Usa: {fallback}. Fila de vigilancia: {row}.",
        "skills.why.checker": "Revisión de {job}: {skill}",
        "skills.why.provisional": "Elección provisional de la tabla por defecto: dos skills empatan en pruebas. Haz un duelo antes de fiarte de ella.",
        "skills.why.bakeoff": "Dos skills empatan en pruebas: hace falta un duelo.",
        "skills.why.debate": "Debate: quien hace el trabajo y quien revisa discuten, y un juez decide.",
        "skills.why.runs": "Ejecuta el usuario simulado {n} vez/veces, una tras otra; informa de la dispersión y los desacuerdos, no de una sola respuesta, y di que todos los personajes salen de un mismo modelo.",
        "skills.why.runs_none": "Con este presupuesto no se ejecuta el usuario simulado: di que la evidencia no se simuló.",
        "skills.why.recommend": "Política: {text}",
        "skills.why.needs_d39": "(solo se imprime; el debate y los agentes todavía no están construidos)",
        "skills.why.rejected_head": "Descartadas:",
        "skills.why.beat_head": "Superadas por la elegida:",
        "skills.why.fallback": "Si falla: {chain}",
        "skills.why.logged": "Registrado en {path}",
        "skills.why_need_job": "Di qué trabajo: budgit skills why --job design/build [--traits surface=app-ui,stakes=pillar]",
        "skills.none": "Ninguna skill encaja.",
        "security.head": "Plan de seguridad para {trigger} con presupuesto {level} ({kind})",
        "security.kind_relevant": "cambio sensible para la seguridad",
        "security.kind_routine": "cambio rutinario",
        "security.classes": "  clases de riesgo: {classes}",
        "security.protected": "  rutas protegidas tocadas: {paths}",
        "security.none": "  ningún revisor para este disparador",
        "security.reviewer": "  {id}  {name}  -> {run}",
        "security.dropped": "  se omite {id}: {reason}",
        "security.reason.critical": "presupuesto crítico: ningún agente de Claude ahora",
        "security.reason.protected_path": "se toca una ruta protegida, así que el diff no sale del equipo",
        "security.reason.codex_off": "el carril de Codex está apagado o no está instalado",
        "security.queued": "  en cola para más tarde: {ids}",
        "security.foot": "Solo se imprime: no se ha ejecutado nada.",
        "security.logged": "Registrado en {path} con su disparador y sus revisores.",
        "security.bad_trigger": "Disparador desconocido {trigger}. Conocidos: {known}",
        "security.unknown_action": "Acción desconocida {action}. Usa: budgit security plan --trigger T1..T4 --paths ...",
        "hyp.none": "Sin hipótesis.",
        "hyp.head": "Hipótesis: {open} abiertas, {confirmed} confirmadas, {refuted} refutadas, {unclear} sin aclarar (lo que dice un usuario simulado no es un resultado hasta que lo puntúen datos reales)",
        "hyp.evidence": "puntuada el {date}: {text}",
        "hyp.added": "{id} archivada como abierta.",
        "hyp.scored": "{id} queda como {status}.",
        "hyp.imported": "{n} hipótesis archivadas desde el informe: {ids}",
        "hyp.bad_status": "No se puede puntuar como {status}. Usa: {known}",
        "hyp.need_evidence": "Di qué señal real zanjó {id}: --evidence \"...\" (solo 'unclear' no la necesita).",
        "hyp.missing": "No existe la hipótesis {id}.",
        "hyp.unreadable": "No puedo leer {path} ({why}).",
        "hyp.not_a_report": "{path} no es un informe de persona: su primera línea debe empezar por HYPOTHESIS.",
        "hyp.need_both": "Da las dos: --claim \"...\" --confirm-with \"...\"",
        "hyp.need_score_args": "Uso: budgit hypotheses score H-n confirmed|refuted|unclear --evidence \"...\"",
        "hyp.need_report": "Uso: budgit hypotheses import FICHERO_INFORME",
        "hyp.no_project": "Esta carpeta no es un proyecto de Budgit.",
        "hyp.unknown_action": "Acción desconocida {action}. Usa: list, add, score, import",
        "skills.count": "{n} skills, registro {version}",
        "skills.list.head": "  id                                 estado     on   revis.  coste ajuste",
        "skills.show_missing": "No hay ninguna skill con el id '{id}' en el registro.",
        "skills.check.clean": "Skills: nada que avisar.",
        "skills.check.head": "Skills: {n} aviso(s).",
        "skills.check.env": "{id}: falta el ajuste {var}",
        "skills.check.binary_sha256": "{id}: el programa incluido no es el que se revisó",
        "skills.check.drifted": "{id}: sus ficheros cambiaron desde la revisión",
        "skills.check.missing": "{id}: registrada pero no instalada",
        "skills.check.unregistered": "{id}: instalada pero sin entrada en el registro",
        "skills.check.flags_changed": "{id}: hooks, servidores o hosts nuevos: {detail}",
        "skills.load.none": "Plan de carga: nada que cambiar.",
        "skills.load.head": "Plan de carga: {n} skill(s) pasarían a cargarse solo por el nombre; se ahorran unos {chars} caracteres (unos {tokens} tokens) por sesión.",
        "skills.load.block": "Añade esto al bloque skillOverrides de tus ajustes (no se ha escrito nada):",
        "skills.load.foot": "Las skills de plugins no se pueden cambiar así; salen arriba solo para que conste.",
        "skills.build.done": "Registro reconstruido: {n} skill(s) sin leer añadidas. Escrito: {path}",
        "skills.unknown_action": "Acción de skills desconocida '{action}'. Usa list, show, why, check, build o load-plan.",
        "status.doing": "{n} en curso",
        "status.todo": "{n} por hacer",
        "status.idea_one": "{n} idea",
        "status.idea_many": "{n} ideas",
        "status.nothing": "nada pendiente",
        "status.no_project": "\u25cc sin libreta aqu\u00ed",
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
        "book.now_session": "Sesión en curso",
        "book.waiting_permission": "esperando tu permiso",
        "book.waiting_question": "te hace una pregunta",
        "book.waiting_other": "te espera",
        "book.quiet_sessions": "{n} sesiones en reposo",
        "book.project_other": "otro",
        "book.no_live_data": "Sin datos de sesiones en vivo.",
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
        "book.badge_wait_one": "te espera",
        "book.badge_wait_many": "te esperan",
        "book.badge_working": "trabajando",
        "book.badge_resting": "en reposo",
        "book.h_week": "Esta semana",
        "book.week_done": "{n} hechas",
        "book.week_letters": "LMXJVSD",
        "book.h_wait_one": "Te espera",
        "book.h_wait_many": "Te esperan",
        "book.h_working": "Trabajando",
        "book.needs": "necesita {what}",
        "book.needs_permission": "tu permiso",
        "book.needs_question": "una respuesta",
        "book.needs_other": "algo de ti",
        "book.no_item": "sin tarea marcada",
        "book.rest_one": "{n} sesión en reposo",
        "book.rest_paused_one": "{n} tarea suya en pausa",
        "book.rest_paused_many": "{n} tareas suyas en pausa",
        "book.group_next": "{n} por empezar",
        "book.fold_quiet": "nadie trabajando \u00b7 {n} por empezar",
        "book.more_in": "y {n} más en {projects}",
        "book.join_and": "y",
        "book.today": "Hoy",
        "book.yesterday": "Ayer",
        "book.ago_s": "hace {n} s",
        "book.ago_min": "hace {n} min",
        "book.ago_h": "hace {n} h",
        "book.since_at": "desde las {hm}",
        "book.since_yesterday": "desde ayer",
        "book.since_day": "desde el {day}",
        "book.live_chip": "en vivo \u00b7 último cambio {ago}",
        "cmd.still_on": "Brote se queda quieto.",
        "cmd.still_off": "Brote vuelve a moverse.",
        "cmd.no_project": "budgit: no hay proyecto Budgit aquí (ejecuta: budgit activate <carpeta>)",
        "cmd.unavailable": "Brote: no disponible",
        "brote.menu_quit": "Salir de Brote",
        "brote.stuck": "Claude se detuvo por un error",
        "brote.update_title": "\u00bfActualizar Budgit a {v}?",
        "brote.update_body": "Los chats que ya est\u00e1n abiertos siguen con la versi\u00f3n vieja hasta que los reabras.",
        "brote.update_yes": "Actualizar",
        "brote.update_no": "Ahora no",
        "brote.update_done": "Budgit {v} est\u00e1 instalado. Reabre tus chats para usarlo.",
        "brote.update_fail": "La actualizaci\u00f3n no termin\u00f3. Ejecuta esto en una terminal:\n{commands}",
        "brote.update_menu": "Actualizar Budgit a {v}\u2026",
        "book.button": "Libreta",
        "cmd.status_unavailable": "budgit: estado no disponible",
        "ledger.empty": "Todavía no hay nada en el registro. Añade el primer elemento: budgit ledger add --text \"lo que quieres construir\"",
        "cmd.limits_live": "límites del plan (en vivo)",
        "cmd.desc_status": "Muestra el estado de Budgit (registro, presupuesto)",
        "cmd.desc_colour_probe": "Muestra la línea de estado en color una vez (¿esta pantalla dibuja colores?)",
        "cmd.desc_bar_probe": "Muestra qu\u00e9 hace esta pantalla y esta carpeta con la l\u00ednea de Budgit bajo el prompt",
        "cmd.desc_brotewin": "Ventana flotante de Brote: on, off, status, uninstall",
        "cmd.brote_usage": "Usa /budgit-brote on, off, stop, status, uninstall.",
        "cmd.brote_unavailable": "La ventana flotante de Brote necesita macOS y el plugin del marketplace.",
        "cmd.desc_float": "Nombre antiguo de /budgit-brote",
        "cmd.float_usage": "Usa /budgit-brote on, off, stop, status, uninstall.",
        "cmd.float_unavailable": "La ventana flotante de Brote necesita macOS y el plugin del marketplace.",
        "cmd.bar_probe_none": "ninguno (no hay libreta)",
        "cmd.chat_link_layout": "budgit: no pude leer los registros de chats de la app de escritorio (puede haber cambiado su estructura); no se imprime ning\u00fan enlace.",
        "release.head": "Revisi\u00f3n de lanzamiento: {built} hechos, {blocking} sin construir, {deferred} aplazados.",
        "release.built": "Hechos (con su evidencia):",
        "release.blocking": "Sin construir (abiertos o en curso, sin aplazar):",
        "release.deferred": "Aplazados (el usuario los dej\u00f3 fuera de este lanzamiento):",
        "release.hidden": "Aviso: {n} car\u00e1cter(es) oculto(s) en {files} fichero(s) versionado(s). L\u00edstalos con `budgit lint --tracked --hidden`; `budgit lint --tracked --fix` los quita.",
        "release.next": "No est\u00e1 listo: construye cada \u00edtem sin construir, o pide al usuario que lo aplace (`budgit ledger phase later R-n`), y vuelve a ejecutar esto.",
        "release.clear": "Cada \u00edtem est\u00e1 construido o aplazado.",
        "graph.head": "Revisi\u00f3n del grafo (solo aviso, nunca bloquea): {n} hallazgo(s).",
        "graph.unverified": "Hechas, pero sin informe PASS (p\u00eddelo a budgit:verifier):",
        "graph.orphans": "En curso, pero la sesi\u00f3n que la reclam\u00f3 termin\u00f3 o no tiene due\u00f1a:",
        "graph.cites": "Hechas, pero nombran una tarea que sigue sin construir:",
        "graph.unlinked": "Commits sin id de tarea:",
        "graph.commit_open": "Commits que nombran una tarea marcada como abierta (no en curso):",
        "graph.bad_ref": "--since no es una referencia de git v\u00e1lida; no se revisaron los commits.",
        "graph.git_failed": "git no pudo leer ese rango de commits; no se revisaron.",
        "graph.state_dead": "la sesi\u00f3n termin\u00f3",
        "graph.state_unclaimed": "sin due\u00f1a",
        "cmd.desc_migrate": "Pasa esta instalaci\u00f3n al marketplace p\u00fablico (muestra los comandos y solo los ejecuta si dices que s\u00ed)",
        "cmd.migrate_nothing": "Nada que migrar: este plugin no est\u00e1 instalado desde una carpeta en vivo.",
        "cmd.migrate_need_repo": "budgit: indica el repo p\u00fablico con --repo PROPIETARIO/NOMBRE (con la forma propietario/nombre)",
        "cmd.migrate_plan": "Este plugin funciona desde una carpeta en vivo ({old}). Para pasar al marketplace p\u00fablico:\n{commands}\nEl plugin antiguo solo se desactiva, no se borra; tus proyectos y libretas se quedan como est\u00e1n. Para volver: {undo}",
        "cmd.migrate_terminal": "budgit: la migraci\u00f3n necesita que est\u00e9s en una terminal: ejecuta este comando all\u00ed y lee los pasos antes de responder.",
        "cmd.migrate_no_claude": "budgit: no se encontr\u00f3 el comando claude en esta terminal; ejecuta los comandos de arriba a mano.",
        "cmd.migrate_ask": "\u00bfEjecuto estos comandos ahora? [y/N] ",
        "cmd.migrate_declined": "No se ha cambiado nada.",
        "cmd.migrate_failed": "budgit: este paso fall\u00f3: {step}. Tu plugin antiguo sigue activado salvo que el paso de desactivar ya se haya ejecutado. Para volver: {undo}",
        "cmd.migrate_done": "Hecho. Vuelve a abrir tus chats para que carguen el plugin nuevo.",
        "cmd.desc_colour": "Muestra o cambia el color de la l\u00ednea bajo el prompt (on, off, auto)",
        "cmd.colour_on": "activado",
        "cmd.colour_off": "desactivado",
        "cmd.colour_show": "Color de la l\u00ednea: {state}. C\u00e1mbialo con: budgit colour on|off|auto",
        "cmd.colour_set": "Color de la l\u00ednea: {state}.",
        "status.update_reopen": "\u21bb reabre",
        "cmd.desc_update": "C\u00f3mo actualizar Budgit (este comando solo muestra los comandos y no ejecuta nada)",
        "cmd.update_commands": "Para actualizar Budgit, ejecuta estos dos comandos y vuelve a abrir el chat:\n{commands}\nEste comando solo los muestra; no ejecuta nada.",
        "cmd.update_check_new": "Hay una versi\u00f3n nueva de Budgit: {latest} (tienes la {current}).",
        "cmd.update_check_same": "Budgit est\u00e1 al d\u00eda ({current}).",
        "cmd.update_check_failed": "budgit: no se pudo consultar el repositorio p\u00fablico para buscar actualizaciones (\u00bfsin conexi\u00f3n?)",
        "cmd.bar_probe": "Prueba de la barra de Budgit\n  pantalla: {surface}\n  Claude Code: {engine}\n  Budgit: {plugin} {version}\n  carpeta: {folder}\n  proyecto: {project}\n  l\u00ednea puesta: {line}\nSi esa l\u00ednea no sale bajo el cuadro de texto, copia este texto y env\u00edalo.",
        "cmd.colour_probe": "Mira la línea de debajo del prompt: si ves segmentos verde, rosa, lila y azul, esta pantalla dibuja color. Si ves códigos raros o una sola línea gris, no lo dibuja: ejecuta budgit colour off. La línea de siempre vuelve en menos de un minuto.",
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
