#!/bin/bash
# Builds the prebuilt Brote.app (arm64 + x86_64) and its MANIFEST.sha256 into $BROTE_OUT, reproducibly: the same sources with the same
# toolchain give byte-identical slices from any folder and any $HOME (tests/test_float_build.py checks it). swiftc only; no network.
# It writes only "$BROTE_OUT/Brote.app", src/, BUILD-INFO.txt and MANIFEST.sha256 (src/ = the sources this bundle is built from, so the
# folder can rebuild itself), and never the installed copy under Application Support.
set -euo pipefail
export PATH=/usr/bin:/bin LC_ALL=C
[ "${BROTE_FORCE_OS:-$(uname -s)}" = Darwin ] || { echo "Brote's floating window is macOS only." >&2; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Needs Apple's command line tools (swiftc). Run: xcode-select --install" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd -P)
out="${BROTE_OUT:-}"
case "$out" in /*) ;; *) echo "BROTE_OUT must be an absolute path (the folder that will hold Brote.app and MANIFEST.sha256)" >&2; exit 1 ;; esac
case "$out/" in */../*|*/./*) echo "BROTE_OUT must not contain . or .. parts" >&2; exit 1 ;; esac
[ "$out" != "$HOME/Library/Application Support/BroteWindow" ] || { echo "BROTE_OUT must not be the installed folder; the installed copy is made by: budgit float on" >&2; exit 1; }
mkdir -p -- "$out"
out=$(cd "$out" && pwd -P)
work=$(mktemp -d "${TMPDIR:-/tmp}/brote-build.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
export SOURCE_DATE_EPOCH=1700000000 ZERO_AR_DATE=1
build_slice() {   # $1 = arch. Each slice builds in its own folder; the folder name is mapped away so no path lands in the binary
  local arch=$1 w="$work/$1"
  mkdir -p "$w/mc" "$w/src"
  cp "$here/brote.swift" "$w/src/brote.swift"
  ( cd "$w/src" && swiftc -O -swift-version 5 -target "$arch-apple-macos13.0" -module-cache-path "$w/mc" \
      -file-prefix-map "$work=/build" -debug-prefix-map "$work=/build" brote.swift -o "$w/Brote" \
      -framework AppKit -framework QuartzCore -framework JavaScriptCore -Xlinker -reproducible )
}
build_slice arm64 > "$work/arm64.log" 2>&1 & p1=$!
build_slice x86_64 > "$work/x86_64.log" 2>&1 & p2=$!
ok=0
wait $p1 || { ok=1; cat "$work/arm64.log" >&2; }
wait $p2 || { ok=1; cat "$work/x86_64.log" >&2; }
[ $ok = 0 ] || { echo "swiftc failed" >&2; exit 1; }
# The app is assembled and signed in the private work folder, then copied: a synced output folder (iCloud Documents) puts Finder and
# file-provider attributes on the bundle folder, and codesign refuses to sign a bundle that carries them.
app="$work/Brote.app"
mkdir -p -- "$app/Contents/MacOS" "$app/Contents/Resources"
lipo -create "$work/arm64/Brote" "$work/x86_64/Brote" -output "$app/Contents/MacOS/Brote"
cp "$here/Info.plist" "$app/Contents/Info.plist"
cp "$here/sprites.js" "$here/say.js" "$here/personality.js" "$here/director.js" "$here/menu.json" "$here/book-icon.json" "$app/Contents/Resources/"
shasum -a 256 "$here/brote.swift" | cut -c1-12 > "$app/Contents/Resources/build.txt"
codesign --force --sign - --timestamp=none "$app"
codesign --verify --strict --deep "$app"
# the only things removed are these four paths under the output folder
rm -rf -- "$out/Brote.app" "$out/src" "$out/MANIFEST.sha256" "$out/BUILD-INFO.txt"
ditto --noextattr --noqtn --norsrc "$app" "$out/Brote.app"
app="$out/Brote.app"
{
  echo "source brote.swift sha256 $(shasum -a 256 "$here/brote.swift" | cut -d' ' -f1)"
  echo "target arm64-apple-macos13.0 x86_64-apple-macos13.0"
  echo "swiftc $(swiftc --version 2>&1 | tr '\n' ' ' | sed 's/ *$//')"
  echo "sdk $(xcrun --show-sdk-version) build $(xcrun --show-sdk-build-version)"
  echo "ld $(ld -v 2>&1 | head -1)"
  echo "flags -O -swift-version 5 -file-prefix-map -debug-prefix-map -Xlinker -reproducible; SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH ZERO_AR_DATE=1"
} > "$out/BUILD-INFO.txt"
codesign --verify --strict --deep "$app" || echo "note: $app carries attributes added by this folder (a synced one?); the signature is intact in the work copy" >&2
mkdir -p -- "$out/src"
for f in brote.swift Info.plist sprites.js say.js personality.js director.js menu.json book-icon.json build-universal.sh; do cp "$here/$f" "$out/src/$f"; chmod 644 "$out/src/$f"; done   # only the app binary runs
mod="$here/../../scripts/floatmanifest.py"                            # the plugin layout: <plugin>/float/src next to <plugin>/scripts
[ -f "$mod" ] || mod="$here/../../../plugin/scripts/floatmanifest.py"   # the development layout: tools/brote/float
/usr/bin/python3 "$mod" write "$out"
echo "built $app ($(lipo -archs "$app/Contents/MacOS/Brote"))"
