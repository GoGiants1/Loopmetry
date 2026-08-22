# Security policy

Loopmetry processes development evidence that may expose confidential project details even when raw source code is excluded.

## Supported versions

The project is pre-1.0. Security fixes will be applied to the latest release and the `main` branch.

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private vulnerability reporting feature when enabled. Do not include real customer transcripts, credentials, or proprietary source code in a public issue.

## Local deployment guidance

- Keep `.loopmetry/` out of version control.
- Restrict database and report permissions to the intended user or team.
- Run adapters against allowlisted repositories only.
- Do not ingest credential files, environment files, or secret-manager exports.
- Prefer normalized evidence over raw transcript retention.
- Review generated Markdown before attaching it to a pull request or ticket.
- Delete local evidence when the configured retention period expires.

## Adapter security requirements

A source adapter should:

- parse files read-only;
- avoid executing transcript content;
- treat command strings as data, not shell input;
- reject path traversal when exporting artifacts;
- record adapter version and source coverage;
- provide deterministic redaction tests; and
- make all network access explicit and opt-in.
