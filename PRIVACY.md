# Privacy

## Current data flow

The Loopmetry v0.1 core:

- reads normalized JSONL events from a local file;
- optionally stores those events in a local SQLite database;
- calculates metrics locally;
- writes reports locally; and
- makes no network requests and includes no telemetry.

The default database path is `.loopmetry/loopmetry.db` inside the current working directory.

## Data accepted by the core

The canonical schema can contain local file paths, requirement summaries, command text, error messages, commit hashes, and short evidence summaries. Depending on the project, these values can still reveal confidential information.

The metric engine does not require:

- raw user prompts;
- agent response text;
- source-code bodies;
- API keys or credentials;
- Git author email addresses;
- customer records; or
- remote repository URLs.

Adapters should omit those fields by default.

## User responsibilities

Users and organizations are responsible for:

- ensuring they are authorized to process the project evidence;
- selecting an appropriate storage location and file permissions;
- applying retention and deletion policies;
- avoiding accidental commits of `.loopmetry/` and generated reports; and
- reviewing adapter-specific data collection before enabling it.

## Future network features

Any future hosted sync or external narrative provider must be optional, disabled by default, and documented separately. A compliant implementation should provide a preflight view of the exact payload, an allowlist-based field policy, explicit retention settings, and deletion/export controls.
