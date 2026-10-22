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
        pair = lambda k: ((rl[k]["used_percentage"], rl[k]["resets_at"])
                          if isinstance(rl.get(k), dict) and "used_percentage" in rl[k] and "resets_at" in rl[k] else None)
        seven, five = pair("seven_day"), pair("five_hour")
        old = budget.load().get("limits", {})
        changed = any(v is not None and (old.get(k) or {}).get("used") != float(v[0])
                      for k, v in (("seven_day", seven), ("five_hour", five)))
        if changed:
            budget.record(seven_day=seven, five_hour=five)
        parts = []
        ctx = (d.get("context_window") or {}).get("used_percentage")
        if ctx is not None:
            parts.append("ctx %d%%" % ctx)
        if five:
            parts.append("5h %d%%" % five[0])
        if seven:
            parts.append("wk %d%%" % seven[0])
        if seven or five:
            parts.append(budget.label(budget.current()["level"]))
        sys.stdout.write(" · ".join(parts))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
