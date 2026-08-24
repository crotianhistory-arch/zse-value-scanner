# Sources — v0.4.1 ESEF Repository URL Normalization

## filings.xbrl.org API

Official API documentation:
https://filings.xbrl.org/docs/api

The public API is served from `https://filings.xbrl.org/api` and provides filing resources including report links.

## Repository-link behavior

The open-source `xbrl-filings-api` project documents the filings.xbrl.org filing URL fields (`json_url`, `package_url`, `report_url`/XHTML, `viewer_url`).
https://github.com/lsalmela/xbrl-filings-api

A current ESEF integration reference explicitly notes that `json_url` can be root-relative and should be resolved against `https://filings.xbrl.org`.
https://pipeworx.io/docs/reference/esef-filings/

## Policy

v0.4.1 only canonicalizes links into the trusted HTTPS origin `filings.xbrl.org`. It does not weaken the downloader's HTTPS-only check and does not permit arbitrary external hosts.
