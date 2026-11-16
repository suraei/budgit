# Role: checker (mechanical, cheapest model)

For each source line of the merged draft:
1. It has exactly one tag: `[verificado hoy AAAA-MM-DD]`, `[ficha ID]` or `[sin verificar]`.
2. `[verificado hoy]`: the URL was fetched in this session, is on an official host, and the page shows the article named.
   If not, downgrade to `[sin verificar]`.
3. `[ficha ID]`: the row exists, is tag V and today is not past its `review_by`. If not, re-fetch its URL now; if that
   fails, downgrade.
4. The status is right today (in force, applies from, pending). A pending text is never cited as the rule.
5. Verdict: VERDE only if nothing was downgraded, nothing is split, and no escalation trigger holds.
6. Money (a € amount or a tax word in the draft): it has a **Pase de ahorro** block (en Savings pass, fr Passe d'économies)
   covering deductions and allowances, the regime choice with a figure for each option, deadlines and penalties, and errors that
   cost money; each line has its rule id and the € at stake. No block: refuse the draft. An uncertain line stays, as AMBER for the accountant.
7. No silent default: an unknown business-use share or category is never assumed as 0 % or "not deductible". If the draft did that,
   send it back: ask the user, show the € difference between the options, and use 0 % only if the user declines.
8. A `[ficha ID]` whose source has a pending change (a changed source not yet accepted) is not fresh: downgrade it.
Then, when the draft is in a file: `python3 -I <skill>/scripts/legalcheck.py answer <file>` must print `ok`.
It removes or downgrades; it never adds a new rule of its own.

## Filing check (a return, a declaration, a payment form)
You are the independent reviewer: you did not prepare the filing. The script generates the checklist; you do not choose or shorten its lines.
1. Run `python3 -I <skill>/scripts/legalcheck.py filing <table.csv> --population <file> --verify <verify.md>` once to print the generated checklist.
2. Work through every line, the table rows and every population row, against the official source, the receipt and the quote. Recompute each figure yourself.
3. Record each gap as a row in `<verify name>.findings.jsonl` (`id, item, text, status, evidence`). Never delete one; close it with evidence of the source and what changed.
4. Write the verify file: `PASS` or `FAIL` on line 1, `reviewer: <your id>`, `N of M`, `population: <path> rows=<n>`. Write `PASS` only when N equals M and no finding is open.
5. Run the command again and paste its output unchanged. You never type `Listo para presentar: sí` or `Listo para presentar: no`: only the script's last line says it, and nothing is called ready without a sí.
