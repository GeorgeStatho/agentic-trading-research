# Privacy

This repository includes startup telemetry, **on by default**, intended to estimate how many unique installs have
actually run the project. It is easy to turn off (see below).

## What telemetry does

When telemetry is enabled, each process (`worker`, `option_manager`, `news_collector`, `api`) sends a small
startup ping to `TELEMETRY_ENDPOINT`. Unless you set it, that is the project's receiver,
`https://dashboard.huvle.org/api/telemetry/startup`.

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

The receiver stores only that payload. Like any web server, the receiver's nginx access log records the
requesting IP address; it is not stored with the telemetry data or used to identify anyone.

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

Set this in `.env` before the first start, and nothing is sent:

```env
TELEMETRY_ENABLED=false
```

## Receiver-side notes

If you run the telemetry receiver on your own VM or server:

- startup write access can be left public so clones can report in
- to stop one machine inventing installs, the receiver lets each network add only a few new install IDs a day
  (`TELEMETRY_RECEIVER_NEW_INSTALLS_PER_NETWORK_PER_DAY`, default 3). For that it keeps an HMAC of the
  requesting address under a random salt for the current day, never the address itself. The salt and the counts
  are deleted once the day is over, so a past day can't be linked back to an address
- the telemetry summary endpoint can be protected with a private bearer token

See [.env.example](.env.example) and [README.md](README.md) for the current environment variables and setup notes.

## Important limitation

Telemetry counts unique install IDs, not guaranteed unique humans. One person on multiple machines may count as multiple installs, and deleting the local installation metadata may create a new install identity.
