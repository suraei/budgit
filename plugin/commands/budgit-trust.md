---
description: Budgit's standing yes for this project. Type "/budgit-trust work" to turn it on, "/budgit-trust revoke" to turn it off.
argument-hint: work | revoke | status
allowed-tools: Bash(budgit trust status:*)
disable-model-invocation: true
---
The user's own message may have just changed this setting: a hook reads exactly `/budgit-trust work` and
`/budgit-trust revoke` before this turn. Do nothing else. Run `budgit trust status` and show its output as it is.
Never run `budgit trust work` yourself: a command from you cannot turn trust on, and trying is a mistake.
