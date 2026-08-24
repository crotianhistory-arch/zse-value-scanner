from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .errors import ZseToolError
from .storage import Database

DATA_DIR = Path(__file__).with_name("data")
TAXONOMY_FILE = DATA_DIR / "official_taxonomy_seed_v0_3_0.json"
PROFILE_DIR = DATA_DIR / "company_profiles"


class ProfileValidationError(ZseToolError):
    """Raised when a company-intelligence profile fails deterministic validation."""


@dataclass(frozen=True)
class PeerCandidate:
    ticker: str
    peer_type: str
    status: str
    eligible: bool
    score: float | None
    activity_score: float
    business_model_score: float | None
    investment_score: float | None
    size_score: float | None
    market_cap_score: float | None
    exact_overlap: list[str]
    group_overlap: list[str]
    division_overlap: list[str]
    features_compared: int
    feature_notes: list[str]
    explanation: str


@dataclass(frozen=True)
class _ActivityMatch:
    score: float
    exact_mass: float
    group_mass: float
    division_mass: float
    exact_overlap: list[str]
    group_overlap: list[str]
    division_overlap: list[str]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileValidationError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileValidationError(f"Invalid JSON in {path}: {exc}") from exc


def ensure_bundled_taxonomies(db: Database) -> None:
    """Load a small, official, versioned taxonomy seed into SQLite.

    v0.3.0 deliberately bundles only the codes needed by the first grounded
    company profile.  The schema is generic and can ingest the complete NACE,
    NKD, CPA or other official lists later without changing company records.
    """
    payload = _load_json(TAXONOMY_FILE)
    for scheme in payload.get("schemes", []):
        db.upsert_classification_scheme(scheme)
    for code in payload.get("codes", []):
        db.upsert_classification_code(code)


def bundled_profile_path(ticker: str) -> Path:
    return PROFILE_DIR / f"{ticker.lower()}.json"


def load_bundled_profile(ticker: str) -> dict[str, Any]:
    path = bundled_profile_path(ticker)
    if not path.exists():
        available = ", ".join(sorted(p.stem.upper() for p in PROFILE_DIR.glob("*.json"))) or "none"
        raise ProfileValidationError(
            f"No bundled profile for {ticker.upper()}. Available bundled profiles: {available}"
        )
    return _load_json(path)


def _require(obj: dict[str, Any], key: str, context: str) -> Any:
    value = obj.get(key)
    if value is None or value == "":
        raise ProfileValidationError(f"{context}: missing required field {key!r}")
    return value


def _confidence(value: Any, context: str) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(f"{context}: invalid confidence {value!r}") from exc
    if not 0.0 <= x <= 1.0:
        raise ProfileValidationError(f"{context}: confidence must be between 0 and 1")
    return x


def _optional_share(value: Any, context: str) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(f"{context}: invalid share/weight {value!r}") from exc
    if not 0.0 <= x <= 1.0:
        raise ProfileValidationError(f"{context}: share/weight must be between 0 and 1")
    return x


