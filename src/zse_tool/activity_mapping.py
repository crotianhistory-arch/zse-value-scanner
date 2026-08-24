from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

TAXONOMY_VERSION = "energy-electrical-v0.1"
DEFAULT_INPUT_LIMIT = 8 * 1024 * 1024
DEFAULT_EXCERPT_CHARS = 900
MAPPING_SOURCE_CATEGORIES = {
    "principal_activity",
    "operating_segments",
    "revenue_business_line",
    "products_services",
}


@dataclass(frozen=True)
class TaxonomyNode:
    node_id: str
    label: str
    parent_id: str | None
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MappingRule:
    rule_id: str
    node_id: str
    aliases: tuple[str, ...] = ()
    require_all: tuple[str, ...] = ()
    source_categories: tuple[str, ...] = ()


NODES = (
    TaxonomyNode("activity", "Business activity", None, "Root node; not itself a peer conclusion."),
    TaxonomyNode("energy_infrastructure", "Energy infrastructure", "activity", "Infrastructure used to produce, move, manage or consume energy."),
    TaxonomyNode("electrical_grid", "Electrical grid", "energy_infrastructure", "Electricity transmission, distribution and grid-system activities."),
    TaxonomyNode("grid_solutions", "Grid solutions", "electrical_grid", "Integrated grid products, systems or solutions."),
    TaxonomyNode("grid_equipment", "Grid equipment", "electrical_grid", "Physical and control equipment used in transmission/distribution grids."),
    TaxonomyNode("transformers", "Transformers", "grid_equipment", "Power, distribution or special transformers."),
    TaxonomyNode("switchgear", "Switchgear", "grid_equipment", "Electrical switchgear and related switching equipment."),
    TaxonomyNode("substations", "Substations", "grid_equipment", "Substations and substation systems."),
    TaxonomyNode("grid_automation", "Grid automation", "grid_equipment", "Automation, digital control or monitoring of grid/substation assets."),
    TaxonomyNode("electrical_distribution", "Electrical distribution", "electrical_grid", "Electrical distribution equipment, systems and activities."),
    TaxonomyNode("secure_power", "Secure power", "electrical_grid", "Secure/critical electrical power supply activities."),
    TaxonomyNode("energy_management", "Energy management", "electrical_grid", "Systems and solutions for measuring, managing and optimising energy use."),
    TaxonomyNode("power_cables", "Power cables", "electrical_grid", "Power cable systems used in electricity transmission/distribution."),
    TaxonomyNode("high_voltage_cables", "High-voltage power cables", "power_cables", "High-voltage and HVDC power cable systems."),
    TaxonomyNode("medium_low_voltage_cables", "Medium/low-voltage power cables", "power_cables", "Medium- and low-voltage power cable systems."),
    TaxonomyNode("submarine_power_cables", "Submarine power cables", "power_cables", "Submarine/offshore power cable systems."),
    TaxonomyNode("cable_accessories", "Power-cable accessories", "power_cables", "Accessories and related components for power-cable systems."),
    TaxonomyNode("distributed_energy", "Distributed energy", "energy_infrastructure", "Distributed energy assets and end-use infrastructure."),
    TaxonomyNode("energy_storage", "Energy storage", "distributed_energy", "Battery or other electricity-storage systems."),
    TaxonomyNode("ev_charging", "EV charging", "distributed_energy", "Electric-vehicle charging equipment and systems."),
    TaxonomyNode("renewable_generation", "Renewable generation", "energy_infrastructure", "Equipment and services for renewable electricity generation."),
    TaxonomyNode("wind_energy", "Wind energy", "renewable_generation", "Wind-energy equipment, projects and services."),
    TaxonomyNode("wind_turbines", "Wind turbines", "wind_energy", "Onshore/offshore wind turbine design, manufacture or sale."),
    TaxonomyNode("wind_power_plants", "Wind power plants", "wind_energy", "Construction, installation or delivery of wind power plants."),
    TaxonomyNode("wind_services", "Wind services", "wind_energy", "Operation, maintenance, optimisation and related wind services."),
    TaxonomyNode("wind_project_development", "Wind project development", "wind_energy", "Greenfield development, maturation, permitting, grid connection and offtake work."),
    TaxonomyNode("electrification", "Electrification", "energy_infrastructure", "Broad electrification products, systems or solutions."),
    TaxonomyNode("automation", "Automation", "activity", "Industrial or building automation and control."),
    TaxonomyNode("industrial_automation", "Industrial automation", "automation", "Industrial automation, process control and industrial-control software."),
    TaxonomyNode("building_automation", "Building automation", "automation", "Building control, automation and related systems."),
    TaxonomyNode("digital_connectivity", "Digital/connectivity infrastructure", "activity", "Digital infrastructure and connectivity-related products or systems."),
    TaxonomyNode("data_center_infrastructure", "Data-centre infrastructure", "digital_connectivity", "Infrastructure products/solutions for data centres."),
    TaxonomyNode("digital_solutions", "Digital solutions", "digital_connectivity", "Reported digital-solutions business activities."),
    TaxonomyNode("telecom_cables", "Telecom cables", "digital_connectivity", "Telecommunications cable systems, including submarine telecom cable activities."),
)
NODE_BY_ID = {node.node_id: node for node in NODES}

