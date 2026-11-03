// Budgit mod: informational only. (1) the one-line status under the prompt and /budgit-status, (2) Brote, the pixel-art
// pet above the prompt, and his notebook (/brote). It never denies, rewrites or blocks anything: enforcement stays in
// the Python settings hooks (hooks/hooks.json "hooks"). No network, no files written, no secrets; every handler fails
// soft. A plugin names ONE hooks module, so both live here. $.store holds only Brote's last `status --brote` answer
// per project and the "still" switch. The Client (brote-client.js) draws on the terminal only (R-172: on the desktop Brote lives in the floating window, no band); where AbovePrompt is
// not raised (VS Code, mobile) only the status line and /brote exist.
// Docs: https://code.claude.com/docs/en/plugins/mods/reference (types checked against Claude Code 2.1.299).

// Every word the user reads comes from `budgit status --brote` (`t`, in the user's language; the catalog lives in
// scripts/i18n.py). The English below is only the fallback for a first call that has not answered yet.
let strings = null            // the last answer's `t`
const word = (key, english) => (strings && strings[key]) || english
const fmt = (text, vars) => String(text || '').replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m))

const REFRESH_MS = 60_000
const RUN_TIMEOUT_MS = 5_000
const RELOAD_FIRST_MS = 3_000     // R-353: the first look for a newer Budgit after a chat starts (a fresh copy after a reload shows its button then)
const FLOAT_TIMEOUT_MS = 20_000   // copying the app and starting it
let timer = null

// Runs `budgit <args>` for the session's project and returns trimmed stdout. R-521: null on a failure, a timeout or a non-zero exit,
// and '' only for a real empty answer; a caller that cannot tell them apart treats null as ''.
async function budgit($, cwd, args) {
  try {
    const r = await $.process.run(
      ['python3', $.plugin.root + '/bin/budgit', ...args, '--project', cwd],
      { timeoutMs: RUN_TIMEOUT_MS },
    )
    return r.exitCode === 0 ? String(r.stdout).trim() : null
  } catch {
    return null
  }
}

/* float:begin */
// R-310/R-316: Brote's floating window from the desktop app alone: no Terminal, no `budgit` on a PATH. The user's own typed word picks one
// of a fixed list of arguments for the plugin's CLI (`budgit brote ...`; /budgit-float is the old name and still works).
const FLOAT_ARGS = { '': ['status'], on: ['on'], off: ['off'], stop: ['stop'], status: ['status'], uninstall: ['uninstall'] }
async function floatRun($, arg) {
  const word_ = String(arg || '').trim().toLowerCase()
  const args = Object.prototype.hasOwnProperty.call(FLOAT_ARGS, word_) ? FLOAT_ARGS[word_] : null
  if (!args) return null
  try {
    const r = await $.process.run(['python3', $.plugin.root + '/bin/budgit', 'brote', ...args], { timeoutMs: FLOAT_TIMEOUT_MS })
    return (String(r.stdout || '') + String(r.stderr || '')).trim()
  } catch {
    return ''
  }
}
/* float:end */

// The plan's live 5-hour and weekly windows as JSON ([{kind, percentUsed, resetsAt}]), or '' if unavailable.
async function liveLimits($) {
  try {
    const usage = await $.session.usage()
    return usage && Array.isArray(usage.rateLimits) ? JSON.stringify(usage.rateLimits) : ''
  } catch {
    return ''
  }
}

// R-238: the CLI decides what the line says, in every folder (a project above the folder counts; without one the line shows the
// account part only). The mod never looks at folders itself, so it cannot disagree with the CLI. An empty answer clears the line.
let surface = ''              // from session.start, for the probe
let lastLine = ''             // what the mod last set
let loadedVersion = ''        // R-241: the plugin version this chat loaded, read once at session start; the CLI compares it with the disk

/* colour:begin */
// R-293: the entry set with $.ui.status is plain text on every surface. The host puts its own warning sign and the plugin name in
// front of it and draws no ANSI there, not even in the terminal (an escape comes out as a replacement character). So the mod never
// asks for colour and cuts off any control sequence that still arrives. Colour belongs to a real `statusLine` command
// (`budgit colour`), which the user sets up in their own settings.
function colourMode() {
  return 'off'
}
function forSurface(line) {
  return String(line || '')
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g, '')
    .replace(/(?:\u001b\[|\u009b)[0-9;?]*[ -\/]*[@-~]/g, '')
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, '')
}
/* colour:end */

