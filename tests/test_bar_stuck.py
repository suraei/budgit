"""The bar says so when a turn ends on an API error, and stops saying so at the next message. The block is plain JS, so it runs here
in node against a fake `$`, extracted from plugin/hooks/register.jsx by its markers."""
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
const a = src.indexOf('/* stuck:begin */'), b = src.indexOf('/* stuck:end */')
if (a < 0 || b < 0) throw new Error('markers missing')
const shown = [], refreshed = []
const hooks = {}
const l0 = src.indexOf('/* line:begin */'), l1 = src.indexOf('/* line:end */')
if (l0 < 0 || l1 < 0) throw new Error('markers missing')
const mod = new Function('word', 'refresh', "let surface = '', lastLine = ''\n" + src.slice(l0, l1) + src.slice(a, b) + '\nreturn { registerStuck, get stuck() { return stuck } }')(
  (k, en) => en, async () => { refreshed.push(1) })
mod.registerStuck((ev, fn) => { hooks[ev] = fn })
const $ = { ui: { status: t => shown.push(t) } }
const next = e => Promise.resolve(e)
;(async () => {
  const out = []
  for (const reason of ['answer', 'aborted', 'refusal']) await hooks['turn.complete']($, { reason }, next)
  out.push(['no line for answer, aborted, refusal', shown.length === 0 && !mod.stuck])
  await hooks['turn.complete']($, { reason: 'error', agentId: 'sub-1' }, next)
  out.push(['a subagent error leaves the bar alone', shown.length === 0 && !mod.stuck])
  const r = await hooks['turn.complete']($, { reason: 'error' }, next)
  out.push(['error sets one plain line', shown.length === 1 && shown[0] === 'Claude stopped with an error' && mod.stuck])
  out.push(['the event passes through unchanged', r && r.reason === 'error'])
  await hooks['turn.complete']($, { reason: 'answer' }, next)
  out.push(['a later answer alone does not clear it', mod.stuck])
  const p = await hooks['prompt.submit']($, { text: 'hi' }, next)
  out.push(['the next message clears it and redraws the line', !mod.stuck && refreshed.length === 1 && p.text === 'hi'])
  await hooks['prompt.submit']($, { text: 'again' }, next)
  out.push(['nothing to clear: no redraw', refreshed.length === 1])
  // R-340: on the desktop the stuck line is the band's, and the footer is cleared
  const deskShown = [], inval = []
  const desk = { session: { surfaces: async () => ['desktop'] }, ui: { status: t => deskShown.push(t), invalidate: n => inval.push(n) } }
  await hooks['turn.complete'](desk, { reason: 'error' }, next)
  out.push(['desktop: the footer is cleared and the band redrawn', deskShown.length === 1 && deskShown[0] === undefined && inval.join() === 'ui.render'])
  const bad = { ui: { status() { throw new Error('boom') } } }
  await hooks['turn.complete'](bad, { reason: 'error' }, next)
  out.push(['a throwing surface never breaks the turn', true])
  console.log(JSON.stringify(out))
})()
"""


@unittest.skipUnless(NODE, "node not found")
class BarStuckTest(unittest.TestCase):
    def test_the_stuck_line_follows_the_turn_and_the_next_message(self):
        with tempfile.TemporaryDirectory() as t:
            h = os.path.join(t, "h.js")
            with open(h, "w") as f:
                f.write(HARNESS)
            r = subprocess.run([NODE, h, REG], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        for name, ok in json.loads(r.stdout):
            self.assertTrue(ok, name)

    def test_refresh_keeps_the_line_while_stuck_and_the_mod_registers_it(self):
        with open(REG, encoding="utf-8") as f:
            text = f.read()
        refresh = text[text.index("async function refresh($)"):text.index("async function startLine")]
        self.assertLess(refresh.index("if (stuck)"), refresh.index("liveLimits($)"))
        self.assertIn("registerStuck(on)", text[text.index("export function register(on)"):])


if __name__ == "__main__":
    unittest.main()
