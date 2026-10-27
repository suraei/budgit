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
const FLOAT_TIMEOUT_MS = 20_000   // copying the app and starting it
let timer = null

// Runs `budgit <args>` for the session's project and returns trimmed stdout, or '' on any problem.
async function budgit($, cwd, args) {
  try {
    const r = await $.process.run(
      ['python3', $.plugin.root + '/bin/budgit', ...args, '--project', cwd],
      { timeoutMs: RUN_TIMEOUT_MS },
    )
    return r.exitCode === 0 ? String(r.stdout).trim() : ''
  } catch {
    return ''
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
function registerStuck(on) {
  on('turn.complete', async ($, e, next) => {
    try {
      if (e.reason === 'error' && !e.agentId) {   // the main loop only: a subagent that fails is not this chat stopping
        stuck = true
        $.ui.status(word('brote.stuck', 'Claude stopped with an error'))
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

async function refresh($) {
  try {
    if (stuck) {
      lastLine = word('brote.stuck', 'Claude stopped with an error')
      $.ui.status(lastLine)
      return
    }
    const cwd = await $.session.cwd()
    const limits = await liveLimits($)
    const line = forSurface(await budgit($, cwd, withLoaded(limits ? ['status', '--line', '--colour', colourMode(), '--limits-json', limits] : ['status', '--line', '--colour', colourMode()])))
    lastLine = line
    $.ui.status(line || undefined)
  } catch {
    // Show nothing rather than disturb the session.
  }
}

async function startLine($) {
  if (timer) timer.cancel()
  timer = $.clock.every(REFRESH_MS, () => refresh($))
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
      const line = await budgit($, cwd, ['status', '--line', '--ansi'])
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
      const text = await budgit($, cwd, ['status', '--probe', '--surface', surface, '--engine', engine, '--shown', lastLine])
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
      const text = await budgit($, cwd, ['status'])
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
  const vars = { reset: resetDay, out: outDay }
  const key = 'brote.say.' + d.state
  const a = t[key + '.a'] === undefined ? 'Brote' : fmt(t[key + '.a'], vars)
  const b = t[key + '.a'] === undefined ? '' : fmt(t[d.state === 'contento' && d.projection == null ? key + '.b_start' : key + '.b'], vars)
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

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    if (e.surface === 'desktop') return next(e)   // R-172: on the desktop Brote lives in the floating window (R-162); no band
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
  })
}

export function register(on) {
  // one hook per event: a plugin may not register the same event twice without a matcher
  on('session.start', async ($, e, next) => {
    surface = String(e.surface || '')
    await readLoadedVersion($)
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
    return next(e)
  })

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