function withLoaded(args) {
  return loadedVersion ? [...args, '--loaded', loadedVersion] : args
}

async function readLoadedVersion($) {
  try {
    const text = await $.fs.read($.plugin.root + '/.claude-plugin/plugin.json')
    const v = String(JSON.parse(String(text)).version || '')
    loadedVersion = /^\d{1,5}(\.\d{1,5}){0,3}$/.test(v) ? v : ''
  } catch {
    loadedVersion = ''       // no hint rather than a wrong one
  }
}

/* stuck:begin */
// A turn that ended on an API error (a limit, the account, a busy server) leaves one plain line under the prompt until the next
// message, whatever it is. The mod cannot tell which error it was: the floating Brote carries the kind.
let stuck = false
let turnRunning = false       // R-353: the main loop has a turn in flight (turn.start to turn.complete); the plugin is never reloaded under it
function registerStuck(on) {
  on('turn.start', async ($, e, next) => {
    if (!e.agentId) turnRunning = true
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    try {
      if (!e.agentId) turnRunning = false
      if (e.reason === 'error' && !e.agentId) {   // the main loop only: a subagent that fails is not this chat stopping
        stuck = true
        await showLine($, word('brote.stuck', 'Claude stopped with an error'), false)   // no trace here: this hook is awaited by the turn; the next refresh traces it
      }
    } catch {
      // Fail soft.
    }
    return next(e)
  })
  on('prompt.submit', async ($, e, next) => {
    try {
      if (stuck) {
        stuck = false
        await refresh($)
      }
    } catch {
      // Fail soft.
    }
    return next(e)
  })
}
/* stuck:end */

/* trace:begin */
// R-353: a tiny trace of what the bar decided, written by the CLI (brotetrace.py) only when the decision changes: the surfaces the host
// listed, whether the band drew and the footer was set, and the footer's length. Never the line's text, a path or a project.
let footerAtDraw = null       // the footer entry's state the last time the band drew
let traceSeen = ''
async function traceLine($, listed, desktop, drawn) {
  const yes = v => (v ? 'yes' : 'no')
  const surfaces = Array.isArray(listed) ? listed.join(',') || '-' : '-'
  const footerLen = footerShown ? String(lastLine.length) : '0'
  const tailLen = String(hintTail.length)
  const key = [surfaces, yes(desktop), yes(drawn), footerLen === '0' ? '0' : 'n', tailLen === '0' ? '0' : 'n', footerAtDraw === null ? '' : yes(footerAtDraw)].join('|')
  if (key === traceSeen) return
  traceSeen = key
  const args = ['trace', '--sid', await $.session.id(), '--surfaces', surfaces, '--band', yes(desktop), '--drawn', yes(drawn), '--footer', footerLen, '--tail', tailLen, '--version', loadedVersion || '-']
  if (footerAtDraw !== null) args.push('--footer-at-draw', yes(footerAtDraw))
  await budgit($, await $.session.cwd(), args)
}
/* trace:end */

/* reload:begin */
// R-353: a chat that loaded a Budgit older than the installed one reloads the plugin by itself, once per version, when no turn is running.
// The CLI says whether the chat is stale and in which words (update --state); the mod only runs the built-in /reload-plugins as the person
// would. What it asked for is kept per chat in the store, because a reload gives the plugin a fresh copy with nothing in memory: the copy
// that comes back sees the version it asked for loaded and says so once; one that still finds the chat stale does not ask again, it offers
// the button (reloadNotice) instead. Never from a hook the turn waits on: the call is made from the clock only.
const RELOADS = 'reloads'
let interactive = false       // a person is at the prompt (the desktop tab says so by listing the desktop surface instead): a -p run has no /reload-plugins to run
let reloadNotice = null       // {text, label} once the automatic way did not work

async function updateState($) {
  if (!loadedVersion) return null
  try {
    const s = JSON.parse((await budgit($, await $.session.cwd(), ['update', '--state', '--loaded', loadedVersion])) || 'null')
    return s && s.kind === 'stale-chat' && /^\d{1,5}(\.\d{1,5}){0,3}$/.test(String(s.available)) ? s : null
  } catch {
    return null
  }
}

async function reloadLog($) {
  try {
    const v = await $.store.get(RELOADS)
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {}
  } catch {
    return {}
  }
}

