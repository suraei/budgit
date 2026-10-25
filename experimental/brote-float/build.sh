#!/bin/bash
# Builds Brote.app from brote.swift with swiftc only. No network, no elevated rights.
# The bundle goes OUTSIDE ~/Documents (iCloud's File Provider stamps Finder metadata on anything made there, and codesign refuses it).
set -euo pipefail
export PATH=/usr/bin:/bin
[ "${BROTE_FORCE_OS:-$(uname -s)}" = Darwin ] || { echo "Brote's floating window is macOS only." >&2; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Needs Apple's command line tools (swiftc). Run: xcode-select --install" >&2; exit 1; }
cd "$(dirname "$0")"
hub=""; d=".."                                  # the folder that holds plugin/bin/budgit: at most 3 levels up
for _ in 1 2 3; do [ -f "$d/plugin/bin/budgit" ] && { hub=$(cd "$d" && pwd -P); break; }; d="$d/.."; done
out="${BROTE_OUT:-$HOME/Library/Application Support/BroteWindow}"
case "$out" in /*) ;; *) echo "BROTE_OUT must be an absolute path" >&2; exit 1 ;; esac
# the only thing removed is "$out/Brote.app" (the next lines); nothing else under $out is touched
app="$out/Brote.app"
rm -rf -- "$app" && mkdir -p -- "$app/Contents/MacOS" "$app/Contents/Resources" build/ModuleCache
swiftc -O -swift-version 5 -module-cache-path build/ModuleCache brote.swift -o "$app/Contents/MacOS/Brote" \
  -framework AppKit -framework QuartzCore -framework JavaScriptCore
cp Info.plist "$app/Contents/Info.plist"
cp sprites.js say.js personality.js director.js menu.json book-icon.json "$app/Contents/Resources/"
shasum -a 256 brote.swift | cut -c1-12 > "$app/Contents/Resources/build.txt"   # which source built this bundle; START in probe.log prints it
codesign --force --sign - "$app"
codesign --verify --strict "$app"
echo "built $app"
echo "run (stage 1, data line off): \"$app/Contents/MacOS/Brote\" --no-data"
if [ -n "$hub" ]; then echo "run (stage 2, data line on):  \"$app/Contents/MacOS/Brote\" --hub \"$hub\""
else echo "data line: pass --hub <a clone of the Budgit repo> (no plugin/bin/budgit within 3 folders above here)"; fi
