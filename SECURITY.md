# Security Policy

Mandata is a *library*, not a payment processor — it handles mandates,
credentials, and evidence records, never funds or card data.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ |
| 0.1.x   | ❌ |

## Reporting a vulnerability

**Do not open a public issue for security reports.**

Email **shedrack.aji@doztra.ai** with:

- description of the vulnerability
- steps to reproduce / proof of concept
- affected version(s)

We aim to acknowledge within 48 hours and provide a remediation timeline within 7 days.

## Threat model notes

Relevant attack surfaces for this project: credential forgery, mandate replay,
time-window manipulation, evidence-chain tampering, and delegation-depth abuse.
If your report touches these, say so explicitly — it speeds triage.