async function reloadSave($, log) {      // the newest 20 chats only
  const ids = Object.keys(log)
  for (const id of ids.slice(0, Math.max(0, ids.length - 20))) delete log[id]
  try { await $.store.set(RELOADS, log) } catch { /* the next chat starts a fresh record */ }
}

// A refused reload leaves the record marked, so a later start of this chat on the newer version (a resume) does not claim an update it did not make.
async function markFailed($) {
  try {
    const id = await $.session.id()
    const log = await reloadLog($)
    if (log[id] && !log[id].failed) {
      log[id] = { ...log[id], failed: true }
      await reloadSave($, log)
    }
  } catch {
    // Fail soft.
  }
}

// R-401: the "plugin updated" line is a row in the band over the prompt for half a minute, not a $.ui.log line: the host puts the plugin name
// before every log, toast and status text, and draws none before what the plugin draws itself.
let updatedNote = ''
let noteSeq = 0               // each note has its own timer: an older timer never clears a newer note, even one with the same words
const NOTE_MS = 30_000
function announce($, text) {
  updatedNote = text
  const mine = ++noteSeq
  try {
    $.ui.invalidate('ui.render')
    $.clock.after(NOTE_MS, () => {
      if (noteSeq !== mine) return
      updatedNote = ''
      $.ui.invalidate('ui.render')
    })
  } catch { /* the row stays until the next redraw */ }
}

function offerReload($, s, why) {
  reloadNotice = { text: String(s.notice || ''), label: String(s.button || '') }
  try { $.ui.log('budgit: no automatic reload (' + why + ')', { to: 'debug' }) } catch { /* debug only */ }
  $.ui.invalidate('ui.render')
}

async function forgetReload($) {
  const id = await $.session.id()
  const log = await reloadLog($)
  delete log[id]
  await reloadSave($, log)
}

// The reload itself. A copy that is replaced by it never gets past the await; one that does (refused, or nothing was replaced) looks again.
async function runReload($, s) {
  let why = 'the version loaded did not change'
  try {
    await $.command.run({ command: 'reload-plugins' })
  } catch (x) {
    why = 'refused: ' + String((x && x.message) || x).slice(0, 160)
  }
  await readLoadedVersion($)
  if (loadedVersion === s.available) {
    reloadNotice = null
    await forgetReload($)
    announce($, String(s.said))
  } else {
    await markFailed($)
    offerReload($, s, why)
  }
}

async function checkReload($) {
  try {
    if (!interactive && (await drawsOnDesktop($)) !== true) return   // the desktop tab runs through the SDK: isInteractive is false there
    const s = await updateState($)
    if (!s) {
      reloadNotice = null
      return
    }
    const id = await $.session.id()
    const log = await reloadLog($)
    if (log[id] && log[id].v === s.available) {      // asked for this version already: once per version, the button is the other way
      if (!reloadNotice) {
        await markFailed($)
        offerReload($, s, 'asked before for ' + s.available)
      }
      return
    }
    if (turnRunning) return                          // next tick
    delete log[id]
    log[id] = { v: s.available, said: String(s.said) }
    await reloadSave($, log)                         // before the call: a copy lost in the reload cannot ask twice
    if (turnRunning) {                               // a turn began while the record was written: not under it
      await forgetReload($)
      return
    }
    await runReload($, s)
  } catch {
    // Fail soft.
  }
}

// A fresh copy, at session start: the version this chat asked for is the one loaded now, so say so, once.
async function announceReloaded($) {
  try {
    if (!loadedVersion) return
    const asked = (await reloadLog($))[await $.session.id()]
    if (asked && asked.v === loadedVersion) {
      await forgetReload($)
      if (asked.said && !asked.failed) announce($, String(asked.said))
    }
  } catch {
    // Fail soft.
  }
}

// The button for when it did not work: the same call, pressed by the person.
async function reloadNow($) {
  try {
    reloadNotice = null
    $.ui.invalidate('ui.render')
    await $.command.run({ command: 'reload-plugins' })
  } catch {
    // The built-in prints its own refusal.
  }
}
/* reload:end */

/* line:begin */
// R-340: the desktop footer cuts the line at about 50 characters, so there the line is a wrapping band above the prompt (AbovePrompt
// below) and the footer copy is cleared so it is not shown twice. Which surface the session draws on is asked each time: the surface
// that session.start names is only the first one at start, and the desktop attaches after it (session.attach) or is missing from
// it (a /reload-plugins, a new chat), so a value kept from session.start left the cut footer in place. Every other surface keeps the footer.
let surfacesListed = null     // what the host listed at the last ask (only for the trace); null when it could not say
async function drawsOnDesktop($) {   // true or false as the host says; null when it could not say
  try {
    const all = await $.session.surfaces()
    surfacesListed = Array.isArray(all) ? all.map(String) : null
    return Array.isArray(all) && all.includes('desktop')
  } catch {
    surfacesListed = null
    return null
  }
}

