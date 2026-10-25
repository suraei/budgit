/* director.js: what Brote does when Claude does something. Pure logic, no DOM, no files, no clock of its own:
   the app calls tick() at 10 fps and feed() with the lines the plugin's hook channel wrote (events.log), then asks spec() for the picture.
   Runs in JavaScriptCore after sprites.js and personality.js (the animation bank CAT, the poses and the extra sprites) and in node for the unit tests.
   Line grammar (written by plugin/hooks/brote-event.sh, nothing else): `<kind> <session> [<tool>]`
     prompt  you sent a message (a turn opens)        pre   a tool is about to run      post  it finished       fail  it failed
     stop    Claude ended the turn                    perm  Claude waits for a permission
   Priorities, highest first: a poke sequence (agotado only) > a call (turn finished / question / permission) > the work mode > free time.
   Being held, an act of the pet itself (hello, poke, grumpy, land, dizzy) or the dormido state always win: spec() answers null then. */
var DIRECTOR = (function () {
  'use strict'
  const TICK = 100                                     // ms per tick (10 fps)
  const KINDS = { prompt: 1, pre: 1, post: 1, fail: 1, stop: 1, perm: 1 }
  const LINE = /^(prompt|pre|post|fail|stop|perm) ([A-Za-z0-9_-]{1,24})(?: ([A-Za-z0-9_]{1,48}))?$/

  /* tool -> mode of the little computer (the screen follows the last tool) */
  const MODE = {
    Edit: 'code', Write: 'code', MultiEdit: 'code', NotebookEdit: 'code',
    Read: 'read', NotebookRead: 'read',
    Grep: 'search', Glob: 'search', WebSearch: 'search', WebFetch: 'search', ToolSearch: 'search', LS: 'search',
    Bash: 'bash', BashOutput: 'bash', KillShell: 'bash', PowerShell: 'bash',
    Agent: 'think', Task: 'think', TodoWrite: 'think', Skill: 'think', ExitPlanMode: 'think',
  }
  function modeOf(tool) {
    if (!tool) return 'think'
    if (Object.prototype.hasOwnProperty.call(MODE, tool)) return MODE[tool]
    return /^mcp__/.test(tool) ? 'search' : 'think'
  }
  /* mode -> clip, and the stretch of the clip that repeats while the mode lasts: [from, to) in ticks of the clip */
  const WORK = {
    code:   { id: 'pc_type',   loop: [0, 48] },
    read:   { id: 'pc_read',   loop: [0, 40] },
    search: { id: 'pc_search', loop: [0, 64] },
    bash:   { id: 'pc_run',    loop: [10, 42] },
    think:  { id: 'pc_think',  loop: [0, 32] },
  }
  const NO_BROWS = /^bw[LR]$/                           // the eyebrows are gone everywhere: drop them if an older bank still draws them

  function parseLine(text) {
    const m = LINE.exec(String(text).trim())
    return m ? { k: m[1], sid: m[2], tool: m[3] || '' } : null
  }

  const AMBIENT_EVERY = { quieto: 0, sutil: 600, vivo: 200 }         // ticks between two free-time things (60 s / 20 s)
  const AMBIENT_POOL = [['am_rope', 3], ['am_cookies', 3], ['am_tea', 3], ['am_code', 2], ['am_pc', 2], ['am_bed', 1]]
  const AMBIENT_STATES = { radiante: 1, contento: 1, justo: 1, cansado: 1 }
  const POKE_WINDOW = 80, FLEES_PER_HOUR = 3, CALM_MAX = 200
  const STEP2 = 1200, STEP3 = 6000, NUDGES_PER_DAY = 20, SNOOZE = 36000, THINK_AFTER = 80, WAIT_AFTER = 3000
  const STALE_QUIET = 4200, STALE_RUNNING = 9000                      // a turn that went silent (Esc sends no Stop) closes after 7 min (long enough for the 5-minute clock), or 15 min with a tool still running

  class Director {
    constructor(opts) {
      opts = opts || {}
      this.rng = opts.rng || Math.random
      this.day = opts.day || (() => Math.floor(Date.now() / 86400000))
      this.level = opts.ambient || 'vivo'
      this.t = 0
      this.state = 'contento'
      this.front = false                                 // Claude is the frontmost app
      this.reduceMotion = false
      this.pet = null
      this.sessions = Object.create(null)                                 // sid -> {open, running, mode, lastPost, last, since}
      this.markers = []                                  // pending calls: {sid, kind: done|ask|perm, t0, snoozed}
      this.latest = null                                 // sid of the most recent work event
      this.clip = null                                   // {key, id, t0, loop, dur, dir}
      this.intro = null; this.outro = null               // pc_out / pc_away windows: {sid, until}
      this.answered = null                               // c_answered: {until}
      this.ambient = null                                // {id, t0, dur}
      this.nextAmbient = AMBIENT_EVERY[this.level] || 0
      this.cool = {}                                     // clip id -> tick it may play again
      this.nudges = { day: this.day(), n: 0 }
      this.pokes = { n: 0, last: -9999, flees: [] }
      this.seq = null                                    // the agotado poke sequence: {steps: [[id, dur|null]...], i, t0, dir}
      this.bad = {}                                      // clips whose function threw: never played again
      this.log = []                                      // last decisions (tests, preview): [tick, text]
    }
    note(s) { this.log.push([this.t, s]); if (this.log.length > 40) this.log.shift() }
    attach(pet) { this.pet = pet }
    setState(s) { this.state = s }
    setAmbient(level) { this.level = AMBIENT_EVERY[level] === undefined ? 'vivo' : level; this.nextAmbient = this.t + (AMBIENT_EVERY[this.level] || 0) }
    setReduceMotion(b) { this.reduceMotion = !!b }
    setFront(b) {
      b = !!b
      if (b && !this.front && this.markers.length) {     // you came to Claude: every call is answered
        this.markers = []; this.answered = { until: this.t + 25 }; this.note('answered')
      }
      this.front = b
    }

    /* ---- the hook channel ---- */
    feed(text) { String(text).split('\n').forEach(l => { const ev = parseLine(l); if (ev) this.event(ev) }) }
    session(sid) { return this.sessions[sid] || (this.sessions[sid] = { open: false, running: 0, mode: 'think', lastPost: -9999, last: this.t, since: this.t }) }
    event(ev) {
      if (!ev || !KINDS[ev.k]) return
      const s = this.session(ev.sid)
      s.last = this.t
      const dropMarker = kinds => { this.markers = this.markers.filter(m => !(m.sid === ev.sid && kinds.indexOf(m.kind) >= 0)) }
      switch (ev.k) {
        case 'prompt':
          s.open = true; s.running = 0; s.since = this.t; s.mode = 'think'; s.lastPost = -9999
          dropMarker(['done', 'ask', 'perm']); this.latest = ev.sid; this.ambient = null
          this.intro = { sid: ev.sid, until: this.t + 40 }; this.outro = null; this.answered = null
          break
        case 'pre':
          if (ev.tool === 'AskUserQuestion') { dropMarker(['done', 'ask', 'perm']); this.open(ev.sid, 'ask'); break }
          dropMarker(['done', 'ask', 'perm'])
          if (!s.open) { s.open = true; s.since = this.t }                     // the app started in the middle of a turn
          s.running++; s.mode = modeOf(ev.tool); this.latest = ev.sid; this.intro = null; this.outro = null; this.ambient = null
          break
        case 'post': case 'fail':
          dropMarker(['ask', 'perm'])
          if (ev.tool === 'AskUserQuestion') break
          s.running = Math.max(0, s.running - 1); s.lastPost = this.t; this.latest = ev.sid
          break
        case 'perm':
          this.open(ev.sid, 'perm')
          break
        case 'stop': {
          const was = s.open
          s.open = false; s.running = 0
          dropMarker(['ask', 'perm', 'done'])
          if (was) {
            if (this.latest === ev.sid) this.outro = { sid: ev.sid, until: this.t + 30 }
            if (!this.front) this.open(ev.sid, 'done')                         // Claude already in front: you are looking at it, no call
          }
          break
        }
      }
    }
    open(sid, kind) {
      if (this.markers.some(m => m.sid === sid && m.kind === kind)) return
      const after = kind === 'done' && this.latest === sid                    // the finished-turn call waits for pc_away (3 s)
      this.markers.push({ sid, kind, t0: this.t + (after ? 30 : 0), snoozed: false, still: !this.nudge() })
      this.note('call ' + kind + (this.markers[this.markers.length - 1].still ? ' (daily ceiling: still badge)' : ''))
    }
    /* a click or a hover on him: the ladder stops moving, the badge stays (the catalogue's rule 5) */
    touch() {
      this.markers.forEach(m => { m.snoozed = true })
      if (this.ambient) { this.ambient = null; this.nextAmbient = this.t + (AMBIENT_EVERY[this.level] || 0) }
    }

    /* ---- pokes while agotado: 1 neutral, 2 annoyed, 3+ angry and runs away ---- */
    poke(away) {
      if (this.state !== 'agotado' || (this.pet && this.pet.held)) return false
      const dir = away < 0 ? -1 : 1
      if (this.seq && this.seq.calm) { this.note('poke ignored while calm'); return true }
      if (this.seq && this.seq.running) return true
      this.pokes.n = this.t - this.pokes.last <= POKE_WINDOW ? this.pokes.n + 1 : 1
      this.pokes.last = this.t
      const n = this.pokes.n
      this.pokes.flees = this.pokes.flees.filter(f => this.t - f < 36000)
      let steps
      if (n === 1) steps = [['ag_grumble', 30]]
      else if (n === 2) steps = [['ag_annoyed', 35]]
      else if (this.pokes.flees.length >= FLEES_PER_HOUR) steps = [['ag_angry', 25], ['ag_return', 0]]
      else { this.pokes.flees.push(this.t); steps = [['ag_angry', 25], ['ag_flee', 28], ['ag_calm', 'calm'], ['ag_return', 60]] }
      this.seq = { steps, i: 0, t0: this.t, dir, running: steps.length > 1 }
      this.note('poke ' + n)
      return true
    }

    /* ---- the 10 fps step ---- */
    tick() {
      this.t++
      Object.keys(this.sessions).forEach(sid => {
        const s = this.sessions[sid]
        if (s.open && this.t - s.last > (s.running ? STALE_RUNNING : STALE_QUIET)) { s.open = false; s.running = 0; this.note('turn timed out') }
      })
      if (this.intro && this.t >= this.intro.until) this.intro = null
      if (this.outro && this.t >= this.outro.until) this.outro = null
      if (this.answered && this.t >= this.answered.until) this.answered = null
      if (this.seq) this.stepSeq()
      this.pickClip()
    }
    stepSeq() {
      const q = this.seq, step = q.steps[q.i], age = this.t - q.t0
      const calm = step[1] === 'calm'
      q.calm = calm
      const done = calm ? (age >= CALM_MAX || (age >= 80 && !(this.pet && this.pet.hover))) : age >= step[1]
      if (!done) return
      q.i++; q.t0 = this.t
      if (q.i >= q.steps.length) this.seq = null
      else q.calm = q.steps[q.i][1] === 'calm'
    }
    idle() { const p = this.pet; return !p || (!p.act && !p.held && !p.hover && !p.walking && !p.fid) }
    work() {
      const s = this.latest && this.sessions[this.latest]
      return s && s.open ? s : null
    }
    nudge() {
      const d = this.day()
      if (d !== this.nudges.day) this.nudges = { day: d, n: 0 }
      if (this.nudges.n >= NUDGES_PER_DAY) return false
      this.nudges.n++; return true
    }
    desired() {
      if (this.state === 'dormido' || (this.pet && this.pet.held)) return null
      if (this.seq) {
        const q = this.seq, step = q.steps[q.i]
        return { key: 'seq:' + q.i + ':' + q.t0, id: step[0], t0: q.t0, loop: step[1] === 'calm' ? [60, 80] : null, dir: q.dir }
      }
      if (this.pet && this.pet.act) return null
      const m = this.markers.filter(x => x.t0 <= this.t).pop()
      if (m) return this.callClip(m)
      if (this.answered) return { key: 'answered', id: 'c_answered', t0: this.answered.until - 25 }
      if (this.outro) return { key: 'out:' + this.outro.until, id: 'pc_away', t0: this.outro.until - 30 }
      const w = this.work()
      if (w) return this.workClip(w)
      return this.ambientClip()
    }
    callClip(m) {
      const age = this.t - m.t0
      if (this.reduceMotion) return { key: 'rm:' + m.kind, id: m.kind === 'done' ? 'att_rm' : (m.kind === 'ask' ? 'c_needs' : 'c_perm'), t0: m.t0, fixed: m.kind === 'done' ? 20 : (m.kind === 'ask' ? 24 : 8) }
      if (m.kind === 'ask') return m.still || m.snoozed ? { key: 'ask-still', id: 'c_needs', t0: m.t0, fixed: 24 } : { key: 'ask', id: 'c_needs', t0: m.t0, loop: [24, 72] }
      if (m.kind === 'perm') return m.still || m.snoozed ? { key: 'perm-still', id: 'c_perm', t0: m.t0, fixed: 8 } : { key: 'perm', id: 'c_perm', t0: m.t0, loop: [0, 120] }
      // turn finished: att_1, then it rests with the badge; step 2 after 2 min, step 3 after 10 min, then the still cap
      const cap = { key: 'cap', id: 'att_cap', t0: m.t0 }
      if (m.snoozed || m.still || age >= STEP3 + 30) return cap
      if (age >= STEP3) return { key: 'att3', id: 'att_3', t0: m.t0 + STEP3 }
      if (age >= STEP2 + 20) return { key: 'rest', id: 'att_1', t0: m.t0, hold: 24 }
      if (age >= STEP2) return { key: 'att2', id: 'att_2', t0: m.t0 + STEP2 }
      if (age >= 30) return { key: 'rest', id: 'att_1', t0: m.t0, hold: 24 }
      return { key: 'att1', id: 'att_1', t0: m.t0 }
    }
    workClip(s) {
      if (this.intro && this.intro.sid === this.latest) return { key: 'intro', id: 'pc_out', t0: this.intro.until - 40 }
      if (this.state === 'agotado') return { key: 'sleep', id: 'pc_sleep', t0: s.since, loop: [24, 60] }
      const quiet = s.running === 0 && this.t - s.lastPost > THINK_AFTER
      const mode = quiet ? 'think' : s.mode
      if (quiet && this.t - s.since > WAIT_AFTER) return { key: 'wait', id: 'c_wait', t0: s.since }
      return { key: 'mode:' + mode, id: WORK[mode].id, t0: this.t, loop: WORK[mode].loop }
    }
    ambientClip() {
      const a = this.ambient
      if (a) {
        if (this.t - a.t0 >= a.dur) { this.ambient = null; this.nextAmbient = this.t + (AMBIENT_EVERY[this.level] || 0) }
        else return { key: 'amb:' + a.id + a.t0, id: a.id, t0: a.t0 }
      }
      if (!AMBIENT_EVERY[this.level] || this.t < this.nextAmbient || !AMBIENT_STATES[this.state] || !this.idle() || this.reduceMotion) return null
      if (this.markers.length || this.work()) return null
      const h5 = this.pet ? this.pet.h5 | 0 : 0
      let pool = AMBIENT_POOL.filter(([id]) => (this.cool[id] || 0) <= this.t && this.entry(id))
      if (h5 >= 90 && (this.cool.q_h5_fan || 0) <= this.t && this.entry('q_h5_fan')) pool = [['q_h5_fan', 8]]
      if (!pool.length) return null
      let r = this.rng() * pool.reduce((n, p) => n + p[1], 0), pick = pool[0][0]
      for (const p of pool) { r -= p[1]; if (r < 0) { pick = p[0]; break } }
      const e = this.entry(pick)
      this.ambient = { id: pick, t0: this.t, dur: Math.round(e.dur * 10) }
      this.cool[pick] = this.t + Math.round(e.cd * 10)
      this.note('ambient ' + pick)
      return { key: 'amb:' + pick + this.t, id: pick, t0: this.t }
    }
    entry(id) {
      if (this.bad[id]) return null
      if (!this.index) { this.index = {}; (PERSONALITY.CAT || []).forEach(e => { this.index[e.id] = e }) }
      return this.index[id] || null
    }
    pickClip() {
      const want = this.desired()
      if (!want || !this.entry(want.id)) { this.clip = null; return }
      if (!this.clip || this.clip.key !== want.key) {
        this.clip = { key: want.key, id: want.id, t0: want.t0, loop: want.loop || null, dir: want.dir, hold: want.hold, fixed: want.fixed }
        this.note('play ' + want.id)
      }
    }

    /* ---- the picture ---- */
    frameOf(c) {
      if (c.fixed !== undefined) return c.fixed
      let ft = this.t - c.t0
      if (ft < 0) ft = 0
      if (c.hold !== undefined && ft >= c.hold) ft = c.hold + (ft % 6)
      if (c.loop && ft >= c.loop[1]) ft = c.loop[0] + ((ft - c.loop[0]) % (c.loop[1] - c.loop[0]))
      return ft
    }
    /* the sprite spec for this tick, or null when the pet's own animation should show */
    spec() {
      const c = this.clip
      if (!c) return null
      const e = this.entry(c.id)
      if (!e) return null
      const S = { stretch: 0, eyes: 'open', mouth: 'smile', cheeks: 'c', leaf: 'up', flowers: false, cap: false, ice: false, feet: 'stand', armL: 'none', armR: 'none',
        thermo: false, dx: 0, dy: 0, look: 0, lookY: 0, sit: false, ex: [], fallen: false, tear: false, bubble: -1 }
      if (c.dir) S.dir = c.dir
      try {
        e.f(S, this.frameOf(c))
        if (this.reduceMotion) { S.dy = 0; S.stretch = 0; S.feet = 'stand' }
        S.ex = S.ex.filter(x => !NO_BROWS.test(x[0]) && Object.prototype.hasOwnProperty.call(SPR, x[0]))
        if (!EYES[S.eyes]) S.eyes = 'open'
        if (!MOUTH[S.mouth]) S.mouth = 'smile'
        if (!ARM[S.armL] || !ARM[S.armR] || !FEET[S.feet] || !LEAF[S.leaf]) throw new Error('unknown part in ' + c.id)
      } catch (err) {
        this.bad[c.id] = String(err && err.message || err); this.clip = null; this.note('broken clip ' + c.id + ': ' + this.bad[c.id])
        return null
      }
      return S
    }
    frameSpec() { return this.spec() }               // the name the shim calls (one place in the shim holds `spec()`)
    /* what the app needs besides the picture */
    directing() { return !!this.clip }
    calling() { return !!(this.clip && this.markers.length && /^(att|c_needs|c_perm)/.test(this.clip.id)) }
    mode() { return this.clip ? this.clip.id : null }
  }

  return { Director, parseLine, modeOf, MODE, WORK, AMBIENT_POOL, NO_BROWS }
})()
