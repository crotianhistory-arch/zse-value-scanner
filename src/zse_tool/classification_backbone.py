from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "official-classification-backbone-v0.1"
ALLOWED_ENDPOINT_HOSTS = {"publications.europa.eu", "ec.europa.eu", "showvoc.op.europa.eu"}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_PAGE_SIZE = 5000
DEFAULT_MAX_PAGES = 200

SKOS = "http://www.w3.org/2004/02/skos/core#"
XKOS = "http://rdf-vocabulary.ddialliance.org/xkos#"
OWL = "http://www.w3.org/2002/07/owl#"


class ClassificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchemeSpec:
    key: str
    system: str
    version: str
    label_contains: str
    expected_item_count: int
    expected_levels: int


@dataclass(frozen=True)
class SdmxSchemeSpec:
    key: str
    system: str
    version: str
    title_contains: str
    preferred_ids: tuple[str, ...]
    expected_item_count: int
    expected_levels: int
    expected_level_counts: dict[int, int]


@dataclass(frozen=True)
class ShowVocSchemeSpec:
    key: str
    system: str
    version: str
    project: str
    expected_item_count: int
    expected_levels: int
    expected_level_counts: dict[int, int]
    required_languages: tuple[str, ...]


@dataclass(frozen=True)
class PageResult:
    rows: list[dict[str, Any]]
    raw_path: str
    sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_https_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise ClassificationError("classification endpoint must use HTTPS")
    if parsed.username or parsed.password:
        raise ClassificationError("classification endpoint credentials are not allowed")
    if parsed.port not in (None, 443):
        raise ClassificationError("unexpected classification endpoint port")
    if parsed.hostname not in ALLOWED_ENDPOINT_HOSTS:
        raise ClassificationError(f"classification endpoint host is not allow-listed: {parsed.hostname}")


def _read_bounded(response: Any, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ClassificationError(f"classification response exceeds {max_bytes} bytes")
    return b"".join(chunks)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "item"


def _write_raw(raw_dir: Path, scheme_key: str, query_kind: str, page: int, data: bytes) -> tuple[str, str]:
    digest = _sha256_bytes(data)
    dst_dir = raw_dir / _safe_component(scheme_key) / _safe_component(query_kind)
    dst_dir.mkdir(parents=True, exist_ok=True)
    path = dst_dir / f"page-{page:04d}-{digest}.json"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return str(path), digest


def _binding_value(binding: dict[str, Any], key: str) -> str | None:
    value = binding.get(key)
    if not isinstance(value, dict):
        return None
    raw = value.get("value")
    if raw is None:
        return None
    return str(raw)


def _binding_lang(binding: dict[str, Any], key: str) -> str:
    value = binding.get(key)
    if not isinstance(value, dict):
        return ""
    return str(value.get("xml:lang") or value.get("lang") or "")


def _sparql_request(endpoint: str, query: str, *, timeout: float = 45.0) -> bytes:
    _validate_https_endpoint(endpoint)
    payload_text = urlencode({"query": query})
    common_headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "zse-value-scanner/0.4.9 classification-backbone",
    }
    get_request = Request(
        f"{endpoint}?{payload_text}",
        method="GET",
        headers=common_headers,
    )
    post_request = Request(
        endpoint,
        data=payload_text.encode("utf-8"),
        method="POST",
        headers={
            **common_headers,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )

    failures: list[str] = []
    for transport, request in (("GET", get_request), ("POST", post_request)):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=timeout) as response:  # nosec B310 - endpoint is validated above
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise ClassificationError(f"SPARQL endpoint returned HTTP {status}")
                    return _read_bounded(response)
            except Exception as exc:  # network boundary; re-raised with context
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        failures.append(f"{transport}: {last_error}")

    raise ClassificationError("SPARQL request failed via GET and POST after retries: " + " | ".join(failures))


def _parse_sparql_json(data: bytes) -> list[dict[str, Any]]:
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"invalid SPARQL JSON: {exc}") from exc
    rows = obj.get("results", {}).get("bindings")
    if not isinstance(rows, list):
        raise ClassificationError("SPARQL JSON missing results.bindings")
    return [row for row in rows if isinstance(row, dict)]