// R-369: never both at once. Where the band is drawn the footer prints nothing: the host's surfaces say so, and so does the band itself
// when it is asked to draw (bandDrawn, since the last refresh) even if the surfaces disagree, and the footer entry is taken back at once
// if it was set. When the host cannot say, the last thing known decides (bandSeen, or what session.start named).
let footerShown = false       // the footer entry holds the line right now
// The room beside the hint is short (about 37 columns at 80), and the engine cuts the tail where the row ends: the budget part first, then the counts.
function tailOf(line) {
  const text = String(line || '')
  const m = /^budgit {2}(.*?) \u250a (.+)$/.exec(text)
  return m ? 'budgit ' + m[2] + ' \u250a ' + m[1] : text
}
let hintTail = ''             // R-401: the line at the end of the hint line under the prompt (terminal only)
function setTail($, text) {
  if (text === hintTail) return
  hintTail = text
  $.ui.invalidate('ui.render')
}
let bandDrawn = false         // the desktop band was asked to draw since the last refresh
let bandSeen = false          // what the last refresh (or a detach) concluded: the band is on the screen
// R-405: the host's list of surfaces and the band's own render can disagree (the list leaves 'desktop' out while AbovePrompt is drawn for it).
// A band that drew on the desktop is ground truth: for BAND_MEMORY_TICKS periodic refreshes after its last draw the line is the band's and
// nothing else's, whatever the list says (before this the footer or the hint tail came back at the first refresh the band had not drawn
// since, and the line showed twice). The memory runs out by itself, so a desktop that left without a detach does give the line back.
// The band reads BEAT while it draws (a read while drawing subscribes, as the terminal path relies on) and every desktop refresh writes a new
// value, so the host redraws the band at each refresh even when the line's text has not changed: the memory below renews itself every tick.
const BEAT = 'budgit:beat'
let tick = 0                  // periodic refreshes so far
let bandAt = -1               // the tick at which the desktop band last drew (-1: never, or detached)
const BAND_MEMORY_TICKS = 2
async function showLine($, line, traced = true) {
  lastLine = line
  const listed = await drawsOnDesktop($)
  const drawn = bandDrawn
  bandDrawn = false
  const recent = bandAt >= 0 && tick - bandAt <= BAND_MEMORY_TICKS
  const desktop = listed === null ? bandSeen || surface === 'desktop' : listed || drawn || recent
  if (listed !== null) bandSeen = desktop
  // R-401: the host prints "⚠ <plugin name>:" before every $.ui.status entry. On the terminal the line goes at the end of the hint
  // line under the prompt instead (PromptHint's `tail`: the plugin draws it, no name and no sign before it). Every surface that draws
  // no such tail (vscode, mobile, a host that cannot say) keeps the footer entry.
  const terminal = !desktop && (Array.isArray(surfacesListed) ? surfacesListed.includes('terminal') : surface === 'terminal')
  setTail($, terminal ? tailOf(line) : '')
  if (desktop) {
    footerShown = false
    $.ui.status(undefined)
    if (traced) try { await $.store.set(BEAT, tick + ':' + Math.random().toString(36).slice(2, 8)) } catch { /* the invalidate below still asks */ }
    $.ui.invalidate('ui.render')
  } else if (terminal) {
    footerShown = false
    $.ui.status(undefined)
  } else {
    footerShown = !!line
    $.ui.status(line || undefined)
  }
  if (traced) try { await traceLine($, surfacesListed, desktop, drawn) } catch { /* the trace never disturbs the line */ }
}
/* line:end */

let refreshing = null         // R-521: the CLI run in flight (a promise), or null
let goodLine = ''             // R-521: the last line the CLI really answered
let goodTick = -1             // the periodic tick it came at; a line 10 ticks (10 minutes) old is shown marked
const STALE_TICKS = 10

