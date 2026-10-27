/* director.js: what Brote does when Claude does something. Pure logic, no DOM, no files, no clock of its own:
   the app calls tick() at 10 fps and feed() with the lines the plugin's hook channel wrote (events.log), then asks spec() for the picture.
   Runs in JavaScriptCore after sprites.js and personality.js (the animation bank CAT, the poses and the extra sprites) and in node for the unit tests.
   Line grammar (written by plugin/hooks/brote-event.sh, nothing else): `<kind> <session> [<tool>]`
     prompt  you sent a message (a turn opens)        pre   a tool is about to run      post  it finished       fail  it failed
     stop    Claude ended the turn                    perm  Claude waits for a permission
     apifail Claude stopped on an API error (the third word is the error class, from a fixed list)
   Priorities, highest first: a poke sequence (agotado only) > a call (turn finished / question / permission) > the work mode > free time.
   Being held, an act of the pet itself (hello, poke, grumpy, land, dizzy) or the dormido state always win: spec() answers null then.
   Above them all but being held: a body clip the app plays with play() (falling, climbing, flying, riding a window). */
var DIRECTOR = (function () {
  'use strict'
  const TICK = 100                                     // ms per tick (10 fps)
  const KINDS = { prompt: 1, pre: 1, post: 1, fail: 1, stop: 1, perm: 1, apifail: 1 }
  const LINE = /^(prompt|pre|post|fail|stop|perm|apifail) ([A-Za-z0-9_-]{1,24})(?: ([A-Za-z0-9_]{1,48}))?$/

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
  /* an API error class -> how he looks: the account (login, payment, organisation) is three small stories that alternate; a limit, a busy server or anything else is a calm pause */
  const ACCOUNT = { authentication_failed: 1, oauth_org_not_allowed: 1, account_on_hold: 1, billing_error: 1, cloud_credential_error: 1 }
  const BROKE = ['c_broke_coin', 'c_broke_cane', 'c_broke_cry'], BROKE_TICKS = 160      // each 8 s story plays whole at TEMPO (16 s), then the next one
  const NO_BROWS = /^bw[LR]$/                           // the eyebrows are gone everywhere: drop them if an older bank still draws them

  function parseLine(text) {
    const m = LINE.exec(String(text).trim())
    return m ? { k: m[1], sid: m[2], tool: m[3] || '' } : null
  }

  const AMBIENT_EVERY = { quieto: 0, sutil: 1200, vivo: 600 }        // ticks between two free-time things (2 min / 60 s)
  const WORK_MIN = 150, CALM_GAP = 60, FIDGET_GAP = [150, 300]        // a work screen stays 15 s at least; 6 s of calm after anything; a fidget every 15-30 s
  const TEMPO = 0.5                                                    // the clips the director picks hold each drawn frame for 2 ticks (200 ms): at most 5 pose changes a second.
                                                                       // The app's body clips (falls, climbs, scoots, walks) keep 1: their art is timed to his real movement
  const WORK_SIT = 450, WORK_BREAK = 300                               // in a long turn he works 45 s at the laptop, closes it and walks for 30 s, then opens it again
  const AMBIENT_POOL = [['am_rope', 3], ['am_cookies', 3], ['am_tea', 3], ['am_code', 2], ['am_pc', 2], ['am_bed', 1]]
  const AMBIENT_STATES = { radiante: 1, contento: 1, justo: 1, cansado: 1, agotado: 1 }
  const READ_VARIANTS = ['bk_read_upside', 'bk_read_squint', 'bk_read_flip', 'bk_read_aha', 'bk_puf']   // two of these, never the same twice, between reading and putting back
  const HELD_BOOK = /^bk[COU]$/                      // the book drawn in his hands: the real book is hidden meanwhile (never two books)
  const AGOTADO_POOL = [['ag_bubbles', 3], ['ag_nod_off', 3], ['ag_catch', 2], ['ag_walk', 2], ['ag_dust', 1]]   // tired, he still moves: sleep bubbles, nodding off, a stumble, a sleepy walk (the walks move him for real: walkDx)
  const MOOD_RANK = { radiante: 4, contento: 3, justo: 2, cansado: 1 }
  const IGNORED_AFTER = 12000                                          // 20 min without a hover or a click: he sulks a little (w_ignored)
  const H5_RESET_DROP = 30                                             // the 5-hour use falls this much at once: the window was reset
  const IDLE_SITS = 300, IDLE_AWAY = 1200, IDLE_BACK = 5                // seconds without keyboard or mouse: he sits and hums at 5 min, sleeps at 20; back within 5 s of input
  const STREAK_WINDOW = 36000, ACTIVE_WINDOW = 6000                   // three items done within an hour is a streak; Claude counts as active for 10 min after an event
  const FEED_CLIPS = { t_new: 1, t_start: 1, t_unstale: 1, tr_week: 1 }   // what the app may report from the data line (t_done and t_streak come from the done count)
  /* a clip's length in ticks when shown at this tempo */
  function ticksAt(e, rate) { return Math.round(e.dur * 10 / (rate || 1)) }
  /* the local hour, weekday (0 Sunday) and date, for the time-of-day clips */
  function localClock() { const d = new Date(); return { h: d.getHours(), wd: d.getDay(), day: d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate() } }

  /* the clip for a change of mood (once, when the data line's state changes); waking from dormido is the pet's own 'wake' */
  function moodClip(was, now) {
    if (was === now || was === 'dormido') return null
    if (now === 'agotado' || now === 'enfermo' || now === 'dormido') return 'tr_' + now
    if (!(was in MOOD_RANK)) return now in MOOD_RANK ? 'tr_up' : null
    if (!(now in MOOD_RANK)) return null
    return MOOD_RANK[now] > MOOD_RANK[was] ? 'tr_up' : (MOOD_RANK[now] < MOOD_RANK[was] ? 'tr_down' : null)
  }
  const POKE_WINDOW = 80, FLEES_PER_HOUR = 3, CALM_MAX = 200
  const STEP2 = 1200, STEP3 = 6000, NUDGES_PER_DAY = 20, SNOOZE = 36000, THINK_AFTER = 80, WAIT_AFTER = 3000
  const ATT1 = 60, ATT2 = 40, ATT3 = 60, INTRO = 80, OUTRO = 60, ANSWERED = 50   // att_1 3 s, att_2 2 s, att_3 3 s, pc_out 4 s, pc_away 3 s, c_answered 2.5 s, at TEMPO
  const STALE_QUIET = 4200, STALE_RUNNING = 9000                      // a turn that went silent (Esc sends no Stop) closes after 7 min (long enough for the 5-minute clock), or 15 min with a tool still running

  class Director {
    constructor(opts) {
      opts = opts || {}
      this.rng = opts.rng || Math.random
      this.day = opts.day || (() => Math.floor(Date.now() / 86400000))
      this.clock = opts.clock || localClock
      this.level = opts.ambient || 'vivo'
      this.t = 0
      this.state = 'contento'
      this.front = false                                 // Claude is the frontmost app
      this.reduceMotion = false
      this.pet = null
      this.sessions = Object.create(null)                                 // sid -> {open, running, mode, lastPost, last, since}
      this.markers = []                                  // pending calls: {sid, kind: done|ask|perm|update, t0, snoozed}
      this.stuck = null                                  // Claude stopped on an API error: {sid, account, t0, first}; stays until your next message
      this.upd = null                                    // the newer version he already called about
      this.brokeN = 0                                    // which account story starts first, so a second error does not open the same way
      this.latest = null                                 // sid of the most recent work event
      this.clip = null                                   // {key, id, t0, loop, dur, dir}
      this.intro = null; this.outro = null               // pc_out / pc_away windows: {sid, until}
      this.answered = null                               // c_answered: {until}
      this.ambient = null                                // {id, t0, dur}
      this.perch = { on: 'floor', claude: false, nearClaude: false }     // where he stands, from the app: floor, claude (its top) or other (another window's top)
      this.lastTouch = 0
      this.nextAmbient = AMBIENT_EVERY[this.level] || 0
      this.cool = {}                                     // clip id -> tick it may play again
      this.nudges = { day: this.day(), n: 0 }
      this.pokes = { n: 0, last: -9999, flees: [] }
      this.quiet = false; this.runsOut = false           // from the data line: nothing in progress or next; the week runs out early at today's rate
      this.idleS = 0; this.away = null                   // seconds since your last keyboard or mouse input (from the app); asleep since {t0} while you are away
      this.lowBattery = false                            // on battery below 20 %
      this.dones = []                                    // ticks of the last hour's done items (t_streak)
      this.morningDay = null; this.morningDue = false    // the first activity of a day before noon: he yawns at the next free moment (ti_morning)
      this.reading = null                                // the book session: {steps: [[id, ticks]...], i, t0} (pick up, read, put back)
      this.seq = null                                    // the agotado poke sequence: {steps: [[id, dur|null]...], i, t0, dir}
      this.bad = {}                                      // clips whose function threw: never played again
      this.forced = null                                 // a clip the app plays for his body (falls, climbs, flights, riding a window): {id, t0, loop, dir, until}
      this.shown = null                                  // the work screen on show: {mode, since}
      this.desk = null                                   // the laptop in a long turn: {since} while he works, {closing} while he shuts it, {off} until his walk ends
      this.calmUntil = 0; this.wasBusy = false; this.hadFid = false; this.goAfter = false
      this.log = []                                      // last decisions (tests, preview): [tick, text]
    }
    note(s) { this.log.push([this.t, s]); if (this.log.length > 40) this.log.shift() }
    attach(pet) { this.pet = pet; this.hadFid = false; this.spaceFidget() }
    spaceFidget() { const p = this.pet; if (p) p.nextFid = p.t + FIDGET_GAP[0] + Math.floor(this.rng() * (FIDGET_GAP[1] - FIDGET_GAP[0])) }
    /* after any clip, act or fidget ends he only breathes and blinks for 6 s (calls never wait) */
    pace() {
      const p = this.pet
      if (p && this.hadFid && !p.fid) this.spaceFidget()
      this.hadFid = !!(p && p.fid)
      const busy = !!(this.clip || (p && (p.act || p.fid)))
      if (this.wasBusy && !busy) {
        if (this.goAfter) this.goAfter = false                                     // he sets off at once (fetching the book)
        else if (!(this.desk && this.desk.off !== undefined)) this.calmUntil = this.t + CALM_GAP   // a laptop break walks at once
      }
      this.wasBusy = busy
      if (p && this.calm()) p.nextFid = Math.max(p.nextFid, p.t + (this.calmUntil - this.t))
    }
    calm() { return this.t < this.calmUntil }
    setState(s) {
      const was = this.state
      this.state = s
      if (!this.stateSeen) { this.stateSeen = true; return }        // the first state after start is no change
      const id = moodClip(was, s)
      if (!id) return
      this.note('mood ' + was + ' -> ' + s)
      if (!this.markers.length && !this.work()) this.play(id, { rate: TEMPO })   // a call or work on screen wins; the new mood shows in his colours anyway
    }
    setPerch(p) {
      const was = this.perch && this.perch.libreta
      this.perch = { on: p.on || 'floor', claude: !!p.claude, nearClaude: !!p.nearClaude, book: !!p.book, libreta: !!p.libreta }
      if (this.perch.libreta && !was) this.putBack()                    // the Libreta opened some other way while he reads: he puts the book down at once
    }
    /* idle animations that fit where he stands and what he knows, added to the usual pool */
    situational() {
      const p = this.perch, pet = this.pet || {}, out = []
      const book = p.book && !p.libreta                                   // beside the book on the floor, the Libreta closed
      if (this.state === 'agotado') { if (p.nearClaude) out.push(['ag_lean', 3]); if (book) out.push(['bk_guard', 2]); return out }
      if (book) out.push(['bk_pickup', 3], ['bk_pat', 2], ['bk_sitnext', 1])
      if (p.on === 'claude') out.push(['sf_top', 3])
      if (p.on === 'other') out.push(['sf_other', 3])
      if (p.on === 'floor' && p.claude) out.push(['fl_up', 2])
      if ((pet.h5 | 0) >= 70) out.push(['q_h5_wipe', 2])
      if ((pet.stale | 0) > 0) out.push(['t_stale', 1])
      if (this.t - this.lastTouch >= IGNORED_AFTER) out.push(['w_ignored', 2])
      if (this.state === 'justo') out.push(['q_watch', 1])
      if (this.runsOut) out.push(['q_runout', 2])
      if (this.quiet) out.push(['t_all', 2])
      if (this.idleS >= IDLE_SITS && this.idleS < IDLE_AWAY) out.push(['ti_idle5', 3])
      if (this.lowBattery) out.push(['sys_battery', 2])
      const c = this.clock(), active = this.active()
      if (c.wd === 0 || c.wd === 6) out.push(['ti_weekend', 1])
      if (active && c.h >= 13 && c.h < 15) out.push(['ti_lunch', 1])
      if (active && c.h === 20) out.push(['ti_evening', 1])
      if (active && c.h < 5) out.push(['ti_night', 2])
      return out
    }
    /* Claude did something in the last 10 min (the time-of-day clips need you to be working) */
    active() { for (const k in this.sessions) if (this.t - this.sessions[k].last <= ACTIVE_WINDOW) return true; return false }
    /* the data line changed: {clips: [t_new, t_start, t_unstale, tr_week], done: items newly done, quiet, runsOut}. The most important one plays */
    setFeed(f) {
      f = f || {}
      this.quiet = !!f.quiet; this.runsOut = !!f.runsOut
      const ids = (Array.isArray(f.clips) ? f.clips : []).filter(id => FEED_CLIPS[id] === 1)
      const done = Math.max(0, Math.min(10, f.done | 0))
      for (let i = 0; i < done; i++) this.dones.push(this.t)
      this.dones = this.dones.filter(t => this.t - t < STREAK_WINDOW)
      if (done) ids.push(this.dones.length >= 3 && (this.cool.t_streak || 0) <= this.t ? 't_streak' : 't_done')
      const best = ids.filter(id => this.entry(id)).sort((a, b) => this.entry(b).pri - this.entry(a).pri)[0]
      return best ? this.react(best) : false
    }
    /* seconds since your last input: 20 min away, he curls up and sleeps; your first input wakes him with a wave */
    setIdle(sec) {
      this.idleS = Math.max(0, sec | 0)
      if (this.idleS >= IDLE_AWAY && !this.away) { this.away = { t0: this.t }; this.reading = null; this.ambient = null; this.note('away') }
      else if (this.away && this.idleS < IDLE_BACK) { this.away = null; this.note('back'); this.react('ti_return') }
    }
    /* on battery below 20 %: he catches it once when it drops, then now and then in his free time */
    setBattery(low) { const was = this.lowBattery; this.lowBattery = !!low; if (this.lowBattery && !was) this.react('sys_battery') }
    /* a one-off reaction (book changes, a failed tool): skipped while a call shows, he is held, asleep, reading or his body is busy.
       opts: from / to (drawn frames) play one part of the clip; go: no calm after it, he sets off at once */
    react(id, opts) {
      const e = this.entry(id), p = this.pet
      if (!e || (this.cool[id] || 0) > this.t) return false
      if (this.forced || this.state === 'dormido' || (p && p.held) || this.reading || this.seq || this.pendingCall()) return false
      this.cool[id] = this.t + Math.round(e.cd * 10)
      this.note('react ' + id)
      return this.play(id, Object.assign({}, opts, { rate: TEMPO }))
    }
    setH5(h) {
      const was = this.h5
      this.h5 = h
      if (was !== undefined && was - h >= H5_RESET_DROP && !this.markers.length && !this.work()) { this.note('5 h window reset'); this.play('tr_5h', { rate: TEMPO }) }
    }
    setAmbient(level) { this.level = AMBIENT_EVERY[level] === undefined ? 'vivo' : level; this.nextAmbient = this.t + (AMBIENT_EVERY[this.level] || 0) }
    setReduceMotion(b) { this.reduceMotion = !!b }
    setFront(b) {
      b = !!b
      if (b && !this.front && this.markers.some(m => m.kind !== 'update')) {     // you came to Claude: every call is answered (the update notice is not about a chat)
        this.markers = this.markers.filter(m => m.kind === 'update'); this.answered = { until: this.t + ANSWERED }; this.note('answered')
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
      const c = this.clock()
      if (this.morningDay !== c.day) { this.morningDay = c.day; this.morningDue = c.h >= 5 && c.h < 12 }
      const dropMarker = kinds => { this.markers = this.markers.filter(m => !(m.sid === ev.sid && kinds.indexOf(m.kind) >= 0)) }
      switch (ev.k) {
        case 'prompt':
          this.stuck = null                                                    // you wrote again: whatever it was, it is your turn now
          s.open = true; s.running = 0; s.since = this.t; s.mode = 'think'; s.lastPost = -9999
          dropMarker(['done', 'ask', 'perm']); this.latest = ev.sid; this.ambient = null
          this.intro = { sid: ev.sid, until: this.t + INTRO }; this.outro = null; this.answered = null; this.desk = null
          break
        case 'pre':
          if (ev.tool === 'AskUserQuestion') { dropMarker(['done', 'ask', 'perm']); this.open(ev.sid, 'ask'); break }
          dropMarker(['done', 'ask', 'perm']); this.unstick(ev.sid)
          if (!s.open) { s.open = true; s.since = this.t }                     // the app started in the middle of a turn
          s.running++; s.mode = modeOf(ev.tool); this.latest = ev.sid; this.intro = null; this.outro = null; this.ambient = null
          break
        case 'post': case 'fail':
          dropMarker(['ask', 'perm']); this.unstick(ev.sid)
          if (ev.tool === 'AskUserQuestion') break
          if (ev.k === 'fail') this.react('c_error')                           // a tool failed: he flinches (at most every 20 s)
          s.running = Math.max(0, s.running - 1); s.lastPost = this.t; this.latest = ev.sid
          break
        case 'perm':
          this.unstick(ev.sid); this.open(ev.sid, 'perm')
          break
        case 'apifail':
          s.open = false; s.running = 0
          dropMarker(['ask', 'perm', 'done'])
          this.stuck = { sid: ev.sid, account: Object.prototype.hasOwnProperty.call(ACCOUNT, ev.tool), t0: this.t, first: this.brokeN++ }
          this.latest = ev.sid; this.intro = null; this.outro = null; this.ambient = null; this.answered = null
          this.note('stuck ' + (this.stuck.account ? 'account' : 'pause'))
          break
        case 'stop': {
          const was = s.open
          s.open = false; s.running = 0
          dropMarker(['ask', 'perm', 'done']); this.unstick(ev.sid)
          if (was) {
            if (this.latest === ev.sid && !(this.desk && this.desk.off)) this.outro = { sid: ev.sid, until: this.t + OUTRO }   // on his walk the laptop is shut already
            if (!this.front) this.open(ev.sid, 'done')                         // Claude already in front: you are looking at it, no call
          }
          break
        }
      }
    }
    /* the app tells him a newer Budgit exists (version text) or that there is none (null); he calls once per version and run, a click answers it */
    setUpdate(version) {
      const v = typeof version === 'string' && /^[0-9A-Za-z.+-]{1,24}$/.test(version) ? version : null
      if (!v) { this.markers = this.markers.filter(m => m.kind !== 'update'); this.upd = null; return }
      if (this.upd === v) return
      this.upd = v
      this.markers = this.markers.filter(m => m.kind !== 'update')
      this.open('upd', 'update')
    }
    dismissUpdate() { this.markers = this.markers.filter(m => m.kind !== 'update') }
    /* the call on show, for the app's click: {kind, sid} or null */
    pendingCall() { const m = this.markers.filter(x => x.t0 <= this.t).pop(); return m ? { kind: m.kind, sid: m.sid } : null }
    unstick(sid) { if (this.stuck && this.stuck.sid === sid) this.stuck = null }
    open(sid, kind) {
      if (this.markers.some(m => m.sid === sid && m.kind === kind)) return
      const after = kind === 'done' && this.latest === sid && !!this.outro    // the finished-turn call waits for pc_away
      this.markers.push({ sid, kind, t0: this.t + (after ? OUTRO : 0), snoozed: false, still: !this.nudge() })
      this.note('call ' + kind + (this.markers[this.markers.length - 1].still ? ' (daily ceiling: still badge)' : ''))
    }
    /* a click or a hover on him: the ladder stops moving, the badge stays (the catalogue's rule 5) */
    /* ---- the app's body clips: they win over everything but being held, and end on their own unless they loop ---- */
    play(id, opts) {
      opts = opts || {}
      const e = this.entry(id)
      if (!e) return false
      const dir = opts.dir === -1 || opts.dir === 1 ? opts.dir : 0
      if (this.forced && this.forced.id === id) { this.forced.dir = dir; return true }
      const rate = opts.rate || 1, t0 = this.t - Math.ceil((opts.from || 0) / rate)
      const until = opts.loop ? Infinity : opts.to !== undefined ? t0 + Math.ceil(opts.to / rate) : t0 + ticksAt(e, rate)
      this.forced = { id, t0, loop: opts.loop ? (Array.isArray(opts.loop) ? opts.loop : [0, ticksAt(e, 1)]) : null, dir, until, rate }
      this.goAfter = !!opts.go
      this.note('body ' + id)
      return true
    }
    stop() { this.forced = null }
    working() { return !!this.work() }
    touch() {
      this.lastTouch = this.t
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
      if (this.forced && this.t >= this.forced.until) this.forced = null
      if (this.seq) this.stepSeq()
      if (this.reading) this.stepReading()
      this.pickClip()
      this.pace()
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
    /* ---- the book session: pick it up, read (the base, then two variants), put it back. One every 10 min (bk_pickup's cooldown) ---- */
    startReading() {
      const pool = READ_VARIANTS.filter(id => this.entry(id)), two = []
      while (two.length < 2 && pool.length) two.push(pool.splice(Math.floor(this.rng() * pool.length), 1)[0])
      const ticks = id => ticksAt(this.entry(id), TEMPO)
      this.reading = { steps: ['bk_pickup', 'bk_read_up', ...two, 'bk_putback'].map(id => [id, ticks(id)]), i: 0, t0: this.t }
      this.note('reading')
    }
    stepReading() {
      const q = this.reading, p = this.pet
      if (this.markers.length || this.work() || this.forced || (p && p.held) || this.state === 'dormido') { this.reading = null; this.note('reading ended'); return }
      if (this.t - q.t0 < q.steps[q.i][1]) return
      q.i++; q.t0 = this.t
      if (q.i >= q.steps.length) this.reading = null
    }
    /* a click on him while he holds the book: he drops it, startled (it beats every other animation) */
    dropBook() {
      if (!this.reading || !this.holding()) return false
      this.reading = { steps: [['bk_drop', ticksAt(this.entry('bk_drop'), TEMPO)]], i: 0, t0: this.t }
      this.note('book dropped'); return true
    }
    putBack() {
      if (!this.reading || !this.holding()) return false
      this.reading = { steps: [['bk_putback', ticksAt(this.entry('bk_putback'), TEMPO)]], i: 0, t0: this.t }
      return true
    }
    /* the picture on show has the book in his hands (the app hides the real one), or is a book clip (drawn with the book on his right) */
    holding() { const S = this.clip && /^bk_/.test(this.clip.id) ? this.build() : null; return !!S && S.ex.some(e => HELD_BOOK.test(e[0])) }
    bookClip() { return !!(this.clip && /^bk_/.test(this.clip.id)) }
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
      if (this.pet && this.pet.held) return null
      if (this.forced) { const f = this.forced; return { key: 'body:' + f.id + ':' + f.t0 + ':' + f.dir, id: f.id, t0: f.t0, loop: f.loop, dir: f.dir || undefined, rate: f.rate } }
      if (this.state === 'dormido') return null
      if (this.seq) {
        const q = this.seq, step = q.steps[q.i]
        return { key: 'seq:' + q.i + ':' + q.t0, id: step[0], t0: q.t0, loop: step[1] === 'calm' ? [60, 80] : null, dir: q.dir }
      }
      if (this.pet && this.pet.act) return null
      const m = this.markers.filter(x => x.t0 <= this.t).pop()
      if (m) return this.callClip(m)
      if (this.answered) return { key: 'answered', id: 'c_answered', t0: this.answered.until - ANSWERED, rate: TEMPO }
      if (this.outro) return { key: 'out:' + this.outro.until, id: 'pc_away', t0: this.outro.until - OUTRO, rate: TEMPO }
      const w = this.work()
      if (this.stuck && (!w || this.latest === this.stuck.sid)) return this.stuckClip(this.stuck)
      if (!w) this.desk = null
      if (w) return this.workClip(w)
      if (this.away) return { key: 'away:' + this.away.t0, id: 'ti_idle20', t0: this.away.t0, hold: 40, rate: TEMPO }     // asleep under the cap until you come back
      if (this.reading) { const q = this.reading, st = q.steps[q.i]; return { key: 'read:' + q.i + ':' + q.t0, id: st[0], t0: q.t0, rate: TEMPO } }
      return this.ambientClip()
    }
    callClip(m) {
      const age = this.t - m.t0
      if (this.reduceMotion) return { key: 'rm:' + m.kind, id: m.kind === 'done' ? 'att_rm' : (m.kind === 'ask' ? 'c_needs' : (m.kind === 'update' ? 'c_update' : 'c_perm')), t0: m.t0, fixed: m.kind === 'done' ? 20 : (m.kind === 'ask' ? 24 : 8) }
      if (m.kind === 'ask') return m.still || m.snoozed ? { key: 'ask-still', id: 'c_needs', t0: m.t0, fixed: 24 } : { key: 'ask', id: 'c_needs', t0: m.t0, loop: [24, 72], rate: TEMPO }
      if (m.kind === 'update') return m.still || m.snoozed ? { key: 'upd-still', id: 'c_update', t0: m.t0, fixed: 8 } : { key: 'upd', id: 'c_update', t0: m.t0, loop: [0, 120], rate: TEMPO }
      if (m.kind === 'perm') return m.still || m.snoozed ? { key: 'perm-still', id: 'c_perm', t0: m.t0, fixed: 8 } : { key: 'perm', id: 'c_perm', t0: m.t0, loop: [0, 120], rate: TEMPO }
      // turn finished: att_1, then it rests with the badge; step 2 after 2 min, step 3 after 10 min, then the still cap
      const cap = { key: 'cap', id: 'att_cap', t0: m.t0 }
      if (m.snoozed || m.still || age >= STEP3 + ATT3) return cap
      if (age >= STEP3) return { key: 'att3', id: 'att_3', t0: m.t0 + STEP3, rate: TEMPO }
      if (age >= STEP2 + ATT2) return { key: 'rest', id: 'att_1', t0: m.t0, hold: 24, rate: TEMPO }
      if (age >= STEP2) return { key: 'att2', id: 'att_2', t0: m.t0 + STEP2, rate: TEMPO }
      if (age >= ATT1) return { key: 'rest', id: 'att_1', t0: m.t0, hold: 24, rate: TEMPO }
      return { key: 'att1', id: 'att_1', t0: m.t0, rate: TEMPO }
    }
    stuckClip(k) {
      if (!k.account) return this.reduceMotion ? { key: 'rm:pause', id: 'c_paused', t0: k.t0, fixed: 0 } : { key: 'pause:' + k.t0, id: 'c_paused', t0: k.t0, rate: TEMPO }
      if (this.reduceMotion) return { key: 'rm:broke', id: BROKE[k.first % BROKE.length], t0: k.t0, fixed: 0 }
      const n = Math.floor((this.t - k.t0) / BROKE_TICKS)
      return { key: 'broke:' + k.t0 + ':' + n, id: BROKE[(k.first + n) % BROKE.length], t0: k.t0 + n * BROKE_TICKS, rate: TEMPO }
    }
    workClip(s) {
      if (this.intro && this.intro.sid === this.latest) return { key: 'intro', id: 'pc_out', t0: this.intro.until - INTRO, rate: TEMPO }
      if (this.state === 'agotado') return { key: 'sleep', id: 'pc_sleep', t0: s.since, loop: [24, 60], rate: TEMPO }
      const quiet = s.running === 0 && this.t - s.lastPost > THINK_AFTER
      const want = quiet ? 'think' : s.mode
      if (quiet && this.t - s.since > WAIT_AFTER) return { key: 'wait', id: 'c_wait', t0: s.since, rate: TEMPO }
      const k = this.desk || (this.desk = { since: this.t })
      if (k.closing !== undefined) {                                     // he shuts the laptop, then walks
        if (this.t - k.closing < OUTRO) return { key: 'desk-close:' + k.closing, id: 'pc_away', t0: k.closing, rate: TEMPO }
        this.desk = { off: this.t + WORK_BREAK }
        return this.workClip(s)
      }
      if (k.off !== undefined) {
        if (this.t < k.off) return null                                  // no clip: the app's stroll walks him
        this.desk = { since: this.t }; this.shown = null
        this.intro = { sid: this.latest, until: this.t + INTRO }         // back at the laptop: he opens it again
        return this.workClip(s)
      }
      if (this.t - k.since >= WORK_SIT && !this.markers.length) { this.desk = { closing: this.t }; this.note('laptop break'); return this.workClip(s) }
      if (!this.shown || (this.shown.mode !== want && this.t - this.shown.since >= WORK_MIN)) this.shown = { mode: want, since: this.t }
      const mode = this.shown.mode
      return { key: 'mode:' + mode, id: WORK[mode].id, t0: this.t, loop: WORK[mode].loop, rate: TEMPO }
    }
    ambientClip() {
      const a = this.ambient
      if (a) {
        if (this.t - a.t0 >= a.dur) { this.ambient = null; this.nextAmbient = this.t + (AMBIENT_EVERY[this.level] || 0) }
        else return { key: 'amb:' + a.id + a.t0, id: a.id, t0: a.t0, rate: TEMPO }
      }
      if (!AMBIENT_EVERY[this.level] || this.t < this.nextAmbient || !AMBIENT_STATES[this.state] || !this.idle() || this.reduceMotion || this.calm()) return null
      if (this.markers.length || this.work()) return null
      const h5 = this.pet ? this.pet.h5 | 0 : 0
      let pool = (this.state === 'agotado' ? AGOTADO_POOL : AMBIENT_POOL).concat(this.situational()).filter(([id]) => (this.cool[id] || 0) <= this.t && this.entry(id))
      if (h5 >= 90 && (this.cool.q_h5_fan || 0) <= this.t && this.entry('q_h5_fan')) pool = [['q_h5_fan', 8]]
      if (this.morningDue && this.state !== 'agotado' && this.entry('ti_morning')) { pool = [['ti_morning', 1]]; this.morningDue = false }
      if (!pool.length) return null
      let r = this.rng() * pool.reduce((n, p) => n + p[1], 0), pick = pool[0][0]
      for (const p of pool) { r -= p[1]; if (r < 0) { pick = p[0]; break } }
      const e = this.entry(pick)
      if (pick === 'bk_pickup') { this.cool[pick] = this.t + Math.round(e.cd * 10); this.startReading(); return this.desired() }
      this.ambient = { id: pick, t0: this.t, dur: ticksAt(e, TEMPO) }
      this.cool[pick] = this.t + Math.round(e.cd * 10)
      this.note('ambient ' + pick)
      return { key: 'amb:' + pick + this.t, id: pick, t0: this.t, rate: TEMPO }
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
        this.clip = { key: want.key, id: want.id, t0: want.t0, loop: want.loop || null, dir: want.dir, hold: want.hold, fixed: want.fixed, rate: want.rate || 1 }
        this.note('play ' + want.id)
      }
    }

    /* ---- the picture ---- */
    frameOf(c) {
      if (c.fixed !== undefined) return c.fixed
      let ft = this.t - c.t0
      if (ft < 0) ft = 0
      ft = Math.floor(ft * (c.rate || 1))                // clip time: at TEMPO each drawn frame shows for 2 ticks
      if (c.hold !== undefined && ft >= c.hold) ft = c.hold + (ft % 6)
      if (c.loop && ft >= c.loop[1]) ft = c.loop[0] + ((ft - c.loop[0]) % (c.loop[1] - c.loop[0]))
      return ft
    }
    /* the sprite spec for this tick, or null when the pet's own animation should show.
       A walking clip (catalog flag `walk`) draws no shift of its own: walkDx() gives it to the app, which moves him for real */
    spec() { const S = this.build(); if (S && this.entry(this.clip.id).walk) S.dx = 0; return S }
    walkDx() { const c = this.clip; if (!c || !this.entry(c.id) || !this.entry(c.id).walk) return null; const S = this.build(); return S ? S.dx : null }
    build() {
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
    calling() { return !!(this.clip && this.markers.length && /^(att|c_needs|c_perm|c_update)/.test(this.clip.id)) }
    mode() { return this.clip ? this.clip.id : null }
  }

  return { Director, TEMPO, WORK_SIT, WORK_BREAK, READ_VARIANTS, parseLine, modeOf, MODE, WORK, AMBIENT_POOL, AGOTADO_POOL, moodClip, NO_BROWS }
})()
