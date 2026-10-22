# Extended brain (`budgit brain`) — reference

Notes (research, lessons, docs, radar) link to each other with Obsidian-style `[[links]]`; a few links carry
a TYPE so the team can ask "is this note still current?" and "what depends on it?" without reading everything.
Read-only: no `budgit brain` command ever edits a note. Opt-in per project: `.budgit/project.json` →
`"brain": {"roots": ["research", "lessons", "docs", "radar"]}` turns on the edit hook and the session pointer.

## Syntax
- A note's id is its filename stem (`research/2026-01-15-example-topic.md` → `[[2026-01-15-example-topic]]`).
  Also valid: `[[folder/stem]]`, `[[stem|label]]`, `[[stem#heading]]`, any case, and frontmatter `aliases:`
  (`[a, b]` or a `- a` block list) or `id:` (lessons: `[[L-002]]`).
- Typed relations live ONLY in one `## Relations` section, one per line:
  `- <type> [[target]] (short reason)`. Anywhere else, `[[x]]` is a plain link.
- Types (closed): `supersedes` (this note replaces the target; the target gets `status: superseded`),
  `depends_on`, `derived_from`, `implements`, `supports`, `contradicts`. Inverses are computed, never written.
- Not allowed: embeds `![[x]]`, block refs `[[x#^id]]`, `- depends_on [[x]]` outside `## Relations`, a second
  `## Relations`. Links inside code or frontmatter are ignored.

## Order of use (cheapest first)
1. `lessons/INDEX.md` and the folder you need → 2. `grep` → 3. on the 1–3 notes you will rely on:
   `budgit brain current <note>` (is there a newer one?) and `budgit brain impact <note>` (what breaks if it
   changes; 2 hops) → 4. read 2–3 notes. `budgit brain backlinks <note>` lists who cites it;
   `budgit brain index [folder]` prints a listing (stdout only; hand-made INDEX.md files are never overwritten).
- When a new note replaces an old one: add `- supersedes [[old]] (why)` to the new note and set the old note's
  `status: superseded`. When a plan/decision builds on research: `derived_from` / `implements`.

## Lint
`budgit brain lint [--note PATH] [--verbose]` — ERROR (exit 1): unknown type, unresolved or ambiguous target,
self-edge, relation outside `## Relations`, second `## Relations`, supersedes cycle, superseded target without
`status: superseded`, embed, block ref, non-NFC filename, unclosed frontmatter, stem/alias collision
(INDEX/README exempt). WARN (count only; `--verbose` lists): no frontmatter, orphan, Dataview `key::` field.
Editing a note inside a brain root runs lint on that note and shows only its errors; full lint runs in
`verify.checks` (renames/deletes break other notes).

## Measurement (until 2026-11-02)
Every query appends to gitignored `.budgit/brain-queries.jsonl` (ts, session, sub, args, n, bytes, redirected).
Keep the graph commands only if `current` prevented at least one stale answer; otherwise keep lint + syntax.

## Obsidian (optional viewer)
Settings → Files and links: "Use [[Wikilinks]]" ON, "New link format" = "Shortest path when possible",
"Automatically update internal links" OFF (it rewrites notes behind the team's back). `.obsidian/workspace*.json`
and `.trash/` are gitignored.