async function refresh($) {
  try {
    if (stuck) {
      await showLine($, word('brote.stuck', 'Claude stopped with an error'))
      return
    }
    const cwd = await $.session.cwd()
    const limits = await liveLimits($)
    // R-521: one run at a time; a slow CLI under load is not asked again on top of itself, a second caller waits for the same answer
    if (!refreshing) {
      refreshing = budgit($, cwd, withLoaded(limits ? ['status', '--line', '--colour', colourMode(), '--limits-json', limits] : ['status', '--line', '--colour', colourMode()]))
        .finally(() => { refreshing = null })
    }
    const answer = await refreshing
    if (answer === null) {      // R-521: a failed or timed-out run says nothing: the last good line stays, marked once it is old
      if (!goodLine) await showLine($, '')      // nothing was ever answered: the old clear stays
      else await showLine($, goodTick >= 0 && tick - goodTick >= STALE_TICKS ? goodLine + ' · ' + fmt(word('brote.line_old', 'older than {min} min'), { min: (STALE_TICKS * REFRESH_MS) / 60_000 }) : goodLine)
      return
    }
    const line = forSurface(answer)
    if (line) { goodLine = line; goodTick = tick } else { goodLine = ''; goodTick = -1 }   // a real empty answer clears (no project here)
    await showLine($, line)
  } catch {
    // Show nothing rather than disturb the session.
  }
}

async function startLine($) {
  if (timer) timer.cancel()
  timer = $.clock.every(REFRESH_MS, async () => {
    tick++
    await refresh($)
    await checkReload($)
  })
  await refresh($)
  // Each in its own try: a refused name throws, and a throw must not skip the rest.
  const commands = [
    ['budgit-status', word('cmd.desc_status', 'Show the Budgit status (ledger, budget)')],
    ['budgit-colour-probe', word('cmd.desc_colour_probe', 'Show the status line in colour once (does this screen draw colours?)')],
    ['budgit-bar-probe', word('cmd.desc_bar_probe', 'Show what this screen and folder do with the Budgit line under the prompt')],
    ['budgit-brote', word('cmd.desc_brotewin', "Brote's floating window: /budgit-brote on, off, status or uninstall")],
    ['budgit-float', word('cmd.desc_float', "Old name of /budgit-brote")],
  ]
  for (const [name, description] of commands) {
    try {
      await $.command.register({ name, description })
    } catch {
      // The probes are optional.
    }
  }
}

function registerLine(on) {
  // R-206: a one-shot probe. The line is drawn once with ANSI colours (`status --line --ansi`); the next 60 s refresh puts the
  // plain one back. It shows whether this surface draws colour in `$.ui.status` (unknown on the desktop Code tab).
  on('command.run', { command: 'budgit-colour-probe' }, async ($) => {
    try {
      const cwd = await $.session.cwd()
      const line = (await budgit($, cwd, ['status', '--line', '--ansi'])) || ''
      if (!line) return { text: word('cmd.no_project', 'budgit: no Budgit project here (run: budgit activate <dir>)') }
      $.ui.status(line)   // the probe shows the codes on purpose, on any surface
      return { text: word('cmd.colour_probe', 'Look at the line under the prompt: green, pink, lilac and blue segments mean this screen draws colour; odd codes or one grey line mean it does not. The plain line comes back within a minute.') }
    } catch {
      return { text: word('cmd.status_unavailable', 'budgit: status unavailable') }
    }
  })

  // R-238: what this surface and folder do with the line, as text every surface can show (the line itself may not be painted).
  on('command.run', { command: 'budgit-bar-probe' }, async ($) => {
    try {
      const cwd = await $.session.cwd()
      let engine = ''
      try {
        engine = String((await $.session.version()).version || '')
      } catch {
        // The engine version is optional.
      }
      const text = (await budgit($, cwd, ['status', '--probe', '--surface', surface, '--engine', engine, '--shown', lastLine])) || ''
      return { text: text || word('cmd.status_unavailable', 'budgit: status unavailable') }
    } catch {
      return { text: word('cmd.status_unavailable', 'budgit: status unavailable') }
    }
  })

  // R-310/R-316: the floating window from the desktop app. No Claude turn, no Terminal. /budgit-float is the old name of /budgit-brote.
  for (const name of ['budgit-brote', 'budgit-float']) {
    on('command.run', { command: name }, async ($, e) => {
      try {
        const out = await floatRun($, e.args)
        if (out === null) return { text: word('cmd.brote_usage', 'Use /budgit-brote on, off, stop, status or uninstall.') }
        return { text: out || word('cmd.brote_unavailable', "Brote's floating window: unavailable here (macOS only; needs the plugin installed from the marketplace).") }
      } catch {
        return { text: word('cmd.brote_unavailable', "Brote's floating window: unavailable here (macOS only; needs the plugin installed from the marketplace).") }
      }
    })
  }

  // Full `budgit status` as the command's text, without a Claude turn.
  on('command.run', { command: 'budgit-status' }, async ($) => {
    try {
      const cwd = await $.session.cwd()
      const text = (await budgit($, cwd, ['status'])) || ''
      const limits = await liveLimits($)
      if (!text) return { text: word('cmd.no_project', 'budgit: no Budgit project here (run: budgit activate <dir>)') }
      // The raw plan windows help check the status line's numbers against the app's usage card.
      return { text: limits ? text + '\n' + word('cmd.limits_live', 'plan limits (live)') + ': ' + limits : text }
    } catch {
      return { text: word('cmd.status_unavailable', 'budgit: status unavailable') }
    }
  })
}