RULES = (
    MappingRule("R-GRID-001", "electrical_grid", aliases=("electricity grid", "power grid")),
    MappingRule("R-GRID-002", "grid_solutions", aliases=("smart grid solutions", "smart grid solution")),
    MappingRule("R-GRID-003", "substations", aliases=("substation", "substations", "standardised substations", "standardized substations")),
    MappingRule("R-GRID-004", "transformers", aliases=("power transformer", "power transformers", "distribution transformer", "distribution transformers", "transformer", "transformers")),
    MappingRule("R-GRID-005", "switchgear", aliases=("switchgear",)),
    MappingRule("R-GRID-006", "grid_automation", aliases=("grid automation", "substation automation")),
    MappingRule("R-GRID-007", "electrical_distribution", aliases=("electrical distribution", "distribution électrique", "distribution electrique")),
    MappingRule("R-GRID-008", "secure_power", aliases=("secure power", "alimentation électrique sécurisée", "alimentation electrique securisee")),
    MappingRule("R-GRID-009", "energy_management", aliases=("energy management", "gestion de l'énergie", "gestion de l’énergie", "gestion de lenergie")),
    MappingRule("R-CABLE-001", "power_cables", aliases=("power cable", "power cables", "power and telecom cables")),
    MappingRule("R-CABLE-002", "high_voltage_cables", aliases=("high voltage power cable", "high-voltage power cable", "high voltage cable", "high-voltage cable", "high voltage direct current", "hvdc")),
    MappingRule("R-CABLE-003", "medium_low_voltage_cables", aliases=("medium voltage power cable", "medium-voltage power cable", "low voltage power cable", "low-voltage power cable")),
    MappingRule("R-CABLE-004", "submarine_power_cables", aliases=("submarine power", "submarine power cable", "submarine power cables")),
    MappingRule("R-CABLE-005", "cable_accessories", require_all=("cable", "accessories")),
    MappingRule("R-DIST-001", "energy_storage", aliases=("energy storage systems", "energy storage", "battery storage")),
    MappingRule("R-DIST-002", "ev_charging", aliases=("ev charging", "electric vehicle charging", "charging infrastructure")),
    MappingRule("R-AUTO-001", "industrial_automation", aliases=("industrial automation", "industrial control", "automatismes industriels", "contrôle industriel", "controle industriel")),
    MappingRule("R-AUTO-002", "building_automation", aliases=("building automation", "building control"), require_all=("automatismes", "bâtiments")),
    MappingRule("R-DIGI-001", "data_center_infrastructure", aliases=("data centers", "data centres", "centres de données", "centres de donnees")),
    MappingRule("R-DIGI-002", "digital_solutions", aliases=("digital solutions",)),
    MappingRule("R-DIGI-003", "telecom_cables", aliases=("telecom cables", "telecommunications cables", "submarine telecom")),
    MappingRule("R-ENER-001", "electrification", aliases=("electrification",)),
    MappingRule("R-WIND-001", "wind_turbines", aliases=("wind turbine", "wind turbines")),
    MappingRule("R-WIND-002", "wind_power_plants", aliases=("wind power plant", "wind power plants")),
    MappingRule("R-WIND-003", "wind_services", aliases=("wind energy service", "wind energy service solutions", "fleet optimisation", "fleet optimization")),
    MappingRule("R-WIND-004", "wind_project_development", aliases=("greenfield project development", "project maturation", "quality project pipeline")),
)


def _match_forms(value: str) -> tuple[str, str]:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    asciiish = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spaced = re.sub(r"[^a-z0-9]+", " ", asciiish)
    spaced = " ".join(spaced.split())
    compact = re.sub(r"[^a-z0-9]", "", asciiish)
    return spaced, compact


