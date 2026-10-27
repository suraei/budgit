#!/bin/sh
# Brote's event channel. Tells the floating Brote what Claude is doing, so his face can be Claude's face.
# One line per hook event appended to a small file that only the running Brote app creates:
#   <kind> <first 12 characters of the session id> [<tool name>]        kind: prompt pre post fail stop perm
#   apifail <first 12 characters of the session id> <class>             Claude stopped on an API error (StopFailure); class: one fixed word
# Nothing else is written: no prompt text, no tool input, no paths, no output, no error message (only the class, from a fixed list). No network, no child process, no state.
# If the file is not there (the app is not running) it does nothing. Whatever goes wrong, it exits 0 and says nothing,
# so it can never block, slow down or change a tool call. Usage (hooks.json): sh brote-event.sh <kind>   (the hook's JSON arrives on stdin)
f="$HOME/Library/Application Support/BroteWindow/events.log"
[ -f "$f" ] || exit 0
kind=$1
case "$kind" in prompt|pre|post|fail|stop|perm|apifail) ;; *) exit 0 ;; esac
# a file nobody is reading (the app was killed) must not grow for ever: the app trims it when it runs; once per turn (prompt, stop) this
# looks at the size and stops appending above 256 KB. The check costs one child process, so it is not made on every tool call.
case "$kind" in prompt|stop|apifail) [ "$(wc -c < "$f")" -le 262144 ] 2>/dev/null || exit 0 ;; esac
IFS= read -r line || true
rest=${line#*\"session_id\":\"}
[ "$rest" != "$line" ] || exit 0
sid=${rest%%\"*}
case "$sid" in ''|*[!A-Za-z0-9_-]*) exit 0 ;; esac
if [ ${#sid} -gt 12 ]; then tail12=${sid#????????????}; sid=${sid%"$tail12"}; fi
tool=
case "$kind" in pre|post|fail)
  rest=${line#*\"tool_name\":\"}
  if [ "$rest" != "$line" ]; then
    tool=${rest%%\"*}
    case "$tool" in *[!A-Za-z0-9_]*) tool= ;; esac
    [ ${#tool} -le 48 ] || tool=
  fi ;;
esac
cls=
case "$kind" in apifail)
  cls=unknown
  rest=${line#*\"error\":\"}
  if [ "$rest" != "$line" ]; then
    v=${rest%%\"*}
    case "$v" in rate_limit|overloaded|authentication_failed|oauth_org_not_allowed|account_on_hold|billing_error|invalid_request|model_not_found|server_error|max_output_tokens|cloud_credential_error) cls=$v ;; esac
  fi ;;
esac
printf '%s %s%s\n' "$kind" "$sid" "${tool:+ $tool}${cls:+ $cls}" >> "$f" 2>/dev/null
exit 0