const PANE = 'brote-notebook'
const KEY = 'data:'            // + cwd: the last `status --brote` answer of that project
const MIN_ROWS = 15            // the Client's height (14 rows of pet + his line); below that the band draws the one-line text form
let broteTimer = null
let failed = ''                // a Client fault: draw the text form until the plugin reloads

const dayOf = (epoch, days) => days[new Date(epoch * 1000).getDay()]

// What Brote says, one line (the approved v4 phrases, now from the catalog). Without the catalog's strings: just his name.
function say(d) {
  const t = d.t
  if (!t || !Array.isArray(t.days)) return 'Brote'
  const resetDay = d.resets_at != null ? dayOf(d.resets_at, t.days) : t.days[1]   // the plan's own weekly reset, not always Monday
  const outDay = d.runs_out != null ? dayOf(d.runs_out, t.days) : resetDay
  const hourOf = epoch => new Date(epoch * 1000).getHours()
  const part = d.runs_out != null ? t['brote.part.' + (hourOf(d.runs_out) >= 21 || hourOf(d.runs_out) < 6 ? 'night' : hourOf(d.runs_out) < 12 ? 'morning' : 'afternoon')] : ''
  const vars = { reset: resetDay, out: outDay, outpart: part || '', resethour: d.resets_at != null ? hourOf(d.resets_at) : 0, used: Math.round(d.used || 0) }
  const key = 'brote.say.' + d.state
  const second = d.state === 'contento' && d.projection == null ? key + '.b_start' : d.state === 'enfermo' && d.runs_out == null ? key + '.b_near' : key + '.b'
  const a = t[key + '.a'] === undefined ? 'Brote' : fmt(t[key + '.a'], vars)
  const b = t[key + '.a'] === undefined ? '' : fmt(t[second] === undefined ? t[key + '.b'] : t[second], vars)
  const five = d.h5 >= 70 && d.state !== 'dormido' ? fmt(t['brote.suffix.five'], { h5: d.h5 }) : ''
  const n = d.stale ? d.stale.length : 0
  const stale = n ? fmt(t[n === 1 ? 'brote.suffix.stale_one' : 'brote.suffix.stale_many'], { n }) : ''
  return `${a} · ${b}${five}${stale}${d.old ? fmt(t['brote.suffix.old']) : ''}`
}

async function refreshBrote($) {
  try {
    const cwd = await $.session.cwd()
    const limits = await liveLimits($)
    const raw = await budgit($, cwd, withLoaded(limits ? ['status', '--brote', '--limits-json', limits] : ['status', '--brote']))
    if (raw === null) return     // R-521: the run failed or timed out: keep what the last good answer stored
    if (!raw) {                  // no project here (the CLI walks up to find one): nothing to show
      await $.store.delete(KEY + cwd)
      return
    }
    const data = JSON.parse(raw)
    if (data.t) strings = data.t
    const prev = await $.store.get(KEY + cwd)
    if (data.state === 'unknown' && prev && prev.state && prev.state !== 'unknown') {
      // no fresh quota data: keep showing the last known state, marked as old, instead of dropping to a neutral face
      Object.assign(data, { state: prev.state, used: prev.used, projection: prev.projection, runs_out: prev.runs_out, resets_at: prev.resets_at, old: true })
    }
    const done = (data.counts && data.counts.done) || 0
    // a closed item (done count went up) is a celebration; the Client reacts when this number changes
    data.celebrate = ((prev && prev.celebrate) || 0) + (prev && done > ((prev.counts && prev.counts.done) || 0) ? 1 : 0)
    await $.store.set(KEY + cwd, data)
  } catch {
    // Show nothing rather than disturb the session.
  }
}

