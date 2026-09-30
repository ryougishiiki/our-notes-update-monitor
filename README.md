# Our Notes Update Monitor

Automated data and resource update tracker for BanG Dream! Our Notes.

The monitor checks lightweight upstream fingerprints, creates immutable normalized snapshots when sources change, computes asset and row-level data diffs, and publishes deterministic reports and JSON events. It stores metadata and normalized source data; large binary resources are not retained.

## Run

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e .

onwatch probe
onwatch probe --trigger-scan
onwatch scan
```

The configured server is `intl`. The catalog version and CDN root are in `config/intl.json`; set `catalog.versionUrl` if a catalog-version endpoint becomes available. The catalog hash is checked on every probe. The Master index uses the public Haneoka data mirror and is marked as derived. A game-version endpoint can be configured under `gameVersion`; it is unset by default.

`probe --trigger-scan` waits for changed fingerprints only when `--settle-seconds` is provided. For a server timer, for example:

```sh
onwatch probe --trigger-scan --settle-seconds 60 --stable-checks 2
```

Deep scans use a local single-flight lock. GitHub Actions also uses a concurrency group. A failed or incomplete scan leaves `state/current.json` pointing to the last complete snapshot.

`state/probe.json` records the latest lightweight check. `state/scanned.json` records the last source fingerprints that produced a complete scan, so a change remains pending if a scan fails or is deferred.

## Snapshot and event formats

Snapshots are stored under `snapshots/<revision>/` and contain:

- source versions and provenance in `meta.json`, `version.json`, and `catalog.json`
- sorted resource metadata in `assets.json` and MusicScore entries in `charts.json`
- normalized Master tables in `master/`

The catalog parser preserves unknown resources and classifies them as `unknown` until a rule is added. Master row diffs include table, key, operation, and changed fields. The public contracts are in `schemas/snapshot.schema.json` and `schemas/update-event.schema.json`.

Each update writes `events/<event-id>.json` and `reports/<event-id>.md`. Event IDs are deterministic for a snapshot transition, so retrying the same scan does not create a second event. Static JSON feeds are rebuilt at `feed/` and `site/api/`; an Atom feed is written to `feed.xml`. The Pages site includes a timeline and per-event pages and can be published directly from `site/`.

## Automation

`.github/workflows/probe.yml` checks fingerprints every five minutes and calls the deep-scan workflow only after a change. Deep scans validate all inputs before advancing the current pointer. The Pages workflow publishes the static JSON API and feed.

The probe tracks the official catalog hash and hashes of the configured mirror indexes. On a change, a deep scan refreshes song details and the configured event and character collections. Mirror data is derived rather than an official Master endpoint; the source authority is recorded in every snapshot and event.

## Status API and integrations

`site/api/status.json` reports health, the last successful scan, current
snapshot, latest event, catalog-version source, game-version availability, and
Master authority. The version source is `config` until an official discovery
endpoint is configured; `gameVersion: null` is represented as unavailable.

`onwatch notify` sends a `repository_dispatch` event only when chart entries
were added, changed, or removed. Its payload contains the event ID, snapshot
revision, catalog hash, and chart-key lists; it does not include a full
snapshot. Workflow failure alerts use a distinct `our-notes-update-failure`
event and do not send ordinary `NO_CHANGE` probes.

Update webhooks include `X-OnWatch-Timestamp`, an HMAC-SHA256
`X-OnWatch-Signature`, and an `Idempotency-Key`. A minimal local contract
receiver is available at `tools/webhook_receiver.py`; it rejects invalid or
expired signatures and acknowledges duplicate event IDs without processing
them again. Keep `ONWATCH_WEBHOOK_URL` and `ONWATCH_WEBHOOK_SECRET` in GitHub
Actions Secrets. Set `ONWATCH_PUBLIC_BASE_URL` as a repository variable for
absolute report links. Do not add credentials to `config/intl.json`.

## License

This repository is licensed under MPL-2.0. Addressables parser attribution is
recorded in `THIRD_PARTY_NOTICES.md`.
