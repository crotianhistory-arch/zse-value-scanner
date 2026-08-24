from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from zse_tool.activity_mapping import NODE_BY_ID, TAXONOMY_VERSION

CATALOG_SCHEMA_VERSION = "activity-classification-mapping-catalog-v0.1"
DEFAULT_INPUT_LIMIT = 8 * 1024 * 1024

ALLOWED_RELATIONS = {
    "contains_activity",
    "product_family_match",
    "broader_activity_match",
    "close_activity_match",
}
ALLOWED_EVIDENCE_CLASSES = {
    "A3_ANALYTICAL_CLASSIFICATION_MAPPING",
}
REFERENCE_SCHEMES = {
    ("NACE", "2.1"): "NACE_REV_2_1",
    ("CPA", "2.2"): "CPA_2_2",
}
EXTERNAL_REFERENCE_HOSTS = {"unstats.un.org", "www.census.gov"}


class ClassificationMappingError(RuntimeError):
    pass


def _load_json_bounded(path: Path, *, max_bytes: int = DEFAULT_INPUT_LIMIT) -> dict[str, Any]:
    path = path.expanduser().resolve()
    size = path.stat().st_size
    if size > max_bytes:
        raise ClassificationMappingError(
            f"JSON input exceeds byte limit ({size} > {max_bytes}): {path}"
        )
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ClassificationMappingError("JSON input must contain an object")
    return obj


def _validate_external_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ClassificationMappingError("external classification source must use HTTPS")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ClassificationMappingError("unexpected external classification source URL")
    if parsed.hostname not in EXTERNAL_REFERENCE_HOSTS:
        raise ClassificationMappingError(
            f"external classification source host is not allow-listed: {parsed.hostname}"
        )


def load_catalog(path: Path) -> dict[str, Any]:
    obj = _load_json_bounded(path)
    if obj.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ClassificationMappingError(
            f"unsupported mapping catalog schema: {obj.get('schema_version')!r}"
        )
    if obj.get("taxonomy_version") != TAXONOMY_VERSION:
        raise ClassificationMappingError(
            f"mapping catalog taxonomy mismatch: expected {TAXONOMY_VERSION!r}"
        )

    rows = obj.get("mappings")
    if not isinstance(rows, list) or not rows:
        raise ClassificationMappingError("mapping catalog has no mappings")

    mapping_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ClassificationMappingError("mapping catalog rows must be objects")
        mapping_id = str(row.get("mapping_id") or "")
        node_id = str(row.get("node_id") or "")
        system = str(row.get("system") or "")
        version = str(row.get("version") or "")
        code = str(row.get("code") or "")
        relation = str(row.get("relation") or "")
        evidence_class = str(row.get("evidence_class") or "")
        validation = str(row.get("validation") or "")

        if not mapping_id or mapping_id in mapping_ids:
            raise ClassificationMappingError(
                f"mapping_id must be non-empty and unique: {mapping_id!r}"
            )
        mapping_ids.add(mapping_id)

        if node_id not in NODE_BY_ID:
            raise ClassificationMappingError(
                f"{mapping_id} references unknown taxonomy node: {node_id!r}"
            )
        if not system or not version or not code:
            raise ClassificationMappingError(
                f"{mapping_id} requires system/version/code"
            )
        if relation not in ALLOWED_RELATIONS:
            raise ClassificationMappingError(
                f"{mapping_id} has unsupported relation: {relation!r}"
            )
        if evidence_class not in ALLOWED_EVIDENCE_CLASSES:
            raise ClassificationMappingError(
                f"{mapping_id} has unsupported evidence class: {evidence_class!r}"
            )
        if not str(row.get("rationale") or "").strip():
            raise ClassificationMappingError(f"{mapping_id} requires a rationale")

        if validation == "reference_db":
            if (system, version) not in REFERENCE_SCHEMES:
                raise ClassificationMappingError(
                    f"{mapping_id} has unsupported reference DB scheme: {(system, version)!r}"
                )
        elif validation == "embedded_official_reference":
            label = str(row.get("label") or "")
            source_url = str(row.get("source_url") or "")
            if not label or not source_url:
                raise ClassificationMappingError(
                    f"{mapping_id} external target requires label and source_url"
                )
            _validate_external_source_url(source_url)
        else:
            raise ClassificationMappingError(
                f"{mapping_id} has unsupported validation mode: {validation!r}"
            )

    return obj