def _term_matches(text_forms: tuple[str, str], term: str) -> bool:
    spaced, compact = text_forms
    term_spaced, term_compact = _match_forms(term)
    if term_spaced and term_spaced in spaced:
        return True
    return len(term_compact) >= 5 and term_compact in compact


def rule_matches(rule: MappingRule, text: str, source_category: str) -> tuple[bool, list[str]]:
    if rule.source_categories and source_category not in rule.source_categories:
        return False, []
    forms = _match_forms(text)
    hits = [term for term in rule.aliases if _term_matches(forms, term)]
    required_hits = [term for term in rule.require_all if _term_matches(forms, term)]
    alias_match = bool(hits)
    required_match = bool(rule.require_all) and len(required_hits) == len(rule.require_all)
    if rule.aliases and rule.require_all:
        matched = alias_match or required_match
    elif rule.aliases:
        matched = alias_match
    else:
        matched = required_match
    return matched, hits + required_hits


def ancestor_ids(node_id: str) -> list[str]:
    if node_id not in NODE_BY_ID:
        raise ValueError(f"unknown taxonomy node: {node_id}")
    out: list[str] = []
    current = NODE_BY_ID[node_id]
    while current.parent_id is not None:
        out.append(current.parent_id)
        current = NODE_BY_ID[current.parent_id]
    return out


def node_depth(node_id: str) -> int:
    return len(ancestor_ids(node_id))


def node_path(node_id: str) -> list[str]:
    ids = [node_id] + ancestor_ids(node_id)
    ids.reverse()
    return ids


def validate_taxonomy() -> None:
    if len(NODE_BY_ID) != len(NODES):
        raise ValueError("duplicate taxonomy node_id")
    for node in NODES:
        if node.parent_id is not None and node.parent_id not in NODE_BY_ID:
            raise ValueError(f"missing parent {node.parent_id} for {node.node_id}")
        seen = {node.node_id}
        current = node
        while current.parent_id is not None:
            if current.parent_id in seen:
                raise ValueError(f"taxonomy cycle at {node.node_id}")
            seen.add(current.parent_id)
            current = NODE_BY_ID[current.parent_id]
    rule_ids: set[str] = set()
    for rule in RULES:
        if rule.rule_id in rule_ids:
            raise ValueError(f"duplicate mapping rule_id: {rule.rule_id}")
        rule_ids.add(rule.rule_id)
        if rule.node_id not in NODE_BY_ID:
            raise ValueError(f"rule {rule.rule_id} references unknown node {rule.node_id}")
        if not rule.aliases and not rule.require_all:
            raise ValueError(f"rule {rule.rule_id} has no match terms")


validate_taxonomy()


def _validate_annual_baseline(payload: dict[str, Any]) -> None:
    if payload.get("mode") != "read-only-annual-activity-baseline":
        raise ValueError("mapping input must be a v0.4.4 annual activity baseline manifest")
    selection = payload.get("selection") or {}
    annuality = selection.get("annuality") or {}
    if annuality.get("annual_like") is not True:
        raise ValueError("mapping input is not marked as an annual-like activity baseline")
    if not isinstance((payload.get("tagged_activity") or {}).get("reported_activity_facts"), list):
        raise ValueError("mapping input does not contain reported_activity_facts")