const bar = (used, proj) => {
  const cells = 28, u = Math.round((Math.min(100, used) / 100) * cells), p = Math.round((Math.min(100, proj == null ? used : proj) / 100) * cells)
  return '█'.repeat(u) + '▒'.repeat(Math.max(0, p - u)) + '░'.repeat(Math.max(0, cells - Math.max(u, p)))
}

function notebook(d) {
  const t = d.t || {}
  const lines = []
  const week = d.used == null ? t['book.week_none'] : fmt(t['book.week'], { used: Math.round(d.used) }) + (d.projection != null ? fmt(t['book.week_projection'], { proj: Math.round(d.projection) }) : t['book.week_early'])
  const sect = (title, meaning, items, extra) => ({ title, meaning, items, extra })
  const doing = (d.doing || []).slice().sort((a, b) => (d.stale.includes(b.id) ? 1 : 0) - (d.stale.includes(a.id) ? 1 : 0))
  const mark = i => (d.stale.includes(i.id) ? t['book.stalled'] : i.state === 'idle' ? t['book.paused'] : '')
  const more = (total, shown) => (total > shown.length ? fmt(t['book.more'], { n: total - shown.length }) : '')
  lines.push(sect(t['book.now'], t['book.now_meaning'], doing.map(i => `${i.id} ${i.text}${mark(i)}`), ''))
  lines.push(sect(t['book.queue'], t['book.queue_meaning'], (d.queue || []).map(i => `${i.id} ${i.text}`), more(d.counts.next, d.queue || [])))
  lines.push(sect(t['book.later'], t['book.later_meaning'], (d.later || []).map(i => `${i.id} ${i.text}`), more(d.counts.later, d.later || [])))
  return { week, sections: lines }
}

async function startBrote($) {
  if (broteTimer) broteTimer.cancel()
  broteTimer = $.clock.every(REFRESH_MS, () => refreshBrote($))
  await refreshBrote($)
  const cwd = await $.session.cwd()
  if (await $.store.get(KEY + cwd)) {      // the CLI found a project for this folder
    await $.command.register({ name: 'brote', description: word('cmd.desc_brote', 'Open Brote\'s notebook (/brote still stops his animation)') })
  }
}

// The band above the prompt as it was before R-353 (the reload row is added around it).
async function bandBody($, e, next) {
  if (e.props.hasSurvey) return next(e)
  if (e.surface === 'desktop') {
    // R-172: on the desktop Brote lives in the floating window (R-162): no art, no button. R-319: only the data line, wrapping, in one Text
    if (!lastLine) return next(e)
    try { await $.store.get(BEAT) } catch { /* only the subscription matters */ }
    bandDrawn = true
    bandSeen = true
    bandAt = tick
    footerAtDraw = footerShown || !!hintTail       // what the footer held when the band drew (the trace tells a real double from a corrected one)
    if (footerShown) {   // R-369: drawn here, so not in the footer too
      footerShown = false
      $.ui.status(undefined)
    }
    if (hintTail) setTail($, '')
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text dimColor wrap="wrap">{lastLine}</Text>
      </Box>
    )
  }
  const d = await $.store.get(KEY + (await $.session.cwd()))   // a get while drawing subscribes: a refresh redraws
  if (!d) return next(e)
  const { Box, Text, Button, Client } = $.ui.resolve(e)
  const line = say(d)
  if (!Client || failed || (e.props.maxRows || 0) < MIN_ROWS) {
    return (
      <Box>
        <Text dimColor>{line} </Text>
        <Button key="brote-open" label={(d.t && d.t['book.button']) || 'Notebook'} onPress={() => $.ui.open({ id: PANE, title: 'Brote' })} />
      </Box>
    )
  }
  const still = !!(await $.store.get('still'))
  return (
    <Client key="brote" module="./brote-client.js" width="100%" height={15}
      props={{ state: d.state, h5: d.h5 || 0, stale: (d.stale || []).length, celebrate: d.celebrate || 0, say: line, still }} />
  )
}