def validate_profile_bundle(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a profile before it can be persisted.

    This is the future LLM safety boundary: an LLM may propose a JSON bundle,
    but the deterministic application accepts only known official taxonomy
    codes and requires dated evidence for every material assignment.
    """
    ensure_bundled_taxonomies(db)
    if not isinstance(payload, dict):
        raise ProfileValidationError("Profile bundle must be a JSON object")

    profile = payload.get("profile")
    if not isinstance(profile, dict):
        raise ProfileValidationError("Profile bundle must contain a 'profile' object")
    ticker = str(_require(profile, "ticker", "profile")).upper()
    profile_date = str(_require(profile, "profile_date", "profile"))
    _require(profile, "legal_name", "profile")
    _require(profile, "source_url", "profile")
    _require(profile, "source_date", "profile")
    _require(profile, "method", "profile")
    profile["confidence"] = _confidence(profile.get("confidence", 1.0), "profile")
    profile["ticker"] = ticker

    activities = payload.get("activities", [])
    if not isinstance(activities, list) or not activities:
        raise ProfileValidationError("Profile bundle must contain at least one activity")

    seen_activity_keys: set[str] = set()
    weighted_total = 0.0
    weighted_count = 0
    for i, activity in enumerate(activities):
        ctx = f"activities[{i}]"
        if not isinstance(activity, dict):
            raise ProfileValidationError(f"{ctx}: expected object")
        key = str(_require(activity, "key", ctx))
        if key in seen_activity_keys:
            raise ProfileValidationError(f"{ctx}: duplicate activity key {key!r}")
        seen_activity_keys.add(key)
        _require(activity, "name", ctx)
        _require(activity, "role", ctx)
        _require(activity, "method", ctx)
        _require(activity, "source_url", ctx)
        _require(activity, "source_date", ctx)
        _require(activity, "evidence", ctx)
        activity["confidence"] = _confidence(activity.get("confidence", 1.0), ctx)
        activity["weight"] = _optional_share(activity.get("weight"), ctx)
        if activity["weight"] is not None:
            weighted_total += activity["weight"]
            weighted_count += 1

        classifications = activity.get("classifications", [])
        if not isinstance(classifications, list) or not classifications:
            raise ProfileValidationError(f"{ctx}: at least one official classification mapping is required")
        for j, classification in enumerate(classifications):
            cctx = f"{ctx}.classifications[{j}]"
            scheme = str(_require(classification, "scheme", cctx)).upper()
            version = str(_require(classification, "version", cctx))
            code = str(_require(classification, "code", cctx))
            _require(classification, "assignment_status", cctx)
            _require(classification, "method", cctx)
            _require(classification, "source_url", cctx)
            _require(classification, "source_date", cctx)
            _require(classification, "evidence", cctx)
            classification["confidence"] = _confidence(classification.get("confidence", 1.0), cctx)
            if not db.classification_code_exists(scheme, version, code):
                raise ProfileValidationError(
                    f"{cctx}: unknown {scheme} {version} code {code!r}; "
                    "only codes loaded from official taxonomies are accepted"
                )
            classification["scheme"] = scheme

    # Revenue-share activities need not cover every ancillary activity, but a
    # declared full segment mix should reconcile within rounding tolerance.
    if weighted_count == len(activities) and not 0.97 <= weighted_total <= 1.03:
        raise ProfileValidationError(
            f"Activity weights sum to {weighted_total:.4f}; expected approximately 1.0 for a fully weighted profile"
        )

    segments = payload.get("segments", [])
    if segments is not None:
        if not isinstance(segments, list):
            raise ProfileValidationError("segments must be a list")
        for i, segment in enumerate(segments):
            ctx = f"segments[{i}]"
            _require(segment, "key", ctx)
            _require(segment, "name", ctx)
            _require(segment, "period_end", ctx)
            _require(segment, "source_url", ctx)
            _require(segment, "source_date", ctx)
            _require(segment, "evidence", ctx)
            segment["revenue_share"] = _optional_share(segment.get("revenue_share"), ctx)
            if segment.get("revenue_eur") is not None:
                try:
                    segment["revenue_eur"] = float(segment["revenue_eur"])
                except (TypeError, ValueError) as exc:
                    raise ProfileValidationError(f"{ctx}: revenue_eur must be numeric") from exc

    for collection_name in ("products", "geographies", "capacities", "subsidiaries"):
        rows = payload.get(collection_name, [])
        if rows is not None and not isinstance(rows, list):
            raise ProfileValidationError(f"{collection_name} must be a list")

    payload["profile"] = profile
    return payload


def import_profile_bundle(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    validated = validate_profile_bundle(db, payload)
    db.save_company_profile_bundle(validated)
    return validated


def validate_profile_file(db: Database, path: Path) -> dict[str, Any]:
    return validate_profile_bundle(db, _load_json(Path(path)))


def import_profile_file(db: Database, path: Path) -> dict[str, Any]:
    return import_profile_bundle(db, _load_json(Path(path)))


def seed_bundled_profile(db: Database, ticker: str) -> dict[str, Any]:
    return import_profile_bundle(db, load_bundled_profile(ticker))


def _activity_vector(db: Database, ticker: str) -> dict[str, float]:
    rows = db.company_activities(ticker)
    weighted: dict[str, float] = {}
    unweighted_codes: list[str] = []
    for row in rows:
        mappings = db.activity_classifications(ticker, row["profile_date"], row["activity_key"], scheme="NACE")
        if not mappings:
            continue
        code = mappings[0]["code"]
        weight = row["weight"]
        if weight is None:
            unweighted_codes.append(code)
        else:
            weighted[code] = weighted.get(code, 0.0) + float(weight)
    # If reported segment weights exist, keep the peer vector tied to that
    # economic mix. Unweighted vertically-integrated/ancillary activities are
    # valuable profile evidence but must not dilute the segment weights.
    if unweighted_codes and not weighted:
        fallback = 1.0 / len(unweighted_codes)
        for code in unweighted_codes:
            weighted[code] = weighted.get(code, 0.0) + fallback
    total = sum(weighted.values())
    if total > 0:
        weighted = {k: v / total for k, v in weighted.items()}
    return weighted


def _nace_division(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits[:2]


def _nace_group(code: str) -> str:
    code = str(code)
    if "." in code:
        head, tail = code.split(".", 1)
        return f"{head}.{tail[:1]}" if tail else head
    return _nace_division(code)


def _code_similarity(a: str, b: str) -> tuple[float, str]:
    if a == b:
        return 1.0, "exact"
    if _nace_group(a) == _nace_group(b) and len(_nace_group(a)) > 2:
        return 0.60, "group"
    if _nace_division(a) and _nace_division(a) == _nace_division(b):
        return 0.35, "division"
    return 0.0, "none"


def _activity_match(a: dict[str, float], b: dict[str, float]) -> _ActivityMatch:
    """Match activity mass once, strongest official-taxonomy relation first.

    Exact NACE classes are strong evidence. Same NACE group is weaker. Sharing
    only a two-digit division is deliberately weak: it can keep a candidate on
    the research radar, but can never by itself make an unrelated company a
    strong business peer.
    """
    a_left = {c: float(w) for c, w in a.items()}
    b_left = {c: float(w) for c, w in b.items()}
    pairs: list[tuple[float, str, str, str]] = []
    for ac in a_left:
        for bc in b_left:
            factor, kind = _code_similarity(ac, bc)
            if factor > 0:
                pairs.append((factor, ac, bc, kind))
    pairs.sort(key=lambda x: (x[0], min(a_left[x[1]], b_left[x[2]])), reverse=True)

    score = 0.0
    mass = {"exact": 0.0, "group": 0.0, "division": 0.0}
    exact: list[str] = []
    groups: list[str] = []
    divisions: list[str] = []
    for factor, ac, bc, kind in pairs:
        matched = min(a_left.get(ac, 0.0), b_left.get(bc, 0.0))
        if matched <= 1e-12:
            continue
        score += matched * factor
        mass[kind] += matched
        a_left[ac] -= matched
        b_left[bc] -= matched
        if kind == "exact":
            exact.append(ac)
        elif kind == "group":
            groups.append(f"{ac}~{bc}")
        elif kind == "division":
            divisions.append(f"{ac}~{bc}")
    return _ActivityMatch(
        score=min(1.0, score),
        exact_mass=min(1.0, mass["exact"]),
        group_mass=min(1.0, mass["group"]),
        division_mass=min(1.0, mass["division"]),
        exact_overlap=sorted(set(exact)),
        group_overlap=sorted(set(groups)),
        division_overlap=sorted(set(divisions)),
    )


def _activity_similarity(a: dict[str, float], b: dict[str, float]) -> tuple[float, list[str], list[str]]:
    """Backwards-compatible compact activity similarity helper."""
    m = _activity_match(a, b)
    broader = [f"{x}(group)" for x in m.group_overlap] + [f"{x}(division)" for x in m.division_overlap]
    return m.score, m.exact_overlap, broader


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return float(a) / float(b)


def _metric(db: Database, ticker: str, name: str) -> float | None:
    return db.latest_preferred_metric_value(ticker, name)


def _size_similarity(db: Database, ticker_a: str, ticker_b: str) -> float | None:
    a = _metric(db, ticker_a, "sales_revenue_ytd")
    b = _metric(db, ticker_b, "sales_revenue_ytd")
    return _log_size_similarity(a, b)


def _log_size_similarity(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    ratio = max(a, b) / min(a, b)
    # Same size => 1.0; 10x difference => 0.5; 100x => 0.
    return max(0.0, 1.0 - math.log10(ratio) / 2.0)


def _market_cap_similarity(db: Database, ticker_a: str, ticker_b: str) -> float | None:
    a = db.latest_market_snapshot(ticker_a)
    b = db.latest_market_snapshot(ticker_b)
    av = None if a is None else a["market_cap_eur"]
    bv = None if b is None else b["market_cap_eur"]
    return _log_size_similarity(av, bv)


def _clip(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def _financial_fingerprint(db: Database, ticker: str) -> dict[str, float]:
    """Build a unitless operating/balance-sheet fingerprint from latest facts.

    These are descriptive business-model features, not valuation metrics. They
    intentionally use ratios so companies of different absolute size can be
    compared. Missing inputs remain missing rather than being imputed.
    """
    sales = _metric(db, ticker, "sales_revenue_ytd")
    ebit = _metric(db, ticker, "ebit_ytd")
    ebitda = _metric(db, ticker, "ebitda_simple_ytd")
    fcf = _metric(db, ticker, "free_cash_flow_ytd")
    cfo = _metric(db, ticker, "operating_cash_flow_ytd")
    capex = _metric(db, ticker, "capex_ytd")

    raw: dict[str, float | None] = {
        "ebit_margin": _safe_ratio(ebit, sales),
        "ebitda_margin": _safe_ratio(ebitda, sales),
        "fcf_margin": _safe_ratio(fcf, sales),
        "capex_intensity": _safe_ratio(capex, sales),
        "cfo_to_ebitda": _safe_ratio(cfo, ebitda),
        "current_ratio": _metric(db, ticker, "current_ratio"),
        "debt_to_equity": _metric(db, ticker, "debt_to_equity_parent"),
        "net_debt_to_ebitda": _metric(db, ticker, "net_debt_to_ebitda_run_rate"),
        "interest_coverage": _metric(db, ticker, "interest_coverage_ebit"),
    }
    bounds = {
        "ebit_margin": (-1.0, 1.0),
        "ebitda_margin": (-1.0, 1.5),
        "fcf_margin": (-1.5, 1.5),
        "capex_intensity": (0.0, 1.5),
        "cfo_to_ebitda": (-3.0, 5.0),
        "current_ratio": (0.0, 8.0),
        "debt_to_equity": (-3.0, 10.0),
        "net_debt_to_ebitda": (-10.0, 15.0),
        "interest_coverage": (-10.0, 30.0),
    }
    return {
        k: _clip(float(v), *bounds[k])
        for k, v in raw.items()
        if v is not None and math.isfinite(float(v))
    }


# A difference equal to ``scale`` yields 50% similarity for that feature.
_MODEL_FEATURE_SPECS: dict[str, tuple[float, float]] = {
    "ebit_margin": (0.10, 1.00),
    "ebitda_margin": (0.12, 1.00),
    "fcf_margin": (0.12, 0.90),
    "capex_intensity": (0.08, 0.70),
    "cfo_to_ebitda": (0.60, 0.70),
    "current_ratio": (1.00, 0.55),
    "debt_to_equity": (1.00, 0.80),
    "net_debt_to_ebitda": (2.00, 1.00),
    "interest_coverage": (5.00, 0.55),
}


def _business_model_similarity(db: Database, ticker_a: str, ticker_b: str) -> tuple[float | None, int, list[str]]:
    a = _financial_fingerprint(db, ticker_a)
    b = _financial_fingerprint(db, ticker_b)
    common = [k for k in _MODEL_FEATURE_SPECS if k in a and k in b]
    if len(common) < 4:
        return None, len(common), []
    weighted = 0.0
    weight_total = 0.0
    rows: list[tuple[float, str, float, float]] = []
    for key in common:
        scale, weight = _MODEL_FEATURE_SPECS[key]
        sim = 1.0 / (1.0 + abs(a[key] - b[key]) / scale)
        weighted += sim * weight
        weight_total += weight
        rows.append((sim, key, a[key], b[key]))
    score = 100.0 * weighted / weight_total if weight_total else None
    rows.sort(reverse=True)
    best = rows[:2]
    worst = sorted(rows)[:2]
    notes = [f"closest {row[1]} ({row[0]*100:.0f}%)" for row in best]
    best_keys = {row[1] for row in best}
    notes += [f"largest gap {row[1]} ({row[0]*100:.0f}%)" for row in worst if row[1] not in best_keys]
    return (None if score is None else round(score, 2)), len(common), notes[:4]


def _investment_similarity(db: Database, ticker_a: str, ticker_b: str) -> tuple[float | None, int, list[str], float | None, float | None]:
    market = _market_cap_similarity(db, ticker_a, ticker_b)
    sales = _size_similarity(db, ticker_a, ticker_b)
    lev_a = _metric(db, ticker_a, "debt_to_equity_parent")
    lev_b = _metric(db, ticker_b, "debt_to_equity_parent")
    leverage = None
    if lev_a is not None and lev_b is not None:
        leverage = 1.0 / (1.0 + abs(_clip(lev_a, -3, 10) - _clip(lev_b, -3, 10)) / 1.0)

    components = [
        ("market-cap size", market, 0.55),
        ("sales size", sales, 0.30),
        ("leverage", leverage, 0.15),
    ]
    available = [(name, value, weight) for name, value, weight in components if value is not None]
    if not available:
        return None, 0, [], market, sales
    total_weight = sum(w for _, _, w in available)
    score = 100.0 * sum(float(v) * w for _, v, w in available) / total_weight
    notes = [f"{name} {float(value)*100:.0f}%" for name, value, _ in available]
    return round(score, 2), len(available), notes, market, sales


def _business_gate(match: _ActivityMatch) -> tuple[bool, str, str]:
    # Broad-division-only overlap can be a weak research lead but never a
    # moderate/strong whole-company business peer.
    only_division = match.exact_mass <= 1e-12 and match.group_mass <= 1e-12 and match.division_mass > 0
    if not only_division and (match.exact_mass >= 0.35 or match.score >= 0.55):
        return True, "STRONG", "substantial detailed activity overlap"
    if not only_division and (match.exact_mass >= 0.20 or match.score >= 0.30):
        return True, "MODERATE", "meaningful detailed activity overlap"
    if match.exact_mass >= 0.08 or match.group_mass >= 0.10 or match.score >= 0.12:
        return True, "WEAK", "limited or broad activity overlap"
    return False, "REJECTED", "no material business-activity overlap"


def _similarity_status(score: float | None) -> str:
    if score is None:
        return "INSUFFICIENT DATA"
    if score >= 75:
        return "STRONG"
    if score >= 60:
        return "MODERATE"
    if score >= 45:
        return "WEAK"
    return "LOW"


def _canonical_peer_type(peer_type: str) -> str:
    value = str(peer_type or "business").strip().lower().replace("_", "-")
    aliases = {
        "business": "business",
        "product": "business",
        "product-business": "business",
        "model": "business-model",
        "business-model": "business-model",
        "investment": "investment",
    }
    if value not in aliases:
        raise ProfileValidationError(
            f"Unknown peer type {peer_type!r}. Use business, product, business-model/model, or investment."
        )
    return aliases[value]


def build_peer_candidates(
    db: Database,
    ticker: str,
    *,
    limit: int = 10,
    peer_type: str = "business",
    include_rejected: bool = True,
) -> list[PeerCandidate]:
    """Build deterministic peer candidates with explicitly separated meanings.

    ``business`` / ``product`` requires a hard activity gate. Financial or size
    similarity can refine ranking but can never rescue an activity-unrelated
    company. ``business-model`` deliberately allows cross-industry economic
    analogues. ``investment`` compares market/size/leverage characteristics and
    must never be interpreted as product or valuation-peer evidence.
    """
    ticker = ticker.upper()
    kind = _canonical_peer_type(peer_type)
    source = _activity_vector(db, ticker)
    if not source:
        raise ProfileValidationError(f"No NACE-mapped company profile found for {ticker}")

    out: list[PeerCandidate] = []
    for other in db.profiled_tickers():
        if other == ticker:
            continue
        target = _activity_vector(db, other)
        if not target:
            continue
        match = _activity_match(source, target)
        size_similarity = _size_similarity(db, ticker, other)
        model_score, model_features, model_notes = _business_model_similarity(db, ticker, other)
        investment_score, investment_features, investment_notes, market_cap_similarity, _investment_sales = _investment_similarity(db, ticker, other)

        eligible = True
        score: float | None
        status: str
        notes: list[str]
        if kind == "business":
            eligible, gate_status, gate_reason = _business_gate(match)
            status = gate_status
            if eligible:
                components: list[tuple[float, float]] = [(match.score * 100.0, 0.80)]
                if model_score is not None:
                    components.append((model_score, 0.10))
                if size_similarity is not None:
                    components.append((size_similarity * 100.0, 0.10))
                weight = sum(w for _, w in components)
                score = sum(v * w for v, w in components) / weight
            else:
                score = None
            notes = model_notes[:2]
            explanation = (
                f"{gate_reason}; activity overlap {match.score:.2f} "
                f"(exact mass {match.exact_mass:.2f}, group {match.group_mass:.2f}, division {match.division_mass:.2f}); "
                f"size {'unavailable' if size_similarity is None else f'{size_similarity:.2f}'}; "
                f"business-model {'unavailable' if model_score is None else f'{model_score/100:.2f}'}"
            )
            features_compared = model_features
        elif kind == "business-model":
            score = model_score
            status = _similarity_status(score)
            eligible = score is not None
            notes = model_notes
            explanation = (
                f"financial operating/balance-sheet fingerprint across {model_features} comparable features; "
                f"activity overlap {match.score:.2f} is context only and is not an eligibility gate"
            )
            features_compared = model_features
        else:
            score = investment_score
            status = _similarity_status(score)
            eligible = score is not None
            notes = investment_notes
            explanation = (
                f"investment-characteristic similarity across {investment_features} available components; "
                "this is not evidence of product/business comparability"
            )
            features_compared = investment_features

        if not include_rejected and not eligible:
            continue
        out.append(
            PeerCandidate(
                ticker=other,
                peer_type=kind,
                status=status,
                eligible=eligible,
                score=None if score is None else round(score, 2),
                activity_score=round(match.score * 100.0, 2),
                business_model_score=model_score,
                investment_score=investment_score,
                size_score=None if size_similarity is None else round(size_similarity * 100.0, 2),
                market_cap_score=None if market_cap_similarity is None else round(market_cap_similarity * 100.0, 2),
                exact_overlap=match.exact_overlap,
                # Backwards compatibility: v0.3.1 exposed broader division
                # matches through group_overlap. Keep them there while also
                # exposing division_overlap explicitly in v0.3.2.
                group_overlap=match.group_overlap + [f"{x}(division)" for x in match.division_overlap],
                division_overlap=match.division_overlap,
                features_compared=features_compared,
                feature_notes=notes,
                explanation=explanation,
            )
        )

    # Eligible candidates always rank above rejected/insufficient candidates.
    out.sort(
        key=lambda x: (
            1 if x.eligible else 0,
            -1.0 if x.score is None else x.score,
            x.activity_score,
            x.ticker,
        ),
        reverse=True,
    )
    return out[: max(1, int(limit))]

def seed_all_bundled_profiles(db: Database) -> list[dict[str, Any]]:
    out = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        out.append(seed_bundled_profile(db, path.stem.upper()))
    return out


def profile_history(db: Database, ticker: str) -> list[dict[str, Any]]:
    return [dict(r) for r in db.company_profile_history(ticker)]


def _latest_financial_period_end(db: Database, ticker: str) -> str | None:
    for row in db.report_inventory(ticker, preferred_only=True):
        if row["period_end"]:
            return str(row["period_end"])
    return None


def profile_quality(db: Database, ticker: str, as_of: str | None = None) -> dict[str, Any]:
    data = profile_as_dict(db, ticker, as_of=as_of)
    p = data["profile"]
    activities = data["activities"]
    material = [a for a in activities if a["weight"] is not None or "material" in str(a["role"])] or activities
    classified = sum(1 for a in material if a.get("classifications"))
    class_cov = classified / len(material) if material else 0.0
    weighted_share = min(1.0, sum(float(a["weight"] or 0.0) for a in activities))
    segment_share = min(1.0, sum(float(r["revenue_share"] or 0.0) for r in data["segments"]))
    geography_share = min(1.0, sum(float(r["revenue_share"] or 0.0) for r in data["geographies"]))
    official_count = sum(
        1 for a in activities for c in a.get("classifications", [])
        if c.get("assignment_status") == "official-registry"
    )

    latest_fin = _latest_financial_period_end(db, ticker)
    lag_days = None
    freshness = "UNKNOWN"
    freshness_points = 0.0
    if latest_fin:
        lag_days = (date.fromisoformat(latest_fin) - date.fromisoformat(str(p["profile_date"]))).days
        lag_days = max(0, lag_days)
        if lag_days <= 270:
            freshness, freshness_points = "CURRENT", 10.0
        elif lag_days <= 450:
            freshness, freshness_points = "AGING", 5.0
        else:
            freshness, freshness_points = "STALE", 0.0

    score = (
        20.0 * class_cov
        + 20.0 * weighted_share
        + 15.0 * segment_share
        + (10.0 if data["products"] else 0.0)
        + 10.0 * geography_share
        + (5.0 if data["capacities"] else 0.0)
        + (5.0 if data["subsidiaries"] else 0.0)
        + (5.0 if official_count else 0.0)
        + freshness_points
    )
    gaps: list[str] = []
    if class_cov < 0.999: gaps.append("some material activities lack an official-taxonomy mapping")
    if weighted_share < 0.95: gaps.append("material activity weights do not cover most of the business")
    if segment_share < 0.95: gaps.append("reported segment shares are incomplete")
    if not data["products"]: gaps.append("products/services not yet stored")
    if geography_share < 0.50: gaps.append("geographic exposure is incomplete")
    if not data["capacities"]: gaps.append("no operating capacities/KPIs stored")
    if not data["subsidiaries"]: gaps.append("material subsidiaries not yet stored")
    if not official_count: gaps.append("official registry activity assignment not yet verified")
    if freshness == "STALE": gaps.append("business profile is stale relative to latest financial statements")
    elif freshness == "AGING": gaps.append("business profile is aging relative to latest financial statements")

    return {
        "ticker": str(p["ticker"]), "profile_date": str(p["profile_date"]),
        "latest_financial_period_end": latest_fin, "profile_lag_days": lag_days,
        "freshness": freshness, "score": round(score, 1),
        "activity_classification_coverage": round(class_cov, 4),
        "weighted_activity_coverage": round(weighted_share, 4),
        "segment_share_coverage": round(segment_share, 4),
        "geography_share_coverage": round(geography_share, 4),
        "products_count": len(data["products"]), "capacities_count": len(data["capacities"]),
        "subsidiaries_count": len(data["subsidiaries"]),
        "official_registry_assignments": official_count, "gaps": gaps,
    }


def profile_as_dict(db: Database, ticker: str, as_of: str | None = None) -> dict[str, Any]:
    profile = db.company_profile(ticker, as_of=as_of)
    if profile is None:
        raise ProfileValidationError(
            f"No company-intelligence profile for {ticker.upper()}. "
            f"For the first test case run: zse-tool profile-seed --ticker {ticker.upper()}"
        )
    ticker = profile["ticker"]
    profile_date = profile["profile_date"]
    activities = []
    for row in db.company_activities(ticker, profile_date=profile_date):
        item = dict(row)
        item["classifications"] = [dict(x) for x in db.activity_classifications(ticker, profile_date, row["activity_key"])]
        activities.append(item)
    return {
        "profile": dict(profile),
        "activities": activities,
        "segments": [dict(x) for x in db.company_segments(ticker, profile_date=profile_date)],
        "products": [dict(x) for x in db.company_products(ticker, profile_date=profile_date)],
        "geographies": [dict(x) for x in db.company_geographies(ticker, profile_date=profile_date)],
        "capacities": [dict(x) for x in db.company_capacities(ticker, profile_date=profile_date)],
        "subsidiaries": [dict(x) for x in db.company_subsidiaries(ticker, profile_date=profile_date)],
    }
