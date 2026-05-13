from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .utils import contains_any, safe_str, to_number


MARKET_CITY_HINTS = {
    "los angeles": [
        "los angeles", "lynwood", "compton", "long beach", "south gate", "paramount", "carson",
        "baldwin park", "la puente", "buena park", "santa fe springs", "downey", "bellflower",
        "inglewood", "hollywood", "boyle heights", "adelanto", "city of commerce", "el monte", "covina",
        "la mirada", "asusa", "azusa", "harbor city", "whittier", "pico rivera"
    ],
    "san francisco": ["san francisco", "sf", "soma"],
    "sacramento": ["sacramento"],
    "fresno": ["fresno"],
}

MARKET_NORMALIZATIONS = {
    "sf": "san francisco",
    "san fran": "san francisco",
    "sfo": "san francisco",
    "la": "los angeles",
    "l.a.": "los angeles",
    "l.a": "los angeles",
}


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [safe_str(v) for v in value if safe_str(v)]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [safe_str(value)]


def _norm_market(value: str) -> str:
    v = safe_str(value).lower().strip()
    return MARKET_NORMALIZATIONS.get(v, v)


def _field_text(row: pd.Series, fields: list[str]) -> str:
    return " ".join(safe_str(row.get(f)).lower() for f in fields)


def _market_match(row: pd.Series, requirements: dict[str, Any]) -> tuple[bool, str]:
    required_cities = [_norm_market(c) for c in _listify(requirements.get("cities"))]
    required_markets = [_norm_market(m) for m in _listify(requirements.get("markets"))]
    if not required_cities and not required_markets:
        return True, "No market/city filter requested."

    # Use structured location fields only. Do not use free-text comments/description for hard market filtering,
    # because that caused false positives where an SF brief could still return LA boards.
    structured_text = _field_text(row, ["city", "market", "state"])

    if required_cities:
        for c in required_cities:
            if c in structured_text:
                return True, f"City match: {c}."
        return False, f"City did not match requested cities: {', '.join(required_cities)}."

    for req in required_markets:
        if req in structured_text:
            return True, f"Market match: {req}."
        for market_name, city_hints in MARKET_CITY_HINTS.items():
            if req == market_name or req in market_name or market_name in req:
                if any(h in structured_text for h in city_hints):
                    return True, f"Market hint match: {req}."

    return False, f"Market did not match requested markets: {', '.join(required_markets)}."


def _media_type_match(row: pd.Series, requirements: dict[str, Any]) -> tuple[bool, str]:
    requested = [m.lower() for m in _listify(requirements.get("media_types"))]
    if not requested:
        return True, "No media type filter requested."
    row_media = safe_str(row.get("media_type")).lower()
    row_static_or_digital = safe_str(row.get("digital_or_static")).lower()
    for media in requested:
        if "digital" in media and ("digital" in row_media or row_static_or_digital == "digital"):
            return True, "Digital media type match."
        if any(term in media for term in ["static", "bulletin", "poster", "wallscape"]):
            if "digital" not in media and row_static_or_digital != "digital":
                return True, "Static media type match."
        if media and media in row_media:
            return True, f"Media type match: {media}."
    return False, f"Media type did not match requested media: {', '.join(requested)}."


def _distance_match(row: pd.Series, requirements: dict[str, Any]) -> tuple[bool, str]:
    max_dist = to_number(requirements.get("max_distance_miles"))
    if max_dist is None:
        pois = requirements.get("poi_requirements") or []
        if pois and isinstance(pois[0], dict):
            max_dist = to_number(pois[0].get("max_distance_miles"))
    if max_dist is None:
        return True, "No distance filter requested."
    dist = to_number(row.get("distance_to_poi_miles"))
    if dist is None:
        return False, "Distance requested but distance could not be calculated."
    if dist <= max_dist:
        return True, f"Within requested distance: {dist:.2f} mi <= {max_dist:.2f} mi."
    return False, f"Outside requested distance: {dist:.2f} mi > {max_dist:.2f} mi."


def _status_match(row: pd.Series, requirements: dict[str, Any]) -> tuple[bool, str]:
    include_inactive = bool(requirements.get("include_inactive", False))
    status = safe_str(row.get("status")).lower()
    if include_inactive:
        return True, "Inactive units allowed by requirements."
    if status in {"taken down", "unavailable"}:
        return False, f"Status is {safe_str(row.get('status'))}."
    return True, f"Status is {safe_str(row.get('status')) or 'Active/unknown'}."


def _score_row(row: pd.Series, requirements: dict[str, Any]) -> tuple[float, str, str]:
    flags = []
    reasons = []
    score = 50.0

    impressions = to_number(row.get("geopath_a18_weekly_impressions"))
    if impressions:
        score += min(25, np.log10(max(impressions, 1)) * 4)
        reasons.append(f"{impressions:,.0f} A18+ weekly impressions")
    else:
        flags.append("Missing A18+ weekly impressions")

    cpm = to_number(row.get("cpm"))
    if cpm and cpm > 0:
        score += max(0, 15 - min(cpm, 50) * 0.2)
        reasons.append(f"CPM ${cpm:.2f}")

    dist = to_number(row.get("distance_to_poi_miles"))
    if dist is not None:
        score += max(0, 20 - dist)
        reasons.append(f"{dist:.2f} miles from POI")

    directional_keywords = _listify(requirements.get("directional_keywords"))
    if directional_keywords:
        text = " ".join([
            safe_str(row.get("description")),
            safe_str(row.get("comments")),
            safe_str(row.get("directional_tags")),
        ])
        if contains_any(text, directional_keywords):
            score += 15
            reasons.append("directional keyword match")
        else:
            flags.append("Directional keywords requested but not found in comments/tags")

    pricing_flags = safe_str(row.get("pricing_review_flags"))
    if pricing_flags:
        flags.append(pricing_flags)

    if not reasons:
        reasons.append("Meets hard filters")

    return round(score, 2), "; ".join(reasons), " | ".join(flags)


def match_units(inventory: pd.DataFrame, requirements: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected_rows = []
    excluded_rows = []

    for _, row in inventory.iterrows():
        checks = [
            _status_match(row, requirements),
            _market_match(row, requirements),
            _media_type_match(row, requirements),
            _distance_match(row, requirements),
        ]
        failed = [reason for passed, reason in checks if not passed]
        passed_reasons = [reason for passed, reason in checks if passed]
        row_dict = row.to_dict()

        if failed:
            row_dict["excluded_reason"] = " | ".join(failed)
            excluded_rows.append(row_dict)
            continue

        score, reason, flags = _score_row(row, requirements)
        row_dict["score"] = score
        row_dict["selection_reason"] = reason
        row_dict["review_flags"] = flags
        row_dict["matched_requirements"] = " | ".join(passed_reasons)
        selected_rows.append(row_dict)

    selected = pd.DataFrame(selected_rows)
    excluded = pd.DataFrame(excluded_rows)
    if not selected.empty:
        selected = selected.sort_values(by="score", ascending=False).reset_index(drop=True)
        n = int(requirements.get("number_of_units") or 25)
        selected = selected.head(n)
    return selected, excluded
