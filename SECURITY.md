# Security Policy

VoiceGateway handles provider API keys, conversation transcripts, and
cost data. We take security seriously and appreciate responsible
disclosure of vulnerabilities.

## Supported versions

VoiceGateway is on the v0.x track and follows semantic versioning. While
v0.x is pre-1.0, only the latest minor receives security fixes.

| Version | Supported |
|---|---|
| Latest minor release | ✓ |
| All earlier minors | ✗ |

When v1.0.0 ships, the support window expands to the two most recent
minor releases. The
[CHANGELOG](CHANGELOG.md) tracks the current latest release.

## Reporting a vulnerability

**Please do not file public GitHub issues for security vulnerabilities.**

Use either channel below:

1. **Preferred: GitHub Security Advisory.** Open a private report at
   <https://github.com/mahimailabs/voicegateway/security/advisories/new>.
   GitHub will route it directly to the maintainers and keep the
   discussion confidential until a fix lands.
2. **Email.** Send the report to `mahimairaja3@gmail.com` with the
   subject prefix `[voicegateway-security]`. PGP key on request.

Include in your report, where applicable:

- A clear description of the issue and the security impact.
- The affected version (`voicegw --version`) and platform.
- Steps to reproduce, or a minimal proof of concept.
- Suggested remediation, if you have one.

## Response timeline

We commit to a best-effort response within 7 days. Realistic targets:

| Stage | Target |
|---|---|
| Acknowledge receipt | Within 7 days |
| Triage and initial assessment | Within 14 days |
| Patched release or mitigation | Within 30 days for high/critical issues; longer for lower-severity issues that need careful design |

VoiceGateway is maintained by a small team, so timelines are best-effort
rather than contractual. If a vulnerability is being actively exploited
and you have not heard back within the acknowledgement window, please
escalate by re-sending to the maintainer email above with the subject
prefix `[voicegateway-security URGENT]`.

## Coordinated disclosure

We follow responsible-disclosure practice:

- Reporters and maintainers agree on a disclosure date before public
  details are shared.
- Credit is given to the reporter in the release notes and the
  CHANGELOG, unless they prefer to remain anonymous.
- CVEs are requested for issues that warrant them (typically high-impact
  remote-exploit or auth-bypass classes).

## Out of scope

The following are tracked as regular bugs, not security reports:

- Crashes or hangs without a clear security impact.
- Performance issues, DoS-by-resource-exhaustion in a single process.
- Issues that require an attacker who already has root or container
  shell access on the host.
- Vulnerabilities in third-party services (OpenAI, Deepgram, etc.);
  please report those to the relevant provider.
- Issues in unsupported versions per the table above.

## Hall of fame

Researchers who have reported valid issues will be listed here after
disclosure with their permission.