def _paged_query(
    endpoint: str,
    base_query: str,
    *,
    raw_dir: Path,
    scheme_key: str,
    query_kind: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[PageResult]:
    if page_size < 1 or page_size > 10000:
        raise ClassificationError("page_size must be between 1 and 10000")
    if max_pages < 1 or max_pages > 1000:
        raise ClassificationError("max_pages must be between 1 and 1000")

    results: list[PageResult] = []
    for page in range(max_pages):
        query = f"{base_query}\nLIMIT {page_size}\nOFFSET {page * page_size}"
        data = _sparql_request(endpoint, query)
        raw_path, digest = _write_raw(raw_dir, scheme_key, query_kind, page, data)
        rows = _parse_sparql_json(data)
        results.append(PageResult(rows=rows, raw_path=raw_path, sha256=digest))
        if len(rows) < page_size:
            return results
    raise ClassificationError(f"{scheme_key}/{query_kind} exceeded max_pages={max_pages}")


def _catalog_from_path(path: Path) -> tuple[str, list[SchemeSpec]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != "official-classification-catalog-v0.1":
        raise ClassificationError("unsupported classification catalog schema")
    endpoint = str(obj.get("endpoint") or "")
    _validate_https_endpoint(endpoint)
    specs: list[SchemeSpec] = []
    for item in obj.get("schemes") or []:
        specs.append(
            SchemeSpec(
                key=str(item["key"]),
                system=str(item["system"]),
                version=str(item["version"]),
                label_contains=str(item["label_contains"]),
                expected_item_count=int(item["expected_item_count"]),
                expected_levels=int(item["expected_levels"]),
            )
        )
    if not specs:
        raise ClassificationError("classification catalog has no schemes")
    if len({s.key for s in specs}) != len(specs):
        raise ClassificationError("duplicate scheme key in catalog")
    return endpoint, specs


def _resolve_scheme_uri(endpoint: str, spec: SchemeSpec, raw_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    needle = spec.label_contains.replace('"', '\\"')
    version = spec.version.replace('"', '\\"')
    query = f"""
PREFIX skos: <{SKOS}>
PREFIX owl: <{OWL}>
SELECT DISTINCT ?scheme ?label ?version WHERE {{
  ?scheme a skos:ConceptScheme .
  OPTIONAL {{ ?scheme skos:prefLabel ?label . }}
  OPTIONAL {{ ?scheme owl:versionInfo ?version . }}
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE(\"{needle}\")))
  FILTER(CONTAINS(LCASE(CONCAT(STR(?label), \" \" , STR(?version))), LCASE(\"{version}\")))
}}
ORDER BY ?scheme
""".strip()
    data = _sparql_request(endpoint, query)
    raw_path, digest = _write_raw(raw_dir, spec.key, "scheme-discovery", 0, data)
    rows = _parse_sparql_json(data)
    candidates: list[str] = []
    audit: list[dict[str, Any]] = []
    for row in rows:
        uri = _binding_value(row, "scheme")
        label = _binding_value(row, "label")
        found_version = _binding_value(row, "version")
        if uri:
            candidates.append(uri)
            audit.append({"scheme_uri": uri, "label": label, "version": found_version})
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ClassificationError(
            f"could not resolve exactly one scheme for {spec.key}; candidates={candidates!r}; raw={raw_path}; sha256={digest}"
        )
    return candidates[0], audit


def _items_query(scheme_uri: str) -> str:
    return f"""
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?concept ?code ?parent WHERE {{
  ?concept a skos:Concept ; skos:inScheme <{scheme_uri}> ; skos:notation ?code .
  OPTIONAL {{ ?concept skos:broader ?parent . }}
}}
ORDER BY ?code ?concept ?parent
""".strip()


def _labels_query(scheme_uri: str) -> str:
    return f"""
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?concept ?code ?labelKind ?label WHERE {{
  ?concept a skos:Concept ; skos:inScheme <{scheme_uri}> ; skos:notation ?code .
  VALUES ?labelKind {{ skos:prefLabel skos:altLabel }}
  ?concept ?labelKind ?label .
}}
ORDER BY ?code ?labelKind ?label
""".strip()


def _notes_query(scheme_uri: str) -> str:
    return f"""
PREFIX skos: <{SKOS}>
PREFIX xkos: <{XKOS}>
SELECT DISTINCT ?concept ?code ?noteKind ?note WHERE {{
  ?concept a skos:Concept ; skos:inScheme <{scheme_uri}> ; skos:notation ?code .
  VALUES ?noteKind {{
    skos:scopeNote
    xkos:inclusionNote
    xkos:coreContentNote
    xkos:additionalContentNote
    xkos:exclusionNote
  }}
  ?concept ?noteKind ?note .
  FILTER(LANG(?note) = \"\" || LANGMATCHES(LANG(?note), \"en\"))
}}
ORDER BY ?code ?noteKind ?note
""".strip()


def _level_from_code(code: str) -> int:
    code = code.strip()
    if re.fullmatch(r"[A-Z]", code):
        return 1
    if re.fullmatch(r"\d{2}", code):
        return 2
    if re.fullmatch(r"\d{2}\.\d", code):
        return 3
    if re.fullmatch(r"\d{2}\.\d{2}", code):
        return 4
    if re.fullmatch(r"\d{2}\.\d{2}\.\d", code):
        return 5
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", code):
        return 6
    raise ClassificationError(f"unrecognized classification code shape: {code!r}")


def _predicate_short(uri: str) -> str:
    if uri.startswith(SKOS):
        return "skos:" + uri[len(SKOS):]
    if uri.startswith(XKOS):
        return "xkos:" + uri[len(XKOS):]
    return uri


def _rows_from_pages(pages: Iterable[PageResult]) -> Iterable[dict[str, Any]]:
    for page in pages:
        yield from page.rows


def _normalize_scheme(
    spec: SchemeSpec,
    scheme_uri: str,
    item_pages: list[PageResult],
    label_pages: list[PageResult],
    note_pages: list[PageResult],
) -> dict[str, Any]:
    items_by_uri: dict[str, dict[str, Any]] = {}
    code_to_uri: dict[str, str] = {}
    for row in _rows_from_pages(item_pages):
        uri = _binding_value(row, "concept")
        code = _binding_value(row, "code")
        parent = _binding_value(row, "parent")
        if not uri or not code:
            continue
        current = items_by_uri.setdefault(uri, {"uri": uri, "code": code, "parent_uri": parent})
        if current["code"] != code:
            raise ClassificationError(f"concept has conflicting codes: {uri}")
        if parent and current.get("parent_uri") not in (None, parent):
            raise ClassificationError(f"concept has multiple direct parents: {uri}")
        if parent:
            current["parent_uri"] = parent
        if code in code_to_uri and code_to_uri[code] != uri:
            raise ClassificationError(f"duplicate code {code!r} in {spec.key}")
        code_to_uri[code] = uri

    if len(items_by_uri) != spec.expected_item_count:
        raise ClassificationError(
            f"{spec.key} expected {spec.expected_item_count} items, observed {len(items_by_uri)}"
        )

    labels: list[dict[str, str]] = []
    label_seen: set[tuple[str, str, str, str]] = set()
    for row in _rows_from_pages(label_pages):
        uri = _binding_value(row, "concept")
        code = _binding_value(row, "code")
        kind_uri = _binding_value(row, "labelKind")
        label = _binding_value(row, "label")
        if not uri or not code or not kind_uri or label is None:
            continue
        if uri not in items_by_uri:
            raise ClassificationError(f"label references unknown concept: {uri}")
        lang = _binding_lang(row, "label")
        kind = _predicate_short(kind_uri)
        key = (code, lang, kind, label)
        if key not in label_seen:
            label_seen.add(key)
            labels.append({"code": code, "language": lang, "kind": kind, "label": label})

    notes: list[dict[str, str]] = []
    note_seen: set[tuple[str, str, str, str]] = set()
    for row in _rows_from_pages(note_pages):
        uri = _binding_value(row, "concept")
        code = _binding_value(row, "code")
        kind_uri = _binding_value(row, "noteKind")
        note = _binding_value(row, "note")
        if not uri or not code or not kind_uri or note is None:
            continue
        if uri not in items_by_uri:
            raise ClassificationError(f"note references unknown concept: {uri}")
        lang = _binding_lang(row, "note")
        kind = _predicate_short(kind_uri)
        key = (code, lang, kind, note)
        if key not in note_seen:
            note_seen.add(key)
            notes.append({"code": code, "language": lang, "kind": kind, "text": note})

    items: list[dict[str, Any]] = []
    for uri, item in items_by_uri.items():
        parent_uri = item.get("parent_uri")
        parent_code = None
        if parent_uri:
            parent = items_by_uri.get(parent_uri)
            if parent is None:
                raise ClassificationError(f"parent is outside scheme for {item['code']}: {parent_uri}")
            parent_code = parent["code"]
        level = _level_from_code(item["code"])
        if level > spec.expected_levels:
            raise ClassificationError(f"{spec.key} code exceeds expected levels: {item['code']}")
        items.append(
            {
                "code": item["code"],
                "uri": uri,
                "parent_code": parent_code,
                "level": level,
            }
        )
    items.sort(key=lambda x: (x["level"], x["code"]))
    labels.sort(key=lambda x: (x["code"], x["language"], x["kind"], x["label"]))
    notes.sort(key=lambda x: (x["code"], x["language"], x["kind"], x["text"]))

    english_codes = {
        row["code"] for row in labels if row["language"].lower().startswith("en")
    }
    missing_english = sorted(set(code_to_uri) - english_codes)
    if missing_english:
        raise ClassificationError(
            f"{spec.key} has {len(missing_english)} codes without an English label; first={missing_english[:5]}"
        )

    languages = sorted({row["language"] for row in labels if row["language"]})
    digest_payload = {"scheme_uri": scheme_uri, "items": items, "labels": labels, "notes": notes}
    return {
        "scheme": {
            "key": spec.key,
            "system": spec.system,
            "version": spec.version,
            "scheme_uri": scheme_uri,
            "item_count": len(items),
            "label_count": len(labels),
            "note_count": len(notes),
            "languages": languages,
            "normalized_sha256": _sha256_bytes(_canonical_json_bytes(digest_payload)),
        },
        "items": items,
        "labels": labels,
        "notes": notes,
    }


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE classification_schemes (
            scheme_key TEXT PRIMARY KEY,
            system TEXT NOT NULL,
            version TEXT NOT NULL,
            scheme_uri TEXT NOT NULL,
            source_endpoint TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            label_count INTEGER NOT NULL,
            note_count INTEGER NOT NULL,
            languages_json TEXT NOT NULL,
            normalized_sha256 TEXT NOT NULL
        );
        CREATE TABLE classification_items (
            scheme_key TEXT NOT NULL,
            code TEXT NOT NULL,
            uri TEXT NOT NULL,
            parent_code TEXT,
            level INTEGER NOT NULL,
            PRIMARY KEY (scheme_key, code),
            FOREIGN KEY (scheme_key) REFERENCES classification_schemes(scheme_key)
        );
        CREATE TABLE classification_labels (
            scheme_key TEXT NOT NULL,
            code TEXT NOT NULL,
            language TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (scheme_key, code, language, kind, label),
            FOREIGN KEY (scheme_key, code) REFERENCES classification_items(scheme_key, code)
        );
        CREATE TABLE classification_notes (
            scheme_key TEXT NOT NULL,
            code TEXT NOT NULL,
            language TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (scheme_key, code, language, kind, text),
            FOREIGN KEY (scheme_key, code) REFERENCES classification_items(scheme_key, code)
        );
        CREATE TABLE classification_links (
            source_scheme_key TEXT NOT NULL,
            source_code TEXT NOT NULL,
            target_scheme_key TEXT NOT NULL,
            target_code TEXT NOT NULL,
            relation TEXT NOT NULL,
            evidence_class TEXT NOT NULL,
            rationale TEXT NOT NULL,
            PRIMARY KEY (source_scheme_key, source_code, target_scheme_key, target_code, relation),
            FOREIGN KEY (source_scheme_key, source_code) REFERENCES classification_items(scheme_key, code),
            FOREIGN KEY (target_scheme_key, target_code) REFERENCES classification_items(scheme_key, code)
        );
        CREATE INDEX idx_labels_search ON classification_labels(language, label);
        CREATE INDEX idx_items_parent ON classification_items(scheme_key, parent_code);
        CREATE INDEX idx_links_target ON classification_links(target_scheme_key, target_code);
        """
    )


def _cpa_to_nace_code(cpa_code: str, level: int) -> str | None:
    if level <= 4:
        return cpa_code
    match = re.fullmatch(r"(\d{2}\.\d{2})\.\d{1,2}", cpa_code)
    if not match:
        return None
    return match.group(1)


def _insert_structural_links(conn: sqlite3.Connection) -> int:
    scheme_rows = conn.execute(
        "SELECT scheme_key, system, version FROM classification_schemes"
    ).fetchall()
    current_nace = next((r[0] for r in scheme_rows if r[1] == "NACE" and r[2] == "2.1"), None)
    current_cpa = next((r[0] for r in scheme_rows if r[1] == "CPA" and r[2] == "2.2"), None)
    if not current_nace or not current_cpa:
        return 0

    nace_codes = {r[0] for r in conn.execute(
        "SELECT code FROM classification_items WHERE scheme_key = ?", (current_nace,)
    )}
    links = []
    for code, level in conn.execute(
        "SELECT code, level FROM classification_items WHERE scheme_key = ?", (current_cpa,)
    ):
        target = _cpa_to_nace_code(code, level)
        if target and target in nace_codes:
            links.append(
                (
                    current_cpa,
                    code,
                    current_nace,
                    target,
                    "CPA_STRUCTURAL_ORIGIN_NACE",
                    "D1_DETERMINISTIC_FROM_OFFICIAL_STRUCTURE",
                    "CPA 2.2 is structured on NACE Rev. 2.1; through class level codes correspond, and CPA categories/subcategories inherit the first four-digit NACE class.",
                )
            )
    conn.executemany(
        """
        INSERT INTO classification_links (
            source_scheme_key, source_code, target_scheme_key, target_code,
            relation, evidence_class, rationale
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        links,
    )
    return len(links)


def _write_database(
    output_db: Path,
    endpoint: str,
    normalized_schemes: list[dict[str, Any]],
    *,
    retrieved_at: str,
    replace: bool,
) -> dict[str, Any]:
    output_db = output_db.expanduser().resolve()
    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists() and not replace:
        raise ClassificationError(f"output database already exists; pass --replace explicitly: {output_db}")

    tmp = output_db.with_suffix(output_db.suffix + f".tmp-{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        _create_schema(conn)
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("schema_version", SCHEMA_VERSION))
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("retrieved_at", retrieved_at))
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("source_endpoint", endpoint))

        for normalized in normalized_schemes:
            scheme = normalized["scheme"]
            conn.execute(
                """
                INSERT INTO classification_schemes (
                    scheme_key, system, version, scheme_uri, source_endpoint, retrieved_at,
                    item_count, label_count, note_count, languages_json, normalized_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scheme["key"],
                    scheme["system"],
                    scheme["version"],
                    scheme["scheme_uri"],
                    endpoint,
                    retrieved_at,
                    scheme["item_count"],
                    scheme["label_count"],
                    scheme["note_count"],
                    json.dumps(scheme["languages"], ensure_ascii=False),
                    scheme["normalized_sha256"],
                ),
            )
            conn.executemany(
                "INSERT INTO classification_items(scheme_key, code, uri, parent_code, level) VALUES (?, ?, ?, ?, ?)",
                [
                    (scheme["key"], row["code"], row["uri"], row["parent_code"], row["level"])
                    for row in normalized["items"]
                ],
            )
            conn.executemany(
                "INSERT INTO classification_labels(scheme_key, code, language, kind, label) VALUES (?, ?, ?, ?, ?)",
                [
                    (scheme["key"], row["code"], row["language"], row["kind"], row["label"])
                    for row in normalized["labels"]
                ],
            )
            conn.executemany(
                "INSERT INTO classification_notes(scheme_key, code, language, kind, text) VALUES (?, ?, ?, ?, ?)",
                [
                    (scheme["key"], row["code"], row["language"], row["kind"], row["text"])
                    for row in normalized["notes"]
                ],
            )
        structural_links = _insert_structural_links(conn)
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("structural_link_count", str(structural_links)))
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ClassificationError(f"reference database integrity_check failed: {integrity}")
    finally:
        conn.close()

    os.replace(tmp, output_db)
    digest = _sha256_bytes(output_db.read_bytes())
    return {"output_db": str(output_db), "sha256": digest, "structural_link_count": structural_links}


def _sync_cellar(catalog: Path, output_db: Path, raw_dir: Path, *, replace: bool = False) -> dict[str, Any]:
    endpoint, specs = _catalog_from_path(catalog)
    raw_dir = raw_dir.expanduser().resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = _utc_now()
    normalized_schemes: list[dict[str, Any]] = []
    source_manifest: dict[str, Any] = {
        "schema_version": "official-classification-raw-manifest-v0.1",
        "retrieved_at": retrieved_at,
        "endpoint": endpoint,
        "schemes": [],
    }

    for spec in specs:
        scheme_uri, discovery_audit = _resolve_scheme_uri(endpoint, spec, raw_dir)
        item_pages = _paged_query(
            endpoint,
            _items_query(scheme_uri),
            raw_dir=raw_dir,
            scheme_key=spec.key,
            query_kind="items",
        )
        label_pages = _paged_query(
            endpoint,
            _labels_query(scheme_uri),
            raw_dir=raw_dir,
            scheme_key=spec.key,
            query_kind="labels",
        )
        note_pages = _paged_query(
            endpoint,
            _notes_query(scheme_uri),
            raw_dir=raw_dir,
            scheme_key=spec.key,
            query_kind="notes-en",
        )
        normalized = _normalize_scheme(spec, scheme_uri, item_pages, label_pages, note_pages)
        normalized["scheme"]["key"] = spec.key
        normalized_schemes.append(normalized)
        source_manifest["schemes"].append(
            {
                "key": spec.key,
                "scheme_uri": scheme_uri,
                "discovery": discovery_audit,
                "item_pages": [p.__dict__ | {"rows": len(p.rows)} for p in item_pages],
                "label_pages": [p.__dict__ | {"rows": len(p.rows)} for p in label_pages],
                "note_pages": [p.__dict__ | {"rows": len(p.rows)} for p in note_pages],
                "normalized": normalized["scheme"],
            }
        )

    # Avoid embedding the full parsed row arrays in the manifest.
    for scheme in source_manifest["schemes"]:
        for page_key in ("item_pages", "label_pages", "note_pages"):
            for page in scheme[page_key]:
                page.pop("rows", None) if isinstance(page.get("rows"), list) else None

    manifest_bytes = _canonical_json_bytes(source_manifest)
    manifest_digest = _sha256_bytes(manifest_bytes)
    manifest_path = raw_dir / f"manifest-{retrieved_at.replace(':', '').replace('-', '')}-{manifest_digest}.json"
    manifest_path.write_bytes(manifest_bytes)

    db_result = _write_database(
        output_db,
        endpoint,
        normalized_schemes,
        retrieved_at=retrieved_at,
        replace=replace,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at": retrieved_at,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": manifest_digest,
        "schemes": [x["scheme"] for x in normalized_schemes],
        **db_result,
    }



def _catalog_v2_from_path(path: Path) -> tuple[str, tuple[str, ...], list[SdmxSchemeSpec]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != "official-classification-catalog-v0.2":
        raise ClassificationError("unsupported SDMX classification catalog schema")
    if obj.get("transport") != "eurostat-sdmx-codelist":
        raise ClassificationError("unsupported classification catalog transport")
    base = str(obj.get("sdmx_base") or "").rstrip("/")
    _validate_https_endpoint(base)
    languages = tuple(str(x).lower() for x in (obj.get("languages") or []))
    if not languages or len(set(languages)) != len(languages):
        raise ClassificationError("SDMX catalog languages must be a non-empty unique list")
    if any(not re.fullmatch(r"[a-z]{2}", x) for x in languages):
        raise ClassificationError("SDMX catalog language codes must be two lowercase letters")

    specs: list[SdmxSchemeSpec] = []
    for item in obj.get("schemes") or []:
        level_counts = {int(k): int(v) for k, v in (item.get("expected_level_counts") or {}).items()}
        specs.append(
            SdmxSchemeSpec(
                key=str(item["key"]),
                system=str(item["system"]),
                version=str(item["version"]),
                title_contains=str(item["title_contains"]),
                preferred_ids=tuple(str(x) for x in (item.get("preferred_ids") or [])),
                expected_item_count=int(item["expected_item_count"]),
                expected_levels=int(item["expected_levels"]),
                expected_level_counts=level_counts,
            )
        )
    if not specs:
        raise ClassificationError("SDMX classification catalog has no schemes")
    if len({s.key for s in specs}) != len(specs):
        raise ClassificationError("duplicate scheme key in SDMX catalog")
    for spec in specs:
        if set(spec.expected_level_counts) != set(range(1, spec.expected_levels + 1)):
            raise ClassificationError(f"{spec.key} expected_level_counts must cover every level")
        if sum(spec.expected_level_counts.values()) != spec.expected_item_count:
            raise ClassificationError(f"{spec.key} expected level counts do not sum to expected_item_count")
    return base, languages, specs


def _http_get(url: str, *, accept: str, timeout: float = 60.0) -> bytes:
    _validate_https_endpoint(url)
    request = Request(
        url,
        method="GET",
        headers={"Accept": accept, "User-Agent": "zse-value-scanner/0.4.10 classification-backbone"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310 - URL is validated above
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    raise ClassificationError(f"classification endpoint returned HTTP {status_code}")
                return _read_bounded(response)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise ClassificationError(f"classification GET failed after retries: {last_error}")


def _write_raw_blob(raw_dir: Path, scope: str, kind: str, data: bytes, suffix: str) -> tuple[str, str]:
    digest = _sha256_bytes(data)
    dst_dir = raw_dir / _safe_component(scope) / _safe_component(kind)
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = "." + suffix.lstrip(".")
    path = dst_dir / f"{digest}{suffix}"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return str(path), digest


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_sdmx_codelist_stubs(data: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ClassificationError(f"invalid SDMX codelist XML: {exc}") from exc
    xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"
    out: list[dict[str, Any]] = []
    for elem in root.iter():
        if _local_name(elem.tag) != "Codelist":
            continue
        codelist_id = str(elem.attrib.get("id") or "")
        if not codelist_id:
            continue
        names: dict[str, str] = {}
        for child in elem:
            if _local_name(child.tag) != "Name":
                continue
            value = (child.text or "").strip()
            if value:
                names[str(child.attrib.get(xml_lang) or "").lower()] = value
        out.append(
            {
                "id": codelist_id,
                "agency_id": str(elem.attrib.get("agencyID") or "ESTAT"),
                "version": str(elem.attrib.get("version") or ""),
                "urn": str(elem.attrib.get("urn") or ""),
                "names": names,
            }
        )
    if not out:
        raise ClassificationError("SDMX codelist stub response contains no codelists")
    return out


def _resolve_sdmx_codelist(spec: SdmxSchemeSpec, stubs: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(row["id"]).upper(): row for row in stubs}
    preferred = [by_id[x.upper()] for x in spec.preferred_ids if x.upper() in by_id]
    unique_preferred = {row["id"]: row for row in preferred}
    if len(unique_preferred) == 1:
        return next(iter(unique_preferred.values()))
    if len(unique_preferred) > 1:
        raise ClassificationError(f"{spec.key} matched multiple preferred codelist IDs: {sorted(unique_preferred)}")

    needle = spec.title_contains.casefold()
    version_token = spec.version.casefold()
    candidates: list[dict[str, Any]] = []
    for row in stubs:
        text = " ".join(row.get("names", {}).values()).casefold()
        if needle in text and (version_token in text or version_token in str(row.get("id", "")).casefold()):
            candidates.append(row)
    if len(candidates) != 1:
        audit = [
            {"id": row["id"], "version": row.get("version"), "names": row.get("names", {})}
            for row in stubs
            if needle in " ".join(row.get("names", {}).values()).casefold()
        ]
        raise ClassificationError(
            f"could not resolve exactly one SDMX codelist for {spec.key}; candidates={[(x['id'], x.get('version')) for x in candidates]!r}; title_matches={audit[:12]!r}"
        )
    return candidates[0]


def _parse_codelist_tsv(data: bytes) -> list[tuple[str, str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ClassificationError(f"invalid UTF-8 codelist TSV: {exc}") from exc
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        if "\t" not in raw_line:
            raise ClassificationError(f"invalid codelist TSV line {lineno}: missing tab")
        code, label = raw_line.split("\t", 1)
        code = code.strip()
        label = label.strip()
        if not code or not label:
            raise ClassificationError(f"invalid codelist TSV line {lineno}: empty code or label")
        if code in seen:
            raise ClassificationError(f"duplicate codelist TSV code: {code}")
        seen.add(code)
        rows.append((code, label))
    if not rows:
        raise ClassificationError("codelist TSV has no rows")
    return rows


def _classification_rows(rows: list[tuple[str, str]], expected_levels: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for code, label in rows:
        try:
            level = _level_from_code(code)
        except ClassificationError:
            continue
        if level <= expected_levels:
            out.append((code, label))
    return out


def _derive_parent_codes(codes: list[str], expected_levels: int) -> dict[str, str | None]:
    parents: dict[str, str | None] = {}
    last_by_level: dict[int, str] = {}
    for code in codes:
        level = _level_from_code(code)
        if level > expected_levels:
            continue
        if level == 1:
            parent = None
        elif level == 2:
            parent = last_by_level.get(1)
            if parent is None:
                raise ClassificationError(f"division {code} appears before any section")
        else:
            parent = last_by_level.get(level - 1)
            if parent is None:
                raise ClassificationError(f"code {code} has no preceding level-{level - 1} parent")
            expected_prefix = {
                3: code[:2],
                4: code[:4],
                5: code[:5],
                6: code[:7],
            }[level]
            if parent != expected_prefix:
                raise ClassificationError(f"derived parent mismatch for {code}: expected {expected_prefix}, observed {parent}")
        parents[code] = parent
        last_by_level[level] = code
        for deeper in list(last_by_level):
            if deeper > level:
                del last_by_level[deeper]
    return parents


def _sdmx_scheme_urn(row: dict[str, Any]) -> str:
    if row.get("urn"):
        return str(row["urn"])
    agency = str(row.get("agency_id") or "ESTAT")
    version = str(row.get("version") or "latest")
    return f"urn:sdmx:org.sdmx.infomodel.codelist.Codelist={agency}:{row['id']}({version})"


def _normalize_sdmx_scheme(
    spec: SdmxSchemeSpec,
    codelist: dict[str, Any],
    labels_by_language: dict[str, list[tuple[str, str]]],
) -> dict[str, Any]:
    if "en" not in labels_by_language:
        raise ClassificationError(f"{spec.key} requires an English codelist")
    english_rows = _classification_rows(labels_by_language["en"], spec.expected_levels)
    codes = [code for code, _ in english_rows]
    if len(codes) != spec.expected_item_count:
        raise ClassificationError(f"{spec.key} expected {spec.expected_item_count} classification items, observed {len(codes)}")
    if len(set(codes)) != len(codes):
        raise ClassificationError(f"{spec.key} has duplicate filtered classification codes")

    level_counts: dict[int, int] = {level: 0 for level in range(1, spec.expected_levels + 1)}
    for code in codes:
        level_counts[_level_from_code(code)] += 1
    if level_counts != spec.expected_level_counts:
        raise ClassificationError(
            f"{spec.key} level-count mismatch: expected={spec.expected_level_counts!r} observed={level_counts!r}"
        )

    code_set = set(codes)
    filtered_by_language: dict[str, list[tuple[str, str]]] = {}
    for language, rows in labels_by_language.items():
        filtered = _classification_rows(rows, spec.expected_levels)
        language_codes = [code for code, _ in filtered]
        if set(language_codes) != code_set or len(language_codes) != len(codes):
            missing = sorted(code_set - set(language_codes))[:5]
            extra = sorted(set(language_codes) - code_set)[:5]
            raise ClassificationError(
                f"{spec.key}/{language} codelist does not match English classification code set; missing={missing!r} extra={extra!r}"
            )
        filtered_by_language[language] = filtered

    parents = _derive_parent_codes(codes, spec.expected_levels)
    scheme_urn = _sdmx_scheme_urn(codelist)
    items = [
        {
            "code": code,
            "uri": f"urn:sdmx:org.sdmx.infomodel.codelist.Code=ESTAT:{codelist['id']}({codelist.get('version') or 'latest'}).{code}",
            "parent_code": parents[code],
            "level": _level_from_code(code),
        }
        for code in codes
    ]
    labels: list[dict[str, str]] = []
    for language in sorted(filtered_by_language):
        for code, label in filtered_by_language[language]:
            labels.append({"code": code, "language": language, "kind": "sdmx:Name", "label": label})
    labels.sort(key=lambda x: (x["code"], x["language"], x["label"]))
    notes: list[dict[str, str]] = []
    digest_payload = {"scheme_uri": scheme_urn, "items": items, "labels": labels, "notes": notes}
    return {
        "scheme": {
            "key": spec.key,
            "system": spec.system,
            "version": spec.version,
            "scheme_uri": scheme_urn,
            "item_count": len(items),
            "label_count": len(labels),
            "note_count": 0,
            "languages": sorted(filtered_by_language),
            "normalized_sha256": _sha256_bytes(_canonical_json_bytes(digest_payload)),
            "source_codelist_id": codelist["id"],
            "source_codelist_version": codelist.get("version") or "",
        },
        "items": items,
        "labels": labels,
        "notes": notes,
    }


def _sync_sdmx(catalog: Path, output_db: Path, raw_dir: Path, *, replace: bool = False) -> dict[str, Any]:
    base, languages, specs = _catalog_v2_from_path(catalog)
    raw_dir = raw_dir.expanduser().resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = _utc_now()

    stubs_url = f"{base}/codelist/ESTAT/all/latest?detail=allstubs"
    stubs_data = _http_get(stubs_url, accept="application/vnd.sdmx.structure+xml;version=2.1")
    stubs_path, stubs_sha = _write_raw_blob(raw_dir, "EUROSTAT_SDMX", "codelist-stubs", stubs_data, "xml")
    stubs = _parse_sdmx_codelist_stubs(stubs_data)

    normalized_schemes: list[dict[str, Any]] = []
    source_manifest: dict[str, Any] = {
        "schema_version": "official-classification-raw-manifest-v0.2",
        "retrieved_at": retrieved_at,
        "transport": "eurostat-sdmx-codelist",
        "endpoint": base,
        "stubs": {"url": stubs_url, "raw_path": stubs_path, "sha256": stubs_sha, "codelist_count": len(stubs)},
        "schemes": [],
    }

    for spec in specs:
        codelist = _resolve_sdmx_codelist(spec, stubs)
        labels_by_language: dict[str, list[tuple[str, str]]] = {}
        raw_languages: list[dict[str, Any]] = []
        for language in languages:
            codelist_id = quote(str(codelist["id"]), safe="")
            url = f"{base}/codelist/ESTAT/{codelist_id}/latest?format=TSV&lang={quote(language, safe='')}"
            data = _http_get(url, accept="text/tab-separated-values,text/plain;q=0.9,*/*;q=0.1")
            path, digest = _write_raw_blob(raw_dir, spec.key, f"labels-{language}", data, "tsv")
            labels_by_language[language] = _parse_codelist_tsv(data)
            raw_languages.append({"language": language, "url": url, "raw_path": path, "sha256": digest})

        normalized = _normalize_sdmx_scheme(spec, codelist, labels_by_language)
        normalized_schemes.append(normalized)
        source_manifest["schemes"].append(
            {
                "key": spec.key,
                "system": spec.system,
                "version": spec.version,
                "codelist_id": codelist["id"],
                "codelist_version": codelist.get("version") or "",
                "codelist_names": codelist.get("names", {}),
                "languages": raw_languages,
                "normalized": normalized["scheme"],
                "notes_status": "not_in_core_sdmx_codelist_snapshot",
            }
        )

    manifest_bytes = _canonical_json_bytes(source_manifest)
    manifest_digest = _sha256_bytes(manifest_bytes)
    manifest_path = raw_dir / f"manifest-{retrieved_at.replace(':', '').replace('-', '')}-{manifest_digest}.json"
    manifest_path.write_bytes(manifest_bytes)

    db_result = _write_database(
        output_db,
        base,
        normalized_schemes,
        retrieved_at=retrieved_at,
        replace=replace,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at": retrieved_at,
        "transport": "eurostat-sdmx-codelist",
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": manifest_digest,
        "schemes": [x["scheme"] for x in normalized_schemes],
        **db_result,
    }


def _catalog_v3_from_path(path: Path) -> tuple[str, list[ShowVocSchemeSpec]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != "official-classification-catalog-v0.3":
        raise ClassificationError("unsupported ShowVoc classification catalog schema")
    if obj.get("transport") != "eurostat-showvoc-sparql":
        raise ClassificationError("unsupported ShowVoc classification catalog transport")

    endpoint = str(obj.get("showvoc_endpoint") or "")
    _validate_https_endpoint(endpoint)

    languages = tuple(str(x).lower() for x in (obj.get("languages") or []))
    if not languages or len(set(languages)) != len(languages):
        raise ClassificationError("ShowVoc catalog languages must be a non-empty unique list")
    if any(not re.fullmatch(r"[a-z]{2}", x) for x in languages):
        raise ClassificationError("ShowVoc catalog language codes must be two lowercase letters")

    specs: list[ShowVocSchemeSpec] = []
    for item in obj.get("schemes") or []:
        level_counts = {int(k): int(v) for k, v in (item.get("expected_level_counts") or {}).items()}
        specs.append(
            ShowVocSchemeSpec(
                key=str(item["key"]),
                system=str(item["system"]),
                version=str(item["version"]),
                project=str(item["project"]),
                expected_item_count=int(item["expected_item_count"]),
                expected_levels=int(item["expected_levels"]),
                expected_level_counts=level_counts,
                required_languages=languages,
            )
        )

    if not specs:
        raise ClassificationError("ShowVoc classification catalog has no schemes")
    if len({s.key for s in specs}) != len(specs):
        raise ClassificationError("duplicate scheme key in ShowVoc catalog")

    for spec in specs:
        if not spec.project:
            raise ClassificationError(f"{spec.key} ShowVoc project is empty")
        if set(spec.expected_level_counts) != set(range(1, spec.expected_levels + 1)):
            raise ClassificationError(f"{spec.key} expected_level_counts must cover every level")
        if sum(spec.expected_level_counts.values()) != spec.expected_item_count:
            raise ClassificationError(f"{spec.key} expected level counts do not sum to expected_item_count")

    return endpoint, specs


def _parse_showvoc_json(data: bytes) -> list[dict[str, Any]]:
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"invalid ShowVoc JSON: {exc}") from exc

    st = obj.get("stresponse")
    if isinstance(st, dict) and st.get("exception"):
        raise ClassificationError(
            "ShowVoc service exception: "
            + str(st.get("msg") or st.get("exception"))
        )

    result = obj.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = None

    if isinstance(result, dict):
        sparql = result.get("sparql")
        if isinstance(sparql, dict):
            rows = sparql.get("results", {}).get("bindings")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]

        rows = result.get("results", {}).get("bindings")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]

    rows = obj.get("results", {}).get("bindings")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    if isinstance(st, dict):
        result = st.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = None
        if isinstance(result, dict):
            sparql = result.get("sparql")
            if isinstance(sparql, dict):
                rows = sparql.get("results", {}).get("bindings")
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            rows = result.get("results", {}).get("bindings")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]

    raise ClassificationError("ShowVoc JSON missing tuple result bindings")


def _showvoc_request(endpoint: str, project: str, query: str, *, timeout: float = 90.0) -> bytes:
    _validate_https_endpoint(endpoint)
    if not project:
        raise ClassificationError("ShowVoc project is required")

    url = f"{endpoint}?{urlencode({'ctx_project': project})}"
    payload = urlencode(
        {
            "query": query,
            "includeInferred": "false",
            "ql": "SPARQL",
            "maxExecTime": "60",
        }
    ).encode("utf-8")

    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "zse-value-scanner/0.4.11 classification-backbone",
        },
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310 - endpoint is validated above
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    raise ClassificationError(f"ShowVoc endpoint returned HTTP {status_code}")
                data = _read_bounded(response)
                _parse_showvoc_json(data)
                return data
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    raise ClassificationError(f"ShowVoc request failed after retries: {last_error}")


def _showvoc_paged_query(
    endpoint: str,
    project: str,
    base_query: str,
    *,
    raw_dir: Path,
    scheme_key: str,
    query_kind: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[PageResult]:
    if page_size < 1 or page_size > 10000:
        raise ClassificationError("page_size must be between 1 and 10000")
    if max_pages < 1 or max_pages > 1000:
        raise ClassificationError("max_pages must be between 1 and 1000")

    results: list[PageResult] = []
    for page in range(max_pages):
        query = f"{base_query}\nLIMIT {page_size}\nOFFSET {page * page_size}"
        data = _showvoc_request(endpoint, project, query)
        raw_path, digest = _write_raw(raw_dir, scheme_key, query_kind, page, data)
        rows = _parse_showvoc_json(data)
        results.append(PageResult(rows=rows, raw_path=raw_path, sha256=digest))
        if len(rows) < page_size:
            return results

    raise ClassificationError(f"{scheme_key}/{query_kind} exceeded max_pages={max_pages}")


def _showvoc_items_query() -> str:
    return f"""
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?concept ?code ?parent WHERE {{
  ?concept a skos:Concept ;
           skos:notation ?code .
  OPTIONAL {{ ?concept skos:broader ?parent . }}
}}
ORDER BY ?code ?concept ?parent
""".strip()


def _showvoc_labels_query(languages: tuple[str, ...]) -> str:
    filters = " || ".join(
        f'LANGMATCHES(LANG(?label), "{language}")'
        for language in languages
    )
    return f"""
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?concept ?code ?label WHERE {{
  ?concept a skos:Concept ;
           skos:notation ?code ;
           skos:prefLabel ?label .
  FILTER({filters})
}}
ORDER BY ?code ?label
""".strip()


def _normalize_showvoc_scheme(
    spec: ShowVocSchemeSpec,
    endpoint: str,
    item_pages: list[PageResult],
    label_pages: list[PageResult],
) -> dict[str, Any]:
    items_by_uri: dict[str, dict[str, Any]] = {}
    code_to_uri: dict[str, str] = {}

    for row in _rows_from_pages(item_pages):
        uri = _binding_value(row, "concept")
        code = _binding_value(row, "code")
        parent_uri = _binding_value(row, "parent")
        if not uri or not code:
            continue

        code = code.strip()
        level = _level_from_code(code)
        if level > spec.expected_levels:
            raise ClassificationError(f"{spec.key} code exceeds expected levels: {code}")

        current = items_by_uri.setdefault(
            uri,
            {"uri": uri, "code": code, "parent_uris": set()},
        )
        if current["code"] != code:
            raise ClassificationError(f"concept has conflicting codes: {uri}")
        if parent_uri:
            current["parent_uris"].add(parent_uri)

        existing_uri = code_to_uri.get(code)
        if existing_uri is not None and existing_uri != uri:
            raise ClassificationError(f"duplicate code {code!r} in {spec.key}")
        code_to_uri[code] = uri

    if len(items_by_uri) != spec.expected_item_count:
        raise ClassificationError(
            f"{spec.key} expected {spec.expected_item_count} classification items, "
            f"observed {len(items_by_uri)}"
        )

    level_counts: dict[int, int] = {
        level: 0 for level in range(1, spec.expected_levels + 1)
    }
    for item in items_by_uri.values():
        level_counts[_level_from_code(item["code"])] += 1

    if level_counts != spec.expected_level_counts:
        raise ClassificationError(
            f"{spec.key} level-count mismatch: "
            f"expected={spec.expected_level_counts!r} observed={level_counts!r}"
        )

    items: list[dict[str, Any]] = []
    for uri, item in items_by_uri.items():
        code = item["code"]
        level = _level_from_code(code)
        parent_uris = item["parent_uris"]

        if len(parent_uris) > 1:
            raise ClassificationError(
                f"{spec.key} code {code} has multiple direct parents: "
                f"{sorted(parent_uris)!r}"
            )

        parent_uri = next(iter(parent_uris), None)
        parent_code = None

        if level == 1:
            if parent_uri is not None:
                raise ClassificationError(f"{spec.key} root code {code} unexpectedly has a parent")
        else:
            if parent_uri is None:
                raise ClassificationError(f"{spec.key} non-root code {code} has no explicit parent")
            parent = items_by_uri.get(parent_uri)
            if parent is None:
                raise ClassificationError(
                    f"{spec.key} parent is outside project for {code}: {parent_uri}"
                )
            parent_code = parent["code"]
            parent_level = _level_from_code(parent_code)
            if parent_level != level - 1:
                raise ClassificationError(
                    f"{spec.key} parent-level mismatch for {code}: "
                    f"parent={parent_code} level={parent_level}"
                )

        items.append(
            {
                "code": code,
                "uri": uri,
                "parent_code": parent_code,
                "level": level,
            }
        )

    labels_by_code_language: dict[tuple[str, str], str] = {}
    required_languages = set(spec.required_languages)

    for row in _rows_from_pages(label_pages):
        uri = _binding_value(row, "concept")
        code = _binding_value(row, "code")
        label = _binding_value(row, "label")
        if not uri or not code or label is None:
            continue
        if uri not in items_by_uri:
            raise ClassificationError(f"label references unknown concept: {uri}")

        code = code.strip()
        if items_by_uri[uri]["code"] != code:
            raise ClassificationError(f"label code mismatch for concept: {uri}")

        language = _binding_lang(row, "label").lower()
        if language not in required_languages:
            continue

        key = (code, language)
        existing = labels_by_code_language.get(key)
        if existing is not None and existing != label:
            raise ClassificationError(
                f"{spec.key} has multiple prefLabels for {code}/{language}"
            )
        labels_by_code_language[key] = label

    missing_labels: list[tuple[str, str]] = []
    for code in sorted(code_to_uri):
        for language in spec.required_languages:
            if (code, language) not in labels_by_code_language:
                missing_labels.append((code, language))

    if missing_labels:
        raise ClassificationError(
            f"{spec.key} missing required prefLabels: "
            f"count={len(missing_labels)} first={missing_labels[:5]!r}"
        )

    expected_label_count = spec.expected_item_count * len(spec.required_languages)
    if len(labels_by_code_language) != expected_label_count:
        raise ClassificationError(
            f"{spec.key} expected {expected_label_count} required-language prefLabels, "
            f"observed {len(labels_by_code_language)}"
        )

    items.sort(key=lambda x: (x["level"], x["code"]))
    labels = [
        {
            "code": code,
            "language": language,
            "kind": "skos:prefLabel",
            "label": label,
        }
        for (code, language), label in labels_by_code_language.items()
    ]
    labels.sort(key=lambda x: (x["code"], x["language"], x["label"]))

    scheme_uri = f"{endpoint}?{urlencode({'ctx_project': spec.project})}"
    notes: list[dict[str, str]] = []
    digest_payload = {
        "source_project": spec.project,
        "items": items,
        "labels": labels,
        "notes": notes,
    }

    return {
        "scheme": {
            "key": spec.key,
            "system": spec.system,
            "version": spec.version,
            "scheme_uri": scheme_uri,
            "item_count": len(items),
            "label_count": len(labels),
            "note_count": 0,
            "languages": sorted(spec.required_languages),
            "normalized_sha256": _sha256_bytes(_canonical_json_bytes(digest_payload)),
            "source_project": spec.project,
            "expected_level_counts": spec.expected_level_counts,
        },
        "items": items,
        "labels": labels,
        "notes": notes,
    }


def _sync_showvoc(
    catalog: Path,
    output_db: Path,
    raw_dir: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    endpoint, specs = _catalog_v3_from_path(catalog)
    raw_dir = raw_dir.expanduser().resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = _utc_now()

    normalized_schemes: list[dict[str, Any]] = []
    source_manifest: dict[str, Any] = {
        "schema_version": "official-classification-raw-manifest-v0.3",
        "retrieved_at": retrieved_at,
        "transport": "eurostat-showvoc-sparql",
        "endpoint": endpoint,
        "schemes": [],
    }

    for spec in specs:
        item_pages = _showvoc_paged_query(
            endpoint,
            spec.project,
            _showvoc_items_query(),
            raw_dir=raw_dir,
            scheme_key=spec.key,
            query_kind="items",
        )
        label_pages = _showvoc_paged_query(
            endpoint,
            spec.project,
            _showvoc_labels_query(spec.required_languages),
            raw_dir=raw_dir,
            scheme_key=spec.key,
            query_kind="pref-labels",
        )

        normalized = _normalize_showvoc_scheme(
            spec,
            endpoint,
            item_pages,
            label_pages,
        )
        normalized_schemes.append(normalized)

        source_manifest["schemes"].append(
            {
                "key": spec.key,
                "system": spec.system,
                "version": spec.version,
                "project": spec.project,
                "expected_item_count": spec.expected_item_count,
                "expected_level_counts": spec.expected_level_counts,
                "required_languages": list(spec.required_languages),
                "item_pages": [
                    {
                        "raw_path": page.raw_path,
                        "sha256": page.sha256,
                        "row_count": len(page.rows),
                    }
                    for page in item_pages
                ],
                "label_pages": [
                    {
                        "raw_path": page.raw_path,
                        "sha256": page.sha256,
                        "row_count": len(page.rows),
                    }
                    for page in label_pages
                ],
                "normalized": normalized["scheme"],
                "notes_status": "not_in_v0_4_11_core_snapshot",
            }
        )

    manifest_bytes = _canonical_json_bytes(source_manifest)
    manifest_digest = _sha256_bytes(manifest_bytes)
    manifest_path = raw_dir / (
        f"manifest-{retrieved_at.replace(':', '').replace('-', '')}-"
        f"{manifest_digest}.json"
    )
    if not manifest_path.exists():
        tmp = manifest_path.with_suffix(manifest_path.suffix + f".tmp-{os.getpid()}")
        tmp.write_bytes(manifest_bytes)
        os.replace(tmp, manifest_path)

    db_result = _write_database(
        output_db,
        endpoint,
        normalized_schemes,
        retrieved_at=retrieved_at,
        replace=replace,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at": retrieved_at,
        "transport": "eurostat-showvoc-sparql",
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": manifest_digest,
        "schemes": [x["scheme"] for x in normalized_schemes],
        **db_result,
    }


def sync(catalog: Path, output_db: Path, raw_dir: Path, *, replace: bool = False) -> dict[str, Any]:
    obj = json.loads(catalog.read_text(encoding="utf-8"))
    schema_version = obj.get("schema_version")
    if schema_version == "official-classification-catalog-v0.3":
        return _sync_showvoc(catalog, output_db, raw_dir, replace=replace)
    if schema_version == "official-classification-catalog-v0.2":
        return _sync_sdmx(catalog, output_db, raw_dir, replace=replace)
    if schema_version == "official-classification-catalog-v0.1":
        return _sync_cellar(catalog, output_db, raw_dir, replace=replace)
    raise ClassificationError(f"unsupported classification catalog schema: {schema_version!r}")

def status(db_path: Path) -> dict[str, Any]:
    db_path = db_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        schemes = []
        for row in conn.execute(
            """
            SELECT scheme_key, system, version, scheme_uri, retrieved_at,
                   item_count, label_count, note_count, languages_json, normalized_sha256
            FROM classification_schemes ORDER BY scheme_key
            """
        ):
            schemes.append(
                {
                    "key": row[0],
                    "system": row[1],
                    "version": row[2],
                    "scheme_uri": row[3],
                    "retrieved_at": row[4],
                    "item_count": row[5],
                    "label_count": row[6],
                    "note_count": row[7],
                    "languages": json.loads(row[8]),
                    "normalized_sha256": row[9],
                }
            )
        links = conn.execute("SELECT COUNT(*) FROM classification_links").fetchone()[0]
        return {"database": str(db_path), "metadata": metadata, "schemes": schemes, "link_count": links}
    finally:
        conn.close()


def search(db_path: Path, term: str, *, language: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    if limit < 1 or limit > 200:
        raise ClassificationError("limit must be between 1 and 200")
    db_path = db_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
        SELECT l.scheme_key, s.system, s.version, l.code, l.language, l.kind, l.label, i.level, i.parent_code
        FROM classification_labels l
        JOIN classification_schemes s ON s.scheme_key = l.scheme_key
        JOIN classification_items i ON i.scheme_key = l.scheme_key AND i.code = l.code
        WHERE lower(l.label) LIKE lower(?)
        """
        params: list[Any] = [f"%{term}%"]
        if language:
            sql += " AND lower(l.language) = lower(?)"
            params.append(language)
        sql += " ORDER BY i.level, l.scheme_key, l.code, l.language, l.kind LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def show_code(db_path: Path, scheme_key: str, code: str) -> dict[str, Any] | None:
    db_path = db_path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        item = conn.execute(
            "SELECT * FROM classification_items WHERE scheme_key = ? AND code = ?",
            (scheme_key, code),
        ).fetchone()
        if item is None:
            return None
        labels = [dict(r) for r in conn.execute(
            "SELECT language, kind, label FROM classification_labels WHERE scheme_key = ? AND code = ? ORDER BY language, kind, label",
            (scheme_key, code),
        )]
        notes = [dict(r) for r in conn.execute(
            "SELECT language, kind, text FROM classification_notes WHERE scheme_key = ? AND code = ? ORDER BY language, kind, text",
            (scheme_key, code),
        )]
        outbound = [dict(r) for r in conn.execute(
            """
            SELECT target_scheme_key, target_code, relation, evidence_class, rationale
            FROM classification_links WHERE source_scheme_key = ? AND source_code = ?
            ORDER BY target_scheme_key, target_code
            """,
            (scheme_key, code),
        )]
        return {"item": dict(item), "labels": labels, "notes": notes, "outbound_links": outbound}
    finally:
        conn.close()


def _print_status(obj: dict[str, Any]) -> None:
    print(f"Reference database: {obj['database']}")
    print(f"Schema: {obj['metadata'].get('schema_version')}")
    print(f"Retrieved: {obj['metadata'].get('retrieved_at')}")
    print(f"Structural links: {obj['link_count']}")
    for s in obj["schemes"]:
        print(
            f"  {s['key']}: {s['item_count']} items | {s['label_count']} labels | "
            f"{s['note_count']} notes | languages={len(s['languages'])}"
        )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Official statistical classification reference backbone")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="Fetch official Eurostat classifications into a standalone SQLite reference DB")
    s.add_argument("--catalog", type=Path, required=True)
    s.add_argument("--output-db", type=Path, required=True)
    s.add_argument("--raw-dir", type=Path, required=True)
    s.add_argument("--replace", action="store_true", help="Explicitly replace an existing reference DB")
    s.add_argument("--json", action="store_true")

    st = sub.add_parser("status", help="Inspect a local reference DB without network access")
    st.add_argument("--db", type=Path, required=True)
    st.add_argument("--json", action="store_true")

    se = sub.add_parser("search", help="Search multilingual labels in a local reference DB")
    se.add_argument("--db", type=Path, required=True)
    se.add_argument("term")
    se.add_argument("--language")
    se.add_argument("--limit", type=int, default=30)
    se.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="Show one classification code, labels, notes and links")
    sh.add_argument("--db", type=Path, required=True)
    sh.add_argument("--scheme", required=True)
    sh.add_argument("--code", required=True)
    sh.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sync":
            result = sync(args.catalog, args.output_db, args.raw_dir, replace=args.replace)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            else:
                print(f"Classification reference DB: {result['output_db']}")
                print(f"SHA256: {result['sha256']}")
                print(f"Raw manifest: {result['source_manifest']}")
                for s in result["schemes"]:
                    print(
                        f"  {s['key']}: {s['item_count']} items | {s['label_count']} labels | "
                        f"{s['note_count']} notes | {len(s['languages'])} languages"
                    )
                print(f"CPA→NACE structural links: {result['structural_link_count']}")
                print("Core metadata database was not modified.")
            return 0
        if args.command == "status":
            result = status(args.db)
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) if args.json else "", end="")
            if not args.json:
                _print_status(result)
            return 0
        if args.command == "search":
            result = search(args.db, args.term, language=args.language, limit=args.limit)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            else:
                for r in result:
                    print(f"{r['scheme_key']} {r['code']} [{r['language'] or '-'}] {r['label']}")
            return 0
        if args.command == "show":
            result = show_code(args.db, args.scheme, args.code)
            if result is None:
                print(f"not found: {args.scheme} {args.code}", file=sys.stderr)
                return 4
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0
    except (ClassificationError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
