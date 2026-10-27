# Layer 2, Spanish: word hygiene (UNREVIEWED by the user)

<!-- humanizelint: off (this page quotes the patterns it forbids) -->

Every pattern below is a draft. It starts from the Spanish UI addendum already marked DRAFT in `lenses/writing.md` plus a few
common calques. Ask the user once per project before treating a Spanish hit as a defect. The IdeaLens study
(arXiv 2610.06778, Table 29) evaluated Spanish and found no Spanish false flags on human text, but it tested ideas, not wording,
so layer 1 applies in Spanish and this list is only house style.

## Lint ids (EN rules W1-W6 have the same meaning)
| Id | What | Examples | Fix |
|----|------|----------|-----|
| W1 | Calques of English marketing | "lleva tu proyecto al siguiente nivel", "desbloquea", "en el mundo actual" | Say what it does |
| W2 | Empty intensifiers and announcers | "realmente", "literalmente", "cabe destacar", "es importante señalar" | Delete |
| W3 | Bureaucratic phrasing | "se ha procedido a", "realización de la gestión" | A verb with a subject |
| W4 | "No es X, es Y" contrast | "No es una herramienta, es un compañero." | Say Y |

## Judgement-only, no lint (from the lens draft)
- Gerund chains ("Pulsando aquí, accediendo a…"); noun piles; passive "se ha procedido".
- One register per product, tú or usted, never mixed.
- Labels in sentence case ("Guardar cambios", not "Guardar Cambios").
- Spain Spanish unless the project says otherwise ("ordenador", "móvil"); numbers and dates with `Intl` (1.234,5 · 19/10/2026).

## Not applied in Spanish
- The em dash flag (W6): the raya is normal Spanish punctuation.
- Passive-voice and interrogative-opener bans: same reasons as English (`layer2-en.md`).

## Layer 1 in Spanish
H4, H5, H6, H8 and H11 have Spanish word lists in `plugin/scripts/humanizelint.py` (also UNREVIEWED). H8 note: "nunca" and "siempre" are allowed only for a guarantee someone has verified, with the check named and the hit marked `ok`. "De siempre" (meaning "the usual") is not flagged.