def _connect_reference_db(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.expanduser().resolve()
    if not db_path.exists():
        raise ClassificationMappingError(
            f"classification reference database not found: {db_path}"
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _reference_target(
    conn: sqlite3.Connection,
    *,
    system: str,
    version: str,
    code: str,
) -> dict[str, Any]:
    scheme_key = REFERENCE_SCHEMES[(system, version)]
    item = conn.execute(
        """
        SELECT scheme_key, code, uri, parent_code, level
        FROM classification_items
        WHERE scheme_key = ? AND code = ?
        """,
        (scheme_key, code),
    ).fetchone()
    if item is None:
        raise ClassificationMappingError(
            f"mapping target missing from reference DB: {scheme_key} {code}"
        )

    label_row = conn.execute(
        """
        SELECT label
        FROM classification_labels
        WHERE scheme_key = ? AND code = ?
          AND lower(language) = 'en'
          AND kind = 'skos:prefLabel'
        ORDER BY label
        LIMIT 1
        """,
        (scheme_key, code),
    ).fetchone()
    if label_row is None:
        raise ClassificationMappingError(
            f"mapping target lacks English skos:prefLabel: {scheme_key} {code}"
        )

    return {
        "validation": "reference_db",
        "scheme_key": scheme_key,
        "system": system,
        "version": version,
        "code": code,
        "label": label_row["label"],
        "uri": item["uri"],
        "parent_code": item["parent_code"],
        "level": item["level"],
    }


def _external_target(row: dict[str, Any]) -> dict[str, Any]:
    source_url = str(row["source_url"])
    _validate_external_source_url(source_url)
    return {
        "validation": "embedded_official_reference",
        "scheme_key": None,
        "system": str(row["system"]),
        "version": str(row["version"]),
        "code": str(row["code"]),
        "label": str(row["label"]),
        "uri": source_url,
        "parent_code": row.get("parent_code"),
        "level": row.get("level"),
        "source_url": source_url,
    }


def translate_node(
    node_id: str,
    catalog: dict[str, Any],
    classification_db: Path,
) -> dict[str, Any]:
    if node_id not in NODE_BY_ID:
        raise ClassificationMappingError(f"unknown taxonomy node: {node_id!r}")

    matching = [
        row for row in catalog["mappings"]
        if str(row.get("node_id")) == node_id
    ]

    conn = _connect_reference_db(classification_db)
    try:
        targets: list[dict[str, Any]] = []
        for row in matching:
            validation = str(row["validation"])
            if validation == "reference_db":
                target = _reference_target(
                    conn,
                    system=str(row["system"]),
                    version=str(row["version"]),
                    code=str(row["code"]),
                )
            else:
                target = _external_target(row)

            targets.append({
                "mapping_id": str(row["mapping_id"]),
                "evidence_class": str(row["evidence_class"]),
                "relation": str(row["relation"]),
                "rationale": str(row["rationale"]),
                "target": target,
            })
    finally:
        conn.close()

    targets.sort(
        key=lambda x: (
            x["target"]["system"],
            x["target"]["version"],
            x["target"]["code"],
            x["mapping_id"],
        )
    )

    node = NODE_BY_ID[node_id]
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "node": {
            "node_id": node.node_id,
            "label": node.label,
            "parent_id": node.parent_id,
            "description": node.description,
        },
        "targets": targets,
        "mapped": bool(targets),
    }


def translate_activity_profile(
    profile: dict[str, Any],
    catalog: dict[str, Any],
    classification_db: Path,
) -> dict[str, Any]:
    if profile.get("taxonomy_version") != TAXONOMY_VERSION:
        raise ClassificationMappingError(
            f"activity profile taxonomy mismatch: expected {TAXONOMY_VERSION!r}"
        )

    explicit = profile.get("explicit_activity_nodes")
    if not isinstance(explicit, list):
        raise ClassificationMappingError(
            "activity profile missing explicit_activity_nodes"
        )

    explicit_ids: list[str] = []
    for row in explicit:
        if not isinstance(row, dict):
            raise ClassificationMappingError(
                "explicit_activity_nodes rows must be objects"
            )
        node_id = str(row.get("node_id") or "")
        if node_id not in NODE_BY_ID:
            raise ClassificationMappingError(
                f"activity profile references unknown node: {node_id!r}"
            )
        if node_id not in explicit_ids:
            explicit_ids.append(node_id)

    translated = [
        translate_node(node_id, catalog, classification_db)
        for node_id in explicit_ids
    ]
    mapped = [row for row in translated if row["mapped"]]
    unmapped = [row["node"]["node_id"] for row in translated if not row["mapped"]]

    target_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for source in mapped:
        for mapping in source["targets"]:
            target = mapping["target"]
            key = (
                source["node"]["node_id"],
                target["system"],
                target["version"],
                target["code"],
                mapping["mapping_id"],
            )
            if key in seen:
                continue
            seen.add(key)
            target_rows.append({
                "source_node_id": source["node"]["node_id"],
                "source_node_label": source["node"]["label"],
                **mapping,
            })

    target_rows.sort(
        key=lambda x: (
            x["source_node_id"],
            x["target"]["system"],
            x["target"]["version"],
            x["target"]["code"],
        )
    )

    return {
        "mode": "read-only-activity-classification-translation",
        "taxonomy_version": TAXONOMY_VERSION,
        "mapping_catalog_schema": catalog["schema_version"],
        "mapping_catalog_id": catalog.get("catalog_id"),
        "entity": profile.get("entity"),
        "filing": profile.get("filing"),
        "source_explicit_node_ids": explicit_ids,
        "translated_source_nodes": [
            row["node"]["node_id"] for row in mapped
        ],
        "unmapped_explicit_node_ids": unmapped,
        "translations": target_rows,
        "policy": {
            "explicit_nodes_only": True,
            "derived_ancestors_translated": False,
            "company_classification_inferred": False,
            "exposure_percentage_inferred": False,
            "peer_or_competitor_decision_created": False,
        },
    }


def catalog_status(
    catalog: dict[str, Any],
    classification_db: Path,
) -> dict[str, Any]:
    node_ids = sorted({str(row["node_id"]) for row in catalog["mappings"]})
    translated = [
        translate_node(node_id, catalog, classification_db)
        for node_id in node_ids
    ]
    system_counts: dict[str, int] = {}
    for row in catalog["mappings"]:
        key = f"{row['system']} {row['version']}"
        system_counts[key] = system_counts.get(key, 0) + 1
    return {
        "catalog_id": catalog.get("catalog_id"),
        "schema_version": catalog["schema_version"],
        "taxonomy_version": catalog["taxonomy_version"],
        "mapped_node_count": len(node_ids),
        "mapping_count": len(catalog["mappings"]),
        "system_mapping_counts": dict(sorted(system_counts.items())),
        "nodes": translated,
    }


def _write_json_atomic(path: Path, obj: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Translate controlled activity nodes into official classification targets"
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", type=Path, required=True)
    common.add_argument("--classification-db", type=Path, required=True)

    st = sub.add_parser("status", parents=[common])
    st.add_argument("--json", action="store_true")

    nd = sub.add_parser("node", parents=[common])
    nd.add_argument("--node", required=True)
    nd.add_argument("--json", action="store_true")

    pr = sub.add_parser("profile", parents=[common])
    pr.add_argument("--input", type=Path, required=True)
    pr.add_argument("--output", type=Path)
    pr.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)

        if args.command == "status":
            result = catalog_status(catalog, args.classification_db)
        elif args.command == "node":
            result = translate_node(args.node, catalog, args.classification_db)
        elif args.command == "profile":
            profile = _load_json_bounded(args.input)
            result = translate_activity_profile(
                profile,
                catalog,
                args.classification_db,
            )
            if args.output:
                _write_json_atomic(args.output, result)
        else:
            return 1

        if getattr(args, "json", False) or args.command in {"node", "profile"}:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"Catalog: {result['catalog_id']} | "
                f"mapped nodes={result['mapped_node_count']} | "
                f"mappings={result['mapping_count']}"
            )
            for system, count in result["system_mapping_counts"].items():
                print(f"  {system}: {count}")
        return 0
    except (
        ClassificationMappingError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
