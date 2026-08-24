# Sources — v0.4.2 Bounded Narrative Fallback

## Live regression observed 2026-08-22

The first Alfen N.V. live ESEF activity pilot (LEI `724500HDW6IWR9J5YT90`, period end 2025-12-31) successfully discovered and downloaded the required xBRL-JSON, but the repository XHTML exceeded the v0.4.1 80 MiB evidence-object limit. The bounded downloader correctly stopped the transfer.

## Design consequence

Structured xBRL evidence remains mandatory for the activity pack. XHTML narrative is an enrichment source and is allowed to be unavailable or skipped when it exceeds the explicit download cap. Oversize narrative evidence must not cause already-valid structured XBRL activity evidence to be discarded.

The patch does not raise the XHTML byte limit, does not relax HTTPS restrictions, does not swallow non-size network/security failures, and does not introduce database writes or peer decisions.
