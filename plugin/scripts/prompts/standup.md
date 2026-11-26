[budgit-job] Budgit daily stand-up. You run headless: you have no session tools, so you cannot wake, message or close any session. Do ledger work only.

1. Run `budgit portfolio` to list the registered projects. For each project, in its ledger: merge duplicate items (fold, never drop), order open items with bugs first, and list yesterday's incidents.
   Report closed items from each project's `budgit status` lines (verify state and outcome plus proof); an item whose line says "outcome missing" or "superseded" is not a success claim.
2. Find sessions by their title when the host gives you a list; never rely on a typed session id.
3. Write one digest file at `<job folder>/standup/<today's date>.md` (your working folder; create the standup folder): per project, the count of open items, the created-vs-closed line of the last 3 days (the "created vs closed" line of `budgit status`), the order you set, and the incidents. The next session start shows it.
4. Under a "Waste" heading of the digest, copy this line verbatim. Budgit computed it from each active project's timelines.jsonl (last 24 h); do not change the numbers: <timelines>
5. Stay inside each project's own Budgit data folder and the digest folder.

Waking project leads, closing quiet lanes and messaging leads run only through the desktop scheduler, where a full session has the tools. They are not part of this run (UNTESTED).
