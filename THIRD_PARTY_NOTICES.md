# Third-party notices

## Haneoka source references

The official Master version-service and manifest contracts and the bounded
catalog probing strategy in this repository are adapted from
[haneoka-gakuen/haneoka](https://github.com/haneoka-gakuen/haneoka):

- `scripts/ingest/master.py` (`decode_master_version` and
  `discover_master_version`)
- `scripts/extract/master.py` (`validate_master_manifest` and Master table
  integrity/decryption flow)
- `scripts/ingest/apks.py` (`_resolve_catalog_version`)
- `scripts/ingest/catalog.py` (Addressables parsing, documented in
  `src/onwatch/sources/addressables.py`)

The referenced Haneoka Source Code Form is licensed under the Mozilla Public
License 2.0 (MPL-2.0). Adapted files retain source-level attribution, and the
license text is available in [`LICENSE`](LICENSE). This notice describes the
known adapted files; it does not change the terms of unrelated source or data.

Haneoka is used only for non-authoritative mirror comparison. Official Master
and catalog data remain the production authority; neither repository is a
runtime dependency.
