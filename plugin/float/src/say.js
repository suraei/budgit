const fmt = (text, vars) => String(text || '').replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m))
const dayOf = (epoch, days) => days[new Date(epoch * 1000).getDay()]

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
