# Brote, floating window

**macOS only (Apple Silicon and Intel).** This is Brote, the pet of the Budgit plugin, as a small window that floats over everything. The plugin ships it ready-built; this folder is the source it is built from, so you can read it and check it.

He sits near Claude's chat bar on every Space. You can drag him, throw him and poke him. With the data line on, his body shows how your week is going (mood, sweat, a stale marker). He has no text and no card above him.

## It turns on by itself

After you install or update the plugin (from the marketplace), the next message you send in any Claude chat starts him: the plugin copies the app that ships in it (`plugin/float/Brote.app`, about 3 MB, signed ad hoc, no developer certificate) to `~/Library/Application Support/BroteWindow/` and opens it. You do not need a new chat; a chat that was already open sees him at its next message once it runs the new version of the plugin (see below). The chat gets one line, once: "Brote's floating window is on; /budgit-brote off turns it off."

- he opens with every Claude session and at every message you send if he is not up, and closes by himself about two minutes after the last chat ends. When he is already up the check is a single quick read and starts nothing;
- it asks macOS for no permission (no screen recording, no accessibility, no files, no login item) and never touches your project files;
- his data line follows the Budgit project of your newest session, and shows nothing when that folder has none;
- when the plugin starts him it first closes an older copy of him that is still running from that same folder (started without `--plugin-root`, not holding `brote.lock`), so there are never two: only that process, only if it is yours and runs that exact binary;
- it does nothing on a Mac that is not at its own screen (an SSH login, CI), and `BUDGIT_FLOAT_AUTO=off` in your environment stops it everywhere;
- a plugin folder you registered by hand, or a "directory" marketplace pointing at a checkout of this repository (a developer setup), never turns him on by itself; type `/budgit-brote on` there once. It is trusted only when Claude Code itself lists it (`installed_plugins.json`, or `known_marketplaces.json` plus that folder's `.claude-plugin/marketplace.json`), you own it, nobody else can write it and every file matches `MANIFEST.sha256`; a copy under `plugins/marketplaces/` never is.

**Turn him on or off with a command.** In the desktop app: `/budgit-brote on`, `/budgit-brote off`, `/budgit-brote status`, `/budgit-brote stop` (close him now; the next message you send opens him again, so use `off` to keep him gone), `/budgit-brote uninstall`. In a terminal: `budgit brote on|off|status|stop|uninstall`. `off` closes him and is remembered: no update, no new chat and no message turns him on again until you type `on`. (`/budgit-float` and `budgit float` still work: they are the old name.)

**One limit.** A chat that was already open when the plugin updated keeps running the old version of the plugin until Claude loads the new one: reopen the app, open a new chat or run `/reload-plugins`. Until then that chat cannot turn him on; every other chat can.

## Check what you run

The plugin copy is trusted only when it is the version Claude Code itself installed, every file of it matches `MANIFEST.sha256`, and it carries no quarantine mark (a downloaded copy is refused, never "fixed"). The installed copy is checked again before every launch. The app is started by its own binary, with an empty environment but `PATH`, `HOME` and `CLAUDE_CONFIG_DIR`.

`plugin/float/` holds the app, `MANIFEST.sha256` (a SHA-256 for every file), `BUILD-INFO.txt` (the compiler, SDK and linker it was built with) and `src/` (the exact sources). The build is reproducible: the same sources and toolchain give byte-identical output from any folder. To see it yourself you need Apple's command line tools (`xcode-select --install`), then:

```bash
BROTE_OUT=/tmp/brote-check ./build-universal.sh
shasum -a 256 /tmp/brote-check/Brote.app/Contents/MacOS/Brote plugin/float/Brote.app/Contents/MacOS/Brote
```

The two lines match when your toolchain is the one in `BUILD-INFO.txt`. A manifest that sits next to the binary it describes catches corruption, a partial copy and a mixed bundle; it cannot vouch for the repository itself, so a release also names its manifest hash in the changelog.

## Remove him

```bash
budgit brote uninstall
```

(`/budgit-brote uninstall` in the desktop app) or right-click him and choose "Remove Brote…". If the plugin is already gone, `sh float-uninstall.sh` from `plugin/scripts/` needs nothing but the shell. Either way the app, its settings, its logs and its session files go, and nothing else, except one small file, `float.json`, that says he is off so he does not come back by himself.

## What he reads and writes

- He runs `/usr/bin/python3 -B <plugin>/bin/budgit status --brote --live` every 30 seconds: no shell, from `/`, read only, stopped after 5 seconds, and only when you own the script and nobody else can write it. He keeps the last answer on your Mac, as the fields of the status line, and never your ledger text.
- Only when you click him to open a chat, he reads the desktop app's own session records for three keys (the session ids and whether it is archived), and logs none of it.
- One small file per live Claude session in `BroteWindow/sessions/` (process id, start time, a 12-character session id, the project folder), so he knows when to leave. `probe.log` holds no path and is private to you.

## Build from source (developers)

`./build.sh` compiles `brote.swift` for this Mac and puts `Brote.app` in `~/Library/Application Support/BroteWindow/` (set `BROTE_OUT` to an absolute path to put it elsewhere). Run the app by its path from a Terminal tab, never with `open`:

```bash
"$HOME/Library/Application Support/BroteWindow/Brote.app/Contents/MacOS/Brote" --no-data
```

`--no-data` shows him without your week. `--hub <folder>` (a clone of the Budgit repo, the folder that holds `plugin/bin/budgit`) turns the data line on for a development run; `--plugin-root <folder>` is what the plugin passes, and with it he also leaves on his own when no session is left. One copy runs at a time: a second start exits silently.

## Language

The menu follows your macOS language: English or Spanish, English when yours is neither.

## Limits

Whether a transparent window shows no frame, whether clicks pass through the empty parts and whether he stays over full-screen Spaces depend on your macOS version, and only a run on your Mac tells. A probe log at `~/Library/Logs/BroteWindow/probe.log` records what the window sees. No CPU or memory figure is promised here.

## Files

- `brote.swift`: the whole app. A core layer (numbers, placement, physics, pokes, the data line, sessions, `--selftest`) and an AppKit shell.
- `sprites.js`, `say.js`, `personality.js`, `director.js`, `menu.json`, `book-icon.json`: his pixels, his words, his moods and the menu title, copied from the plugin so they cannot drift from it.
- `Info.plist`, `build.sh`, `build-universal.sh`, `.gitignore`.
