#!/usr/bin/env python3
"""Status line for Claude Code: shows context/limits and feeds the budgit budget (zero tokens).

Claude Code pipes a JSON object on stdin; `rate_limits` is present for Pro/Max subscribers after the
first API response. Never raises: a broken status line must not disturb a session.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def main():
    try:
        d = json.load(sys.stdin)
        import budget
        rl = d.get("rate_limits") or {}
        # R-525: resets_at is optional (and five_hour is absent on Ultra); a window with no reset time still counts
        pair = lambda k: ((rl[k]["used_percentage"], rl[k].get("resets_at"))
                          if isinstance(rl.get(k), dict) and rl[k].get("used_percentage") is not None else None)
        seven, five = pair("seven_day"), pair("five_hour")
        try:      # R-1378: the per-session usage log; both keys are optional and a failure is never fatal
            model = d.get("model")
            feed = [(k, rl[k].get("used_percentage"), rl[k].get("resets_at")) for k in ("five_hour", "seven_day") if isinstance(rl.get(k), dict)]
            budget.note_session_feed(d.get("session_id"), model.get("id") if isinstance(model, dict) else None, feed, "statusline")
        except Exception:
            pass
        old = budget.load().get("limits", {})
        changed = any(v is not None and (old.get(k) or {}).get("used") != float(v[0])
                      for k, v in (("seven_day", seven), ("five_hour", five)))
        if changed:
            budget.record(seven_day=seven, five_hour=five, source="statusline")
        parts = []
        ctx = (d.get("context_window") or {}).get("used_percentage")
        if ctx is not None:
            parts.append("ctx %d%%" % ctx)
        if five:
            parts.append("5h %d%%" % five[0])
        if seven:      # R-1309: the weekly figure shown is the one Budgit keeps (the merge), never the stdin value as it came
            kept = (budget.load().get("limits") or {}).get("seven_day")
            if isinstance(kept, dict) and kept.get("used") is not None:
                parts.append("wk %d%%" % kept["used"])
        if seven or five:
            parts.append(budget.label(budget.current()["level"]))
        sys.stdout.write(" · ".join(parts))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
