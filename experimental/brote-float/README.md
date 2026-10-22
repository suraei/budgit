# Brote, floating window

**Experimental. macOS only.** This is Brote, the pet of the Budgit plugin, as a small window that floats over everything. It is source code that you build on your own Mac. Nothing in it is needed for the plugin to work.

He sits near Claude's chat bar on every Space. You can drag him, throw him and poke him. With the data line on, his body shows how your week is going (mood, sweat, a stale marker). He has no text and no card above him.

## Build it

You need macOS 13 or newer and Apple's command line tools (`xcode-select --install`). Then, from this folder:

```bash
./build.sh
```

`build.sh` compiles `brote.swift` with `swiftc`, signs it ad hoc and puts `Brote.app` in `~/Library/Application Support/BroteWindow/` (set `BROTE_OUT` to an absolute path to put it somewhere else). It uses no network and no Xcode project, and it installs nothing else. On any other system it stops with one line.

## Run it

Run the app by its path, from a Terminal tab. Do not double-click it or use `open`:

```bash
"$HOME/Library/Application Support/BroteWindow/Brote.app/Contents/MacOS/Brote" --no-data
```

`--no-data` shows Brote without your week. To turn the data line on, pass `--hub <folder>`: a **clone of the Budgit repo**, the folder that holds `plugin/bin/budgit`. A marketplace install is not a clone, so clone the repo first. `build.sh` prints the right command when it finds that folder next to the source. Without `--hub` the data line stays off.

Every 30 seconds the app runs `/usr/bin/python3 -B <folder>/plugin/bin/budgit status --brote`. It starts no shell, runs from `/`, reads only, and stops the call after 5 seconds. It uses the script only when you own it and nobody else can write it. It keeps the last answer on your Mac, as the fields of the status line and never your ledger text.

To quit, right-click him and choose "Quit Brote", or run `pkill -x Brote`. Closing the Terminal tab ends him too.

## Language

The menu follows your macOS language: English or Spanish, English when yours is neither.

## Limits

This is early. Whether a transparent window shows no frame, whether clicks pass through the empty parts and whether he stays over full-screen Spaces depend on your macOS version, and only a run on your Mac tells. A probe log at `~/Library/Logs/BroteWindow/probe.log` records what the window sees. No CPU or memory figure is promised here.

## Files

- `brote.swift`: the whole app. A core layer (numbers, placement, physics, pokes, the data line, `--selftest`) and an AppKit shell.
- `sprites.js`, `say.js`, `menu.json`: his pixels, his words and the menu title, copied from the plugin so they cannot drift from it.
- `Info.plist`, `build.sh`, `.gitignore`.
