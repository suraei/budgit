# Ledger reference (plugin `budgit`)

Binding is PER SESSION: first action of every session on a project → `budgit activate <dir>`.
Every message the user sends (mid-turn ones and the user's answers to your questions included) lands in
`.budgit/inbox.jsonl`; a turn cannot end while one of this session's inputs is untracked.

| Command | Use |
|---|---|
| `budgit ledger add --src I-n [--phase P]` | new request — copies the user's literal words |
| `budgit ledger link R-n --src I-n` | refines an existing item |
| `budgit ledger ack I-n [I-m …] --note "why"` | approvals/"sí"/"continua" — not new requests |
| `budgit ledger phase mvp\|later R-n [R-m …]` | MVP first: tag the MVP items; park the rest until it ships (`add --phase later` for new ideas) |
| `budgit ledger done R-n --evidence "<proof>"` | close with proof |
| `budgit ledger verify R-n` | needs `.budgit/verify/R-n.md` starting with PASS (written by `budgit:verifier`) |
| `budgit ledger drop R-n --by-user I-n` | only when the user asked; cite the user's input |
| `budgit ledger start R-n [--take]` | claim the item as `doing` for THIS session (id from `CLAUDE_CODE_SESSION_ID`); a live other session blocks it unless `--take`. `ledger set R-n doing` routes here |
| `budgit status` / `budgit ledger list --open` | what is pending (doing items show `live`, `idle` or `unclaimed`) |
| `budgit status --brote` | the JSON Brote draws from (state from the weekly projection, counts, doing/queue/later, stale) |
| `budgit new <slug> [--rigor mini\|standard]` | create project folder + git (noreply) + `.budgit/` + hub card |
| `budgit idea "<text>"` | park an idea in the hub (`ideas/IDEA-NNN-*.md`, status inbox) |
| `budgit image "<prompt>" --name <file>` | generate ONE image via the official Codex CLI on the ChatGPT lane (if set up) → `assets/generated/<file>.png` + `MANIFEST.jsonl` (prompt, sha256, tokens, time). Never ask the user to generate images by hand |
| `budgit prepare` | fresh state before verifying (commands approved by the user at a terminal: `--approve`; the error prints the exact command to show in a bash block) |

A claimed item leaves `doing` when its session closes (SessionEnd releases it; `/clear` and `resume` release nothing, and a
resumed session claims again what its own end released). A session that died without the hook is found by the next
SessionStart, which reopens its claims and writes one progress line. Idle claims (no prompt for 12 h) are shown and kept;
legacy `doing` items with no owner are never reopened by a sweep. A `doing` item not updated for more than 7 days is *stale*:
the notebook lists it first and Brote points at it (`budgit status --line --extras` adds `· N estancadas` to the status line; off by default).

Never edit `.budgit/ledger.json` by hand (`accept-edit` exists for real accidents and is shown to the user).
`.budgit/ledger.md` WITHOUT `.budgit/ledger.json` = hand-kept v0 ledger: don't activate/migrate it —
the Budgit hub session does (tell the user). Session already full of covered messages → `--since-now`.
No `budgit` command (plugin not loaded) → hand-kept table `ID | input (literal) | status | evidence`
+ `Decisions` table (ID, question, literal answer, consequence, R-items refined), and say so.
