"""R-293: the mod's status entry (`$.ui.status`) is plain text on every surface. The host draws no ANSI there, not even in the
terminal: it prefixes the entry with its own warning sign and the plugin name and shows an escape as a replacement character
(seen on the user's terminal, 0.13.0). Colour belongs to a real `statusLine` command only. The part of plugin/hooks/register.jsx
between the colour markers plus refresh() runs here in node against a fake `$` and a fake budgit."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import ROOT

NODE = shutil.which("node")
REG = os.path.join(ROOT, "plugin", "hooks", "register.jsx")

HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8')
const a = src.indexOf('/* colour:begin */'), b = src.indexOf('/* colour:end */')
const l0 = src.indexOf('/* line:begin */'), l1 = src.indexOf('/* line:end */')
// R-521: refresh() reads the module state declared just above it (the run in flight, the last good line)
const r0 = src.indexOf('let refreshing = null'), r1 = src.indexOf('async function startLine')
const f0 = src.indexOf('const fmt = '), f1 = src.indexOf('\n', f0)
if (a < 0 || b < 0 || r0 < 0 || r1 < 0 || l0 < 0 || l1 < 0 || f0 < 0) throw new Error('markers missing')
const COLOURED = '\x1b[38;2;158;211;106m✻ 15 en curso\x1b[0m \x1b[1;31mx\x1b[0m'
async function run(surf, answer, surfaces) {
  const asked = [], shown = [], invalidated = []
  const f = new Function('surf', 'budgit', 'liveLimits', 'withLoaded', 'word', '$out',
    "let surface = surf, lastLine = '', stuck = false\nconst REFRESH_MS = 60000\n" + src.slice(f0, f1) + '\n' + src.slice(a, b) + src.slice(l0, l1) + src.slice(r0, r1) +
    '\nreturn { refresh, getLast: () => lastLine, getTail: () => hintTail }')
  const m = f(surf, async (_$, _c, args) => { asked.push(args); return answer }, async () => '', x => x, (k, en) => en, null)
  const session = { cwd: async () => '/p' }
  if (surfaces) session.surfaces = surfaces
  await m.refresh({ session, ui: { status: t => shown.push(t), invalidate: n => invalidated.push(n) } })
  return { asked, shown, invalidated, last: m.getLast(), tail: m.getTail() }
}
;(async () => {
  const out = []
  for (const surf of ['terminal', 'desktop', 'vscode', 'mobile', '', 'something-new']) {
    const r = await run(surf, COLOURED)
    out.push([surf + ': asks for no colour', r.asked[0].includes('off') && !r.asked[0].includes('auto') && !r.asked[0].includes('on')])
    // R-340: on the desktop the footer entry is cleared and the band draws the same plain line, so the escape check reads what is kept
    const desk = surf === 'desktop'
    const term0 = surf === 'terminal'    // R-401: on the terminal the line is the tail of the hint line, not a footer entry
    const plain = desk ? r.last : term0 ? r.tail : r.shown[0]
    out.push([surf + ': even if an escape arrives, the shown line has none', r.shown.length === 1 && !plain.includes('\x1b') && plain === '✻ 15 en curso x' && ((!desk && !term0) || r.shown[0] === undefined)])
    const ctl = await run(surf, 'a\x1b]0;title\x07b\x9b31mc')
    out.push([surf + ': OSC and C1 sequences are cut too', (desk ? ctl.last : term0 ? ctl.tail : ctl.shown[0]) === 'abc'])
  }
  const e = await run('desktop', '')
  out.push(['an empty answer clears the line', e.shown.length === 1 && e.shown[0] === undefined && e.last === ''])
  // R-340: the surface session.start named is only the first at start; what the host says now decides
  const LINE = '✻ 14 en curso, el limite semanal se agota el martes por la tarde'
  const late = await run('terminal', LINE, async () => ['terminal', 'desktop'])
  out.push(['desktop attached after start: footer cleared, band redrawn, line kept', late.shown.length === 1 && late.shown[0] === undefined && late.invalidated.join() === 'ui.render' && late.last === LINE])
  const unset = await run('', LINE, async () => ['desktop'])
  out.push(['no surface named at start, desktop attached: footer cleared', unset.shown.length === 1 && unset.shown[0] === undefined && unset.last === LINE])
  const term = await run('terminal', LINE, async () => ['terminal'])
  out.push(['terminal only: the hint tail carries the line, no footer entry', term.tail === LINE && term.shown[0] === undefined && term.invalidated.join() === 'ui.render'])
  const gone = await run('desktop', LINE, async () => ['terminal'])
  out.push(['desktop left: the hint tail carries the line again', gone.tail === LINE])
  const refused = await run('desktop', LINE, async () => { throw new Error('no') })
  out.push(['host cannot say: what start named decides', refused.shown[0] === undefined && refused.last === LINE])
  const refusedT = await run('terminal', LINE, async () => { throw new Error('no') })
  out.push(['host cannot say, terminal: the hint tail', refusedT.tail === LINE])
  const vs = await run('vscode', LINE, async () => ['vscode'])
  out.push(['a surface with no hint tail keeps the footer entry', vs.shown[0] === LINE && vs.tail === ''])
  console.log(JSON.stringify(out))
})()
"""


@unittest.skipUnless(NODE, "node not found")
class BarColourSurfaceTest(unittest.TestCase):
    def test_the_status_entry_is_plain_on_every_surface(self):
        with tempfile.TemporaryDirectory() as t:
            h = os.path.join(t, "h.js")
            with open(h, "w") as f:
                f.write(HARNESS)
            r = subprocess.run([NODE, h, REG], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name, ok in json.loads(r.stdout):
            self.assertTrue(ok, name)

    def test_no_call_site_of_the_status_entry_can_carry_colour(self):
        with open(REG, encoding="utf-8") as f:
            text = f.read()
        for flag in ("'--colour', 'auto'", "'--colour', 'on'"):
            self.assertNotIn(flag, text)
        self.assertEqual(text.count("'--ansi'"), 1)             # the explicit /budgit-colour-probe command, typed by the user
        sites = [l.split("//")[0].strip() for l in text.splitlines() if "$.ui.status(" in l and not l.strip().startswith("//")]
        self.assertEqual(sorted(sites), sorted([
            "$.ui.status(undefined)",          # R-340: the desktop footer is cleared, the band draws the line
            "$.ui.status(undefined)",          # R-369: and again when the band is drawn and the footer still held the line
            "$.ui.status(undefined)",          # R-401: the terminal's line is the hint tail, so any old footer entry is cleared
            "$.ui.status(line || undefined)",   # showLine: the one place the line is set (R-319)
            "$.ui.status(line)"]))             # the probe

    def test_the_words_shown_in_the_status_entry_have_no_escape(self):
        import i18n
        for lang in ("en", "es"):
            self.assertNotIn("\x1b", i18n.t("brote.stuck", lang))


if __name__ == "__main__":
    unittest.main()
