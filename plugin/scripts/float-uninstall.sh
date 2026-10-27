#!/bin/sh
# Removes Brote's floating window completely, using nothing but /bin/sh: the app, its settings, logs and session files.
# Run it when the plugin is already gone:  sh float-uninstall.sh      (the plugin's own way is: budgit brote uninstall)
# It removes exactly the paths below, never follows a link out of them, and touches nothing else. The one file it leaves is
# BroteWindow/float.json saying he is off: without it the next Claude session would turn him on again.
set -u
export PATH=/usr/bin:/bin

case "${LANG:-}" in es*) es=1 ;; *) es=0 ;; esac
say() { if [ "$es" = 1 ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

home=${HOME:-}
case "$home" in
  /*) ;;
  *) say "HOME is not a folder path; nothing was done." "HOME no es una ruta de carpeta; no se hizo nada."; exit 1 ;;
esac
id=es.budgit.brote.float
bw="$home/Library/Application Support/BroteWindow"

# 1. quit the running app: it is the process that holds brote.lock open (lsof) and runs this folder's binary, as this user
if [ ! -L "$bw" ] && [ -d "$bw" ] && [ -f "$bw/brote.lock" ] && [ ! -L "$bw/brote.lock" ]; then
  pid=$(head -c 32 "$bw/brote.lock" 2>/dev/null | tr -dc '0-9')
  if [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null; then
    holders=$(/usr/sbin/lsof -t -- "$bw/brote.lock" 2>/dev/null)
    line=$(ps -o uid=,args= -p "$pid" 2>/dev/null)
    uid=$(/usr/bin/id -u)
    case " $(echo $holders) " in
      *" $pid "*)
        case "$line" in
          *"$bw/Brote.app/Contents/MacOS/Brote"*)
            set -- $line
            if [ "$1" = "$uid" ]; then
              kill "$pid" 2>/dev/null
              n=0; while [ "$n" -lt 30 ] && kill -0 "$pid" 2>/dev/null; do sleep 0.1; n=$((n + 1)); done
              kill -9 "$pid" 2>/dev/null
            fi ;;
        esac ;;
    esac
  fi
fi

# 2. the system's own copy of the settings (after the app is quit, or it would write them again) and the Launch Services entry
#    (while the app still exists to be named): only for the real home of this user
real=$(/usr/bin/dscl . -read "/Users/$(/usr/bin/id -un)" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')
if [ -n "$real" ] && [ "$real" = "$home" ] && [ -x /usr/bin/defaults ]; then
  ls=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  [ -x "$ls" ] && "$ls" -u "$bw/Brote.app" >/dev/null 2>&1
  /usr/bin/defaults delete "$id" >/dev/null 2>&1
fi

# 3. remove exactly what this feature wrote
if [ -L "$bw" ]; then
  say "BroteWindow is a link, not a folder; it was left alone (remove the link yourself if it is yours)." \
      "BroteWindow es un enlace, no una carpeta; se dejó como estaba (quita el enlace tú si es tuyo)."
else
  # nothing was ever written, but he must not start by himself after this: the one file left says so
  [ -e "$bw" ] || (umask 077; mkdir -p "$bw") 2>/dev/null
fi
if [ -d "$bw" ] && [ ! -L "$bw" ]; then
  for name in Brote.app Brote.app.prev Brote.app.sha256 Brote.app.sha256.new float.json brote.lock ensure.lock ensure.log events.log probe.log sessions; do
    rm -rf -- "$bw/$name"            # a link here is removed itself, never followed
  done
  for stage in "$bw"/.stage-* "$bw"/float.json.tmp* "$bw"/ensure.log.tmp*; do
    if [ -e "$stage" ] || [ -L "$stage" ]; then rm -rf -- "$stage"; fi
  done
  # the one file left says he is off, so a plugin update does not bring him back by himself ("budgit brote on" does)
  (umask 077; printf '{"float": "off", "removed": %s}\n' "$(date +%s)" > "$bw/float.json") 2>/dev/null
fi

rm -rf -- "$home/Library/Logs/BroteWindow" "$home/Library/Preferences/$id.plist" "$home/Library/Saved Application State/$id.savedState"

say "Brote's floating window is removed: its app, settings, logs and session files are gone. It will not come back by itself." \
    "La ventana flotante de Brote está quitada: su app, ajustes, registros y ficheros de sesión ya no existen. No volverá sola."
exit 0