function registerBrote(on) {
  on('command.run', { command: 'brote' }, async ($, e) => {
    try {
      if (String(e.args || '').trim() === 'still') {
        const still = !(await $.store.get('still'))
        await $.store.set('still', still)
        return { text: still ? word('cmd.still_on', 'Brote stays still.') : word('cmd.still_off', 'Brote moves again.') }
      }
      await refreshBrote($)
      await $.ui.open({ id: PANE, title: 'Brote' })
      const d = await $.store.get(KEY + (await $.session.cwd()))
      return { text: d ? say(d) : word('cmd.no_project', 'budgit: no Budgit project here (run: budgit activate <dir>)') }
    } catch {
      return { text: word('cmd.unavailable', 'Brote: unavailable') }
    }
  })

  // a click on the pet in the Client posts here
  on('ui.message', async ($, e, next) => {
    try {
      if (e.data && e.data.book === 'toggle') await $.ui.open({ id: PANE, title: 'Brote' })
    } catch {
      // Fail soft.
    }
    return next(e)
  })

  on('ui.fault', ($, e, next) => {
    failed = e.reason
    $.ui.invalidate('ui.render')
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const d = await $.store.get(KEY + (await $.session.cwd()))
    if (!d) return <Text dimColor>{word('book.no_data', 'No data yet.')}</Text>
    const nb = notebook(d)
    return (
      <Box flexDirection="column">
        <Text>{say(d)}</Text>
        <Text dimColor>{nb.week}</Text>
        <Text>{d.used == null ? '' : bar(d.used, d.projection)}</Text>
        {nb.sections.map(s => (
          <Box key={s.title} flexDirection="column" marginTop={1}>
            <Text bold>{s.title} <Text dimColor>({s.meaning})</Text></Text>
            {s.items.length === 0 && <Text dimColor>{word('book.nothing', 'nothing')}</Text>}
            {s.items.map(t => <Text key={t}>{t}</Text>)}
            {s.extra && <Text dimColor>{s.extra}</Text>}
          </Box>
        ))}
      </Box>
    )
  })

  // R-401: the data line at the end of the hint line under the prompt (terminal), with no plugin name and no warning sign before it
  on('ui.render', { component: 'PromptHint' }, async ($, e, next) => {
    if (!hintTail || e.surface === 'desktop') return next(e)
    return next({ ...e, props: { ...e.props, tail: '  ' + hintTail } })
  })

  // R-353: when the automatic reload did not work, one more row under the band: why, and the button that does it by hand
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const body = await bandBody($, e, next)
    if ((!reloadNotice && !updatedNote) || e.props.hasSurvey) return body
    const { Box, Text, Button } = $.ui.resolve(e)
    return (
      <Box key="band" flexDirection="column">
        <Box key="body">{body}</Box>
        {updatedNote && <Box key="updated"><Text dimColor wrap="wrap">{updatedNote}</Text></Box>}
        {reloadNotice && (
          <Box key="reload">
            <Text dimColor wrap="wrap">{reloadNotice.text} </Text>
            <Button key="budgit-reload" label={reloadNotice.label} onPress={() => reloadNow($)} />
          </Box>
        )}
      </Box>
    )
  })
}

// R-340: the desktop joins after session.start (and leaves again): draw the line for the surface that is there now.
async function onSurfaceAttach($, e, next) {
  try {
    if (e.surface === 'desktop') await refresh($)
  } catch {
    // Fail soft.
  }
  return next(e)
}

async function onSurfaceDetach($, e, next) {
  try {
    if (e.surface === 'desktop') {
      bandSeen = false
      bandDrawn = false
      bandAt = -1
      await refresh($)
    }
  } catch {
    // Fail soft.
  }
  return next(e)
}

export function register(on) {
  // one hook per event: a plugin may not register the same event twice without a matcher
  on('session.start', async ($, e, next) => {
    surface = String(e.surface || '')
    interactive = !!e.isInteractive
    await readLoadedVersion($)
    await announceReloaded($)
    try {
      await startBrote($)   // first: its answer carries the words (language) the line's command description uses
    } catch {
      // Fail soft: the session starts either way.
    }
    try {
      await startLine($)
    } catch {
      // Fail soft.
    }
    $.clock.after(RELOAD_FIRST_MS, () => checkReload($))   // not here: the turn waits on this hook
    return next(e)
  })

  on('session.attach', onSurfaceAttach)
  on('session.detach', onSurfaceDetach)

  // After each turn, and when a plan limit's percent used changes.
  on('session.measure', async ($, e, next) => {
    try {
      await refresh($)
      await refreshBrote($)
    } catch {
      // Fail soft.
    }
    return next(e)
  })

  registerLine(on)
  registerBrote(on)
  registerStuck(on)
}
