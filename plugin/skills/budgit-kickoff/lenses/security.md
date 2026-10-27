# Lens: security
- Every diff: `/security-review` (if not installed: read the diff for injection, secrets and unsafe input, and quote the lines) (+ security-guidance hooks already on). Before release: Anthropic
  `claude-security` plugin (verifiers try to disprove each finding); deterministic SAST/supply chain:
  Trail of Bits static-analysis / supply-chain skills; gitleaks (`--redact`, local) + `osv-scanner --offline`
  CLIs. Enable per project. Not trivy: its releases/actions were backdoored 2026-03-19 (CVE-2026-33634); not
  TruffleHog by default (verifies secrets against live APIs).
- `budgit:security-reviewer` (HOLD/PASS gatekeeper; runs `budgit secrets` + `audit-mcp`): before a release, when a
  dependency is added, and BEFORE installing any skill/plugin/MCP. Opt-in commit guard: `budgit secrets --install-hook`.
- Apps that move money (trading, payments): nothing goes live on model confidence. Default paper/dry-run;
  promotion needs a replay result, a data-freshness bound, explicit risk limits and the user's approval, recorded in
  the ledger.
- Secrets never in tracked files; personal/diet/health data = special category (GDPR art. 9).
- A read-only or security brief forbids commands that raise an operating-system password or permission prompt (keychain reads, `sudo`, system settings).