def _excerpt(text: str, terms: Iterable[str], limit: int = DEFAULT_EXCERPT_CHARS) -> str:
    clean = " ".join(text.split())
    folded = clean.casefold()
    positions = [folded.find(term.casefold()) for term in terms if term and folded.find(term.casefold()) >= 0]
    if not positions:
        return clean[:limit]
    pos = min(positions)
    lo = max(0, pos - limit // 3)
    hi = min(len(clean), lo + limit)
    return clean[lo:hi]


def map_annual_activity(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_annual_baseline(payload)
    facts = payload["tagged_activity"]["reported_activity_facts"]
    mappings: list[dict[str, Any]] = []
    explicit_nodes: set[str] = set()

    for fact in facts:
        category = str(fact.get("category") or "")
        if category not in MAPPING_SOURCE_CATEGORIES:
            continue
        text = str(fact.get("text") or "")
        if not text.strip():
            continue
        for rule in RULES:
            matched, terms = rule_matches(rule, text, category)
            if not matched:
                continue
            explicit_nodes.add(rule.node_id)
            mappings.append({
                "evidence_class": "A1_ANALYTICAL_ACTIVITY_MAPPING",
                "taxonomy_version": TAXONOMY_VERSION,
                "node_id": rule.node_id,
                "node_label": NODE_BY_ID[rule.node_id].label,
                "node_path": node_path(rule.node_id),
                "rule_id": rule.rule_id,
                "matched_terms": sorted(set(terms)),
                "source_fact_id": fact.get("fact_id"),
                "source_concept": fact.get("concept"),
                "source_category": category,
                "source_period": fact.get("period"),
                "source_language": fact.get("language"),
                "evidence_excerpt": _excerpt(text, terms),
            })

    all_nodes = set(explicit_nodes)
    ancestor_sources: dict[str, set[str]] = {}
    for node_id in explicit_nodes:
        for ancestor in ancestor_ids(node_id):
            all_nodes.add(ancestor)
            ancestor_sources.setdefault(ancestor, set()).add(node_id)

    explicit_summary = [
        {
            "node_id": node_id,
            "label": NODE_BY_ID[node_id].label,
            "depth": node_depth(node_id),
            "path": node_path(node_id),
            "mapping_count": sum(1 for m in mappings if m["node_id"] == node_id),
        }
        for node_id in sorted(explicit_nodes)
    ]
    derived_summary = [
        {
            "evidence_class": "A2_DERIVED_TAXONOMY_ANCESTOR",
            "node_id": node_id,
            "label": NODE_BY_ID[node_id].label,
            "depth": node_depth(node_id),
            "path": node_path(node_id),
            "derived_from": sorted(ancestor_sources[node_id]),
        }
        for node_id in sorted(ancestor_sources)
        if node_id not in explicit_nodes
    ]

    return {
        "mode": "read-only-analytical-activity-mapping",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
            "mapping_is_analytical_not_reported_fact": True,
            "taxonomy_ancestors_are_derived_not_reported": True,
            "whole_company_or_segment_exposure_not_inferred": True,
        },
        "taxonomy_version": TAXONOMY_VERSION,
        "entity": payload.get("entity"),
        "filing": payload.get("filing"),
        "source_manifest_mode": payload.get("mode"),
        "explicit_activity_nodes": explicit_summary,
        "derived_ancestor_nodes": derived_summary,
        "mappings": mappings,
        "all_node_ids": sorted(all_nodes),
        "provenance": {
            "annual_baseline_selection": payload.get("selection"),
            "source_xbrl_json": (payload.get("provenance") or {}).get("xbrl_json"),
            "source_report_package": (payload.get("provenance") or {}).get("original_report_package"),
        },
    }


def pairwise_overlaps(mapped_profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, left in enumerate(mapped_profiles):
        for right in mapped_profiles[i + 1 :]:
            left_explicit = {x["node_id"] for x in left.get("explicit_activity_nodes") or []}
            right_explicit = {x["node_id"] for x in right.get("explicit_activity_nodes") or []}
            left_all = set(left.get("all_node_ids") or [])
            right_all = set(right.get("all_node_ids") or [])
            shared_explicit = sorted(left_explicit & right_explicit)
            shared_derived_only = sorted((left_all & right_all) - set(shared_explicit))
            rows.append({
                "left_lei": (left.get("entity") or {}).get("lei"),
                "left_name": (left.get("entity") or {}).get("reported_name"),
                "right_lei": (right.get("entity") or {}).get("lei"),
                "right_name": (right.get("entity") or {}).get("reported_name"),
                "shared_explicit_nodes": [
                    {"node_id": n, "label": NODE_BY_ID[n].label, "depth": node_depth(n)}
                    for n in shared_explicit
                ],
                "shared_specific_ancestors": [
                    {"node_id": n, "label": NODE_BY_ID[n].label, "depth": node_depth(n)}
                    for n in shared_derived_only
                    if node_depth(n) >= 2
                ],
                "shared_broad_ancestors": [
                    {"node_id": n, "label": NODE_BY_ID[n].label, "depth": node_depth(n)}
                    for n in shared_derived_only
                    if node_depth(n) < 2
                ],
                "note": "Intersection only; no similarity score, rank or peer conclusion.",
            })
    return rows


def _load_json_bounded(path: Path, *, max_bytes: int = DEFAULT_INPUT_LIMIT) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(f"input manifest exceeds byte limit: {size} > {max_bytes}")
    payload = json.loads(p.read_text())
    if not isinstance(payload, dict):
        raise ValueError("input manifest must contain a JSON object")
    return payload


def _write_json_atomic(payload: dict[str, Any], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return target


def load_input_manifests(paths: list[Path], input_dir: Path | None, *, max_bytes: int) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[Path] = [Path(p) for p in paths]
    if input_dir is not None:
        root = Path(input_dir).expanduser().resolve()
        candidates.extend(sorted(p for p in root.glob("*.json") if p.name != "batch-summary.json"))
    unique: list[Path] = []
    seen: set[Path] = set()
    for raw in candidates:
        p = raw.expanduser().resolve()
        if p not in seen:
            seen.add(p)
            unique.append(p)
    if not unique:
        raise ValueError("no annual-baseline input manifests supplied")
    return [(p, _load_json_bounded(p, max_bytes=max_bytes)) for p in unique]


def build_mapping_batch(items: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for source_path, payload in items:
        try:
            mapped = map_annual_activity(payload)
            profiles.append(mapped)
            rows.append({
                "state": "ok",
                "source_manifest": str(source_path),
                "lei": (mapped.get("entity") or {}).get("lei"),
                "entity_name": (mapped.get("entity") or {}).get("reported_name"),
                "period_end": (mapped.get("filing") or {}).get("period_end"),
                "explicit_node_ids": [x["node_id"] for x in mapped["explicit_activity_nodes"]],
                "mapping_count": len(mapped["mappings"]),
                "profile": mapped,
            })
        except Exception as exc:
            rows.append({
                "state": "error",
                "source_manifest": str(source_path),
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
    return {
        "mode": "read-only-analytical-activity-mapping-batch",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
        },
        "taxonomy_version": TAXONOMY_VERSION,
        "requested": len(items),
        "succeeded": len(profiles),
        "failed": sum(1 for r in rows if r["state"] == "error"),
        "results": rows,
        "pairwise_overlaps": pairwise_overlaps(profiles),
    }


def write_batch_outputs(batch: dict[str, Any], output_dir: Path) -> Path:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    for row in batch["results"]:
        clean = dict(row)
        profile = clean.pop("profile", None)
        if profile is not None:
            lei = str(row.get("lei") or "unknown")
            path = root / f"{lei}.mapped.json"
            _write_json_atomic(profile, path)
            clean["mapped_profile_path"] = str(path)
        summary_rows.append(clean)
    summary = dict(batch)
    summary["results"] = summary_rows
    summary["taxonomy"] = [node.to_dict() for node in NODES]
    return _write_json_atomic(summary, root / "mapping-summary.json")


def _print_batch(batch: dict[str, Any]) -> None:
    print(f"Requested: {batch['requested']}  Succeeded: {batch['succeeded']}  Failed: {batch['failed']}")
    print("STATE  PERIOD      LEI                   EXPLICIT  ENTITY / ERROR")
    for row in batch["results"]:
        if row["state"] == "ok":
            print(
                f"ok     {(row.get('period_end') or '-'):11} {(row.get('lei') or '-'):20} "
                f"{len(row.get('explicit_node_ids') or []):>8}  {row.get('entity_name') or '-'}"
            )
        else:
            print(f"error  {'-':11} {'-':20} {'-':>8}  {row.get('error_type')}: {row.get('error')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.activity_mapping",
        description="Map annual ESEF activity evidence to a reviewable multi-label taxonomy without peer scoring.",
    )
    parser.add_argument("--input", action="append", default=[], help="v0.4.4 annual-baseline JSON manifest; repeatable")
    parser.add_argument("--input-dir", help="Directory containing v0.4.4 per-LEI annual-baseline JSON manifests")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--input-max-mib", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.input and not args.input_dir:
        parser.error("provide --input and/or --input-dir")
    if args.input_max_mib < 1:
        parser.error("--input-max-mib must be positive")

    items = load_input_manifests(
        [Path(p) for p in args.input],
        Path(args.input_dir) if args.input_dir else None,
        max_bytes=args.input_max_mib * 1024 * 1024,
    )
    batch = build_mapping_batch(items)
    summary = write_batch_outputs(batch, Path(args.output_dir))
    if args.json:
        printable = dict(batch)
        printable["results"] = [{k: v for k, v in row.items() if k != "profile"} for row in batch["results"]]
        print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_batch(batch)
        print(f"Mapping summary: {summary}")
    return 0 if batch["succeeded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
