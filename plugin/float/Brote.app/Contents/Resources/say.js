const fmt = (text, vars) => String(text || '').replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m))
const dayOf = (epoch, days) => days[new Date(epoch * 1000).getDay()]

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
