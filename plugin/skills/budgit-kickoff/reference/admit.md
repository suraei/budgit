# budgit admit — may the machine take one more lane or suite? (R-381)

```
budgit admit [lane|suite] [--json]     # one line; exit 0 go, 1 wait, 2 refuse
```

No model, no network: it reads the 1-minute load average, the core count, free memory and how many Claude sessions are busy
this minute (the live sessions Claude Code itself lists, status busy), and answers with the reason in one line.

| Reading | Answer |
| --- | --- |
| load1 above twice the cores | refuse |
| lane only: free memory under 1536 MB | refuse |
| lane only: 6 or more sessions busy (a PM, the TM and four lanes) | refuse |
| load1 above the cores | wait |
| otherwise | go |

Refusals are checked before waits. A reading that cannot be taken is skipped, never guessed. `suite` looks at the load only.

## Who asks
- **`budgit suite` and `budgit main-check`** ask with `suite` after taking the machine-wide suite lock. Not go: exit 4 with the
  reason; with `--wait` they recheck every 30 s instead.
- **PMs and the TM** run `budgit admit lane` before `start_session`; on 1 they wait, on 2 they do not start the lane.
- **The Agent PreToolUse hook** adds one line of advice to an Agent call when the answer is not go. It never blocks a call.
  `BUDGIT_ADMIT_ADVICE=off` silences it.

## Thresholds
In the Budgit home `config.json`, all optional:

```json
{"admit": {"wait_load": 8, "refuse_load": 16, "min_free_mb": 1536, "max_busy": 6}}
```

Defaults: `wait_load` = the cores, `refuse_load` = twice the cores. `BUDGIT_SUITE_LOAD_LIMIT` (tests, odd machines) sets both load
thresholds at once.

## Fair share across projects (R-402)
`budgit admit lane --project DIR` (the project defaults to the current one) adds the share of the project to the machine's readings. A lane is a busy session attributed to the project whose title is not a PM or TM chat; a project has pending work when its ledger has an open or doing item.
- A project with no lane always gets its first one, whatever the load and the busy count; only a machine out of memory refuses it.
- A second lane needs a load at or under the waiting limit. Above it, the project with the most lanes is the one that yields first (the reason says so); park a lane that can wait.
- A third lane also needs every other project with pending work to hold a lane (the reason says how many do not).
- The refusal limits still apply to a project that holds a lane. `--json` carries `share` and `lane_cap`: how many lanes the project may hold now (1 above the waiting limit, 2 while another project has none, else bounded only by memory and the busy limit). Spare capacity is lent while nobody needs it and taken back as soon as somebody does, because every answer is computed from the live counts.
- Suites are queued in the same spirit: see "Fair order" in `main-check.md`.
