from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 4 * 1024 * 1024
ALLOWED_BASIS = {"FY", "H1", "Q1", "Q1-Q3"}
ALLOWED_PRECISION = {"exact", "rounded", "lower_bound", "upper_bound"}


class TrajectoryError(ValueError):
    pass


def _read_json_bounded(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise TrajectoryError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TrajectoryError("top-level JSON must be an object")
    return data


def _scope_key(scope: dict[str, Any] | None) -> tuple[str, str]:
    scope = scope or {}
    return str(scope.get("level", "group")), str(scope.get("name", ""))


def _market_key(market: dict[str, Any] | None) -> tuple[str, str]:
    market = market or {}
    return str(market.get("iso2", "")), str(market.get("name", ""))


def _validate_observation(obs: dict[str, Any], idx: int, sources: dict[str, Any]) -> None:
    required = ["observation_id", "metric", "period", "value", "evidence_class"]
    missing = [k for k in required if k not in obs]
    if missing:
        raise TrajectoryError(f"observation {idx} missing: {', '.join(missing)}")

    period = obs["period"]
    if not isinstance(period, dict):
        raise TrajectoryError(f"observation {idx} period must be object")
    if period.get("basis") not in ALLOWED_BASIS:
        raise TrajectoryError(f"observation {idx} unsupported period basis: {period.get('basis')!r}")
    if not isinstance(period.get("year"), int):
        raise TrajectoryError(f"observation {idx} period.year must be int")
    if not period.get("label") or not period.get("end"):
        raise TrajectoryError(f"observation {idx} period requires label and end")

    value = obs["value"]
    if not isinstance(value, dict) or not isinstance(value.get("amount"), (int, float)):
        raise TrajectoryError(f"observation {idx} value.amount must be numeric")
    if value.get("precision", "exact") not in ALLOWED_PRECISION:
        raise TrajectoryError(f"observation {idx} unsupported precision")
    if not value.get("unit"):
        raise TrajectoryError(f"observation {idx} value.unit required")

    source_id = obs.get("source_id")
    inline_source = obs.get("source")
    if not source_id and not inline_source:
        raise TrajectoryError(f"observation {idx} requires source_id or source")
    if source_id and source_id not in sources:
        raise TrajectoryError(f"observation {idx} unknown source_id: {source_id}")

    market = obs.get("market")
    if market is not None:
        iso2 = str(market.get("iso2", ""))
        if len(iso2) != 2 or iso2.upper() != iso2:
            raise TrajectoryError(f"observation {idx} market.iso2 must be uppercase ISO2")


def _series_identity(obs: dict[str, Any]) -> tuple[Any, ...]:
    value = obs["value"]
    return (
        obs["metric"],
        obs["period"]["basis"],
        *_scope_key(obs.get("scope")),
        *_market_key(obs.get("market")),
        value.get("currency", ""),
        value.get("unit", ""),
        value.get("scale", ""),
    )


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _cagr(first: float, last: float, years: int) -> float | None:
    if years <= 0 or first <= 0 or last < 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _monotonic_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "INSUFFICIENT"
    diffs = [b - a for a, b in zip(values, values[1:])]
    if all(d > 0 for d in diffs):
        return "INCREASING"
    if all(d < 0 for d in diffs):
        return "DECREASING"
    if all(d == 0 for d in diffs):
        return "UNCHANGED"
    return "MIXED"


def build_trajectory(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "commercial-trajectory-evidence-v0.1":
        raise TrajectoryError("unsupported schema_version")
    company = data.get("company")
    observations = data.get("observations")
    sources = data.get("sources", {})
    if not isinstance(company, dict) or not isinstance(observations, list):
        raise TrajectoryError("company object and observations array are required")
    if not isinstance(sources, dict):
        raise TrajectoryError("sources must be an object")
    if len(observations) > 10000:
        raise TrajectoryError("too many observations")

    seen_ids: set[str] = set()
    for idx, obs in enumerate(observations):
        if not isinstance(obs, dict):
            raise TrajectoryError(f"observation {idx} must be object")
        _validate_observation(obs, idx, sources)
        oid = str(obs["observation_id"])
        if oid in seen_ids:
            raise TrajectoryError(f"duplicate observation_id: {oid}")
        seen_ids.add(oid)

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        grouped[_series_identity(obs)].append(obs)

    series_out: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda r: (r["period"]["year"], r["period"]["end"]))
        metric, basis, scope_level, scope_name, iso2, market_name, currency, unit, scale = key

        points: list[dict[str, Any]] = []
        prev_by_year: dict[int, dict[str, Any]] = {}
        for row in rows:
            point = dict(row)
            year = row["period"]["year"]
            prior = prev_by_year.get(year - 1)
            point["derived"] = {}
            if prior is not None:
                growth = _pct_change(float(row["value"]["amount"]), float(prior["value"]["amount"]))
                if growth is not None:
                    point["derived"]["yoy_pct"] = round(growth, 4)
                    point["derived"]["yoy_evidence_class"] = "D1_DETERMINISTIC_DERIVATION"
                    point["derived"]["yoy_prior_observation_id"] = prior["observation_id"]
            prev_by_year[year] = row
            points.append(point)

        amounts = [float(r["value"]["amount"]) for r in rows]
        summary: dict[str, Any] = {
            "observation_count": len(rows),
            "monotonic_direction": _monotonic_direction(amounts),
            "direction_evidence_class": "D1_DETERMINISTIC_DERIVATION",
        }
        if len(rows) >= 2:
            first, last = rows[0], rows[-1]
            change = _pct_change(float(last["value"]["amount"]), float(first["value"]["amount"]))
            if change is not None:
                summary["first_to_latest_pct"] = round(change, 4)
            if basis == "FY":
                years = last["period"]["year"] - first["period"]["year"]
                cagr = _cagr(float(first["value"]["amount"]), float(last["value"]["amount"]), years)
                if cagr is not None:
                    summary["cagr_pct"] = round(cagr, 4)
                    summary["cagr_years"] = years
                    summary["cagr_evidence_class"] = "D1_DETERMINISTIC_DERIVATION"

        series_out.append(
            {
                "series": {
                    "metric": metric,
                    "period_basis": basis,
                    "scope": {"level": scope_level, "name": scope_name},
                    "market": ({"iso2": iso2, "name": market_name} if iso2 else None),
                    "value_schema": {"currency": currency or None, "unit": unit, "scale": scale or None},
                },
                "points": points,
                "summary": summary,
            }
        )

    series_out.sort(
        key=lambda s: (
            s["series"]["metric"],
            (s["series"]["market"] or {}).get("iso2", ""),
            s["series"]["scope"]["name"],
            s["series"]["period_basis"],
        )
    )

    return {
        "schema_version": "commercial-trajectory-v0.1",
        "company": company,
        "observation_count": len(observations),
        "source_count": len(sources),
        "sources": sources,
        "series_count": len(series_out),
        "series": series_out,
        "policy": {
            "reported_values_are_preserved": True,
            "derived_growth_is_deterministic": True,
            "different_period_bases_are_never_mixed": True,
            "market_revenue_is_not_market_share": True,
            "competitor_displacement_is_not_inferred": True,
            "automatic_database_writes": False,
            "llm_used": False,
        },
    }


def _filtered(result: dict[str, Any], market: str | None, metric: str | None) -> dict[str, Any]:
    if not market and not metric:
        return result
    keep = []
    for s in result["series"]:
        sm = s["series"]
        if market and (sm.get("market") or {}).get("iso2") != market:
            continue
        if metric and sm.get("metric") != metric:
            continue
        keep.append(s)
    out = dict(result)
    out["series"] = keep
    out["series_count"] = len(keep)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build read-only historical commercial trajectories")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--market", help="optional ISO2 filter")
    p.add_argument("--metric", help="optional metric filter")
    args = p.parse_args(argv)

    data = _read_json_bounded(args.input)
    result = _filtered(build_trajectory(data), args.market, args.metric)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output.with_suffix(args.output.suffix + ".tmp")
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(args.output)

    print(f"Company: {result['company'].get('name')} ({result['company'].get('ticker', '-')})")
    print(f"Observations: {result['observation_count']} | series: {result['series_count']}")
    for s in result["series"]:
        meta = s["series"]
        market = (meta.get("market") or {}).get("iso2") or "ALL"
        scope = meta["scope"]["name"] or meta["scope"]["level"]
        summary = s["summary"]
        bits = [summary["monotonic_direction"]]
        if "cagr_pct" in summary:
            bits.append(f"CAGR={summary['cagr_pct']:.2f}%")
        print(f"  {meta['metric']} {market} {scope} [{meta['period_basis']}]: " + " ".join(bits))
        for pt in s["points"]:
            yoy = pt.get("derived", {}).get("yoy_pct")
            yoy_txt = "" if yoy is None else f" yoy={yoy:+.2f}%"
            v = pt["value"]
            print(f"    {pt['period']['label']}: {v['amount']} {v.get('currency','')} {v.get('scale','')} {v['unit']}{yoy_txt}")
    print("Read-only trajectory: no market-share, competitor-displacement or database decision was persisted.")
    if args.output:
        print(f"Commercial trajectory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
