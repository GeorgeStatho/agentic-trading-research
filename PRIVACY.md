# Privacy

This repository includes optional startup telemetry intended to estimate how many unique installs have actually run the project.

## What telemetry does

When telemetry is enabled, the application sends a small startup ping to the configured telemetry endpoint.

The current implementation sends only:

- `event`
- `telemetry_version`
- `project`
- `runtime`
- `install_id`
- `install_created_at`
- `started_at`
- optional `release`

`install_id` is a random local identifier generated on the machine running the project. It is used to estimate unique installs, not to identify a real person.

## What telemetry does not collect

The current telemetry implementation does not intentionally send:

- names
- email addresses
- GitHub account information
- source code
- local environment variables
- trading credentials
- portfolio contents
- trade history

## What the data is used for

Telemetry is currently used to:

- count unique installs that have run the project
- count startup events over time
- power dashboard usage statistics such as the "People Using It" KPI

## How to disable telemetry

Telemetry can be disabled by setting:

```env
TELEMETRY_ENABLED=false
```

## Receiver-side notes

If you run the telemetry receiver on your own VM or server:

- startup write access can be left public so clones can report in
- the telemetry summary endpoint can be protected with a private bearer token

See [.env.example](.env.example) and [README.md](README.md) for the current environment variables and setup notes.

## Important limitation

Telemetry counts unique install IDs, not guaranteed unique humans. One person on multiple machines may count as multiple installs, and deleting the local installation metadata may create a new install identity.
