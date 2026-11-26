"""R-310: the floating Brote can be turned on from the desktop app alone: `/budgit-float on` runs the plugin's own CLI, with no
Terminal and no `budgit` on a PATH. Only a fixed list of words reaches the CLI. The part of plugin/hooks/register.jsx between the
float markers runs here in node against a fake `$`."""
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
const a = src.indexOf('/* float:begin */'), b = src.indexOf('/* float:end */')
if (a < 0 || b < 0) throw new Error('markers missing')
const m = new Function('FLOAT_TIMEOUT_MS', src.slice(a, b) + '\nreturn { floatRun }')(20000)
async function ask(arg, reply) {
  const calls = []
  const $ = { plugin: { root: '/plug' }, process: { run: async (argv, o) => { calls.push([argv, o]); if (reply === 'throw') throw new Error('x'); return reply } } }
  return { out: await m.floatRun($, arg), calls }
}
;(async () => {
  const out = []
  const run = ['python3', '-I', '-S', '/plug/bin/budgit', 'brote']
  const cases = { '': ['status'], on: ['on'], ' ON ': ['on'], off: ['off'], stop: ['stop'], status: ['status'], uninstall: ['uninstall'] }
  for (const [arg, want] of Object.entries(cases)) {
    const r = await ask(arg, { exitCode: 0, stdout: 'done\n', stderr: '' })
    out.push([JSON.stringify(arg) + ' runs ' + want.join(' '), JSON.stringify(r.calls[0][0]) === JSON.stringify(run.concat(want)) && r.out === 'done'])
    out.push([JSON.stringify(arg) + ' has a timeout', r.calls[0][1].timeoutMs === 20000])
  }
  for (const arg of ['yes', 'on --yes', 'on; rm -rf ~', '--help', 'on\nstatus', '../on', 'x']) {
    const r = await ask(arg, { exitCode: 0, stdout: 'no', stderr: '' })
    out.push([JSON.stringify(arg) + ' reaches nothing', r.out === null && r.calls.length === 0])
  }
  out.push(['stderr is shown too', (await ask('on', { exitCode: 4, stdout: '', stderr: 'refused' })).out === 'refused'])
  out.push(['a failing run says nothing and the command falls back to its own words', (await ask('on', 'throw')).out === ''])
  console.log(JSON.stringify(out))
})()
"""


@unittest.skipUnless(NODE, "node not found")
class FloatSlashTest(unittest.TestCase):
    def test_only_a_fixed_list_of_words_reaches_the_cli(self):
        with tempfile.TemporaryDirectory() as t:
            h = os.path.join(t, "h.js")
            with open(h, "w") as f:
                f.write(HARNESS)
            r = subprocess.run([NODE, h, REG], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name, ok in json.loads(r.stdout):
            self.assertTrue(ok, name)

    def test_the_command_is_registered_and_wired(self):
        with open(REG, encoding="utf-8") as f:
            text = f.read()
        for name in ("budgit-brote", "budgit-float"):                  # the new name, and the old one kept as an alias
            self.assertIn("['%s'," % name, text)
        self.assertIn("for (const name of ['budgit-brote', 'budgit-float'])", text)
        self.assertIn("{ command: name }", text)
        self.assertIn("floatRun($, e.args)", text)

    def test_its_words_exist_in_both_languages(self):
        import i18n
        for lang in ("en", "es"):
            for key in ("cmd.desc_brotewin", "cmd.brote_usage", "cmd.brote_unavailable", "cmd.desc_float", "cmd.float_usage", "cmd.float_unavailable"):
                self.assertNotEqual(i18n.t(key, lang), key, (lang, key))
        self.assertNotEqual(i18n.t("cmd.brote_usage", "en"), i18n.t("cmd.brote_usage", "es"))
        self.assertIn("/budgit-brote", i18n.t("cmd.brote_usage", "en"))


if __name__ == "__main__":
    unittest.main()
