from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from distance import add_distance_to_poi, get_primary_poi
from grid_writer import write_output_workbook
from inventory import normalize_inventory
from pricing import add_pricing
from requirements_extractor import (
    coerce_requirements,
    default_requirements,
    extract_requirements,
    extract_text_from_pdf,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR


# -----------------------------
# Proposal intelligence helpers
# -----------------------------

def _clean_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _clean_unit_id(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        if cleaned == "":
            return None
        return float(cleaned)
    except Exception:
        return None


TARGET_HINTS = {
    "sofi": {
        "keywords": ["sofi", "sofi stadium", "inglewood"],
        "primary_units": ["40575", "40576"],
        "primary_cities": ["inglewood"],
        "nearby_cities": [
            "hawthorne",
            "el segundo",
            "westchester",
            "playa vista",
            "los angeles",
        ],
        "markets": ["Los Angeles"],
        "cities": ["Los Angeles", "Inglewood"],
        "poi": {
            "poi_name": "SoFi Stadium",
            "poi_address": "1001 Stadium Dr, Inglewood, CA",
            "latitude": 33.9535,
            "longitude": -118.3392,
            "priority": 1,
        },
        "max_distance_miles": 15,
    },
    "dtla": {
        "keywords": ["dtla", "downtown la", "downtown los angeles", "union station"],
        "primary_units": ["10126", "0103", "103"],
        "primary_cities": ["los angeles"],
        "nearby_cities": ["city of commerce", "east los angeles", "vernon", "boyle heights"],
        "markets": ["Los Angeles"],
        "cities": ["Los Angeles"],
        "poi": {
            "poi_name": "Downtown Los Angeles",
            "poi_address": "Downtown Los Angeles, CA",
            "latitude": 34.0407,
            "longitude": -118.2468,
            "priority": 1,
        },
        "max_distance_miles": 15,
    },
    "san_francisco": {
        "keywords": [
            "san francisco",
            "san francsico",
            "sf",
            "levi's stadium",
            "levis stadium",
            "levi’s stadium",
            "santa clara",
        ],
        "primary_units": [],
        "primary_cities": ["san francisco", "santa clara"],
        "nearby_cities": ["oakland", "san mateo", "south san francisco", "daly city"],
        "markets": ["San Francisco"],
        "cities": ["San Francisco", "Santa Clara"],
        "poi": {
            "poi_name": "Levi's Stadium",
            "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
            "latitude": 37.403,
            "longitude": -121.970,
            "priority": 1,
        },
        "max_distance_miles": 35,
    },
    "sacramento": {
        "keywords": ["sacramento", "sacto"],
        "primary_units": [],
        "primary_cities": ["sacramento"],
        "nearby_cities": ["west sacramento", "elk grove", "roseville"],
        "markets": ["Sacramento"],
        "cities": ["Sacramento"],
        "poi": None,
        "max_distance_miles": None,
    },
    "san_jose": {
        "keywords": ["san jose"],
        "primary_units": [],
        "primary_cities": ["san jose"],
        "nearby_cities": ["santa clara", "sunnyvale", "cupertino"],
        "markets": ["San Jose"],
        "cities": ["San Jose"],
        "poi": None,
        "max_distance_miles": None,
    },
    "santa_cruz": {
        "keywords": ["santa cruz"],
        "primary_units": [],
        "primary_cities": ["santa cruz"],
        "nearby_cities": ["capitola", "watsonville"],
        "markets": ["Santa Cruz"],
        "cities": ["Santa Cruz"],
        "poi": None,
        "max_distance_miles": None,
    },
}


KNOWN_POIS = {
    "sofi stadium": {
        "poi_name": "SoFi Stadium",
        "poi_address": "1001 Stadium Dr, Inglewood, CA",
        "latitude": 33.9535,
        "longitude": -118.3392,
        "priority": 1,
        "market": "Los Angeles",
        "city": "Inglewood",
    },
    "levi's stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
        "market": "San Francisco",
        "city": "Santa Clara",
    },
    "levis stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
        "market": "San Francisco",
        "city": "Santa Clara",
    },
    "levi’s stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
        "market": "San Francisco",
        "city": "Santa Clara",
    },
    "metlife stadium": {
        "poi_name": "MetLife Stadium",
        "poi_address": "1 MetLife Stadium Dr, East Rutherford, NJ",
        "latitude": 40.8135,
        "longitude": -74.0745,
        "priority": 3,
        "market": "New York",
        "city": "East Rutherford",
    },
    "gillette stadium": {
        "poi_name": "Gillette Stadium",
        "poi_address": "1 Patriot Pl, Foxborough, MA",
        "latitude": 42.0909,
        "longitude": -71.2643,
        "priority": 3,
        "market": "Boston",
        "city": "Foxborough",
    },
    "at&t stadium": {
        "poi_name": "AT&T Stadium",
        "poi_address": "1 AT&T Way, Arlington, TX",
        "latitude": 32.7473,
        "longitude": -97.0945,
        "priority": 3,
        "market": "Dallas",
        "city": "Arlington",
    },
    "nrg stadium": {
        "poi_name": "NRG Stadium",
        "poi_address": "NRG Pkwy, Houston, TX",
        "latitude": 29.6847,
        "longitude": -95.4107,
        "priority": 3,
        "market": "Houston",
        "city": "Houston",
    },
    "arrowhead stadium": {
        "poi_name": "Arrowhead Stadium",
        "poi_address": "1 Arrowhead Dr, Kansas City, MO",
        "latitude": 39.0490,
        "longitude": -94.4839,
        "priority": 3,
        "market": "Kansas City",
        "city": "Kansas City",
    },
    "mercedes-benz stadium": {
        "poi_name": "Mercedes-Benz Stadium",
        "poi_address": "1 AMB Dr NW, Atlanta, GA",
        "latitude": 33.7554,
        "longitude": -84.4008,
        "priority": 3,
        "market": "Atlanta",
        "city": "Atlanta",
    },
    "lincoln financial field": {
        "poi_name": "Lincoln Financial Field",
        "poi_address": "One Lincoln Financial Field Way, Philadelphia, PA",
        "latitude": 39.9008,
        "longitude": -75.1675,
        "priority": 3,
        "market": "Philadelphia",
        "city": "Philadelphia",
    },
}


def detect_target_profiles(brief_text: str) -> list[str]:
    brief_lower = _clean_text(brief_text)
    matches = []

    for profile_name, profile in TARGET_HINTS.items():
        if any(keyword in brief_lower for keyword in profile["keywords"]):
            matches.append(profile_name)

    return matches


def apply_target_profiles(raw_requirements: dict[str, Any], brief_text: str) -> dict[str, Any]:
    """
    Adds target location intelligence without hard excluding all alternates.
    This helps the app understand stadiums, cities, freeways, target markets, and POIs.
    """
    requirements = dict(raw_requirements or {})
    brief_lower = _clean_text(brief_text)

    markets = list(requirements.get("markets") or [])
    cities = list(requirements.get("cities") or [])
    raw_pois = list(requirements.get("poi_requirements") or [])
    cleaned_pois = []
    known_unit_ids = list(requirements.get("known_unit_ids") or [])

    for item in raw_pois:
        if isinstance(item, dict):
            cleaned_pois.append(item)

            market = item.get("market")
            city = item.get("city")
            if market and market not in markets:
                markets.append(market)
            if city and city not in cities:
                cities.append(city)

        elif isinstance(item, str):
            key = _clean_text(item)
            if key in KNOWN_POIS:
                poi = KNOWN_POIS[key]
                cleaned_pois.append(poi)

                market = poi.get("market")
                city = poi.get("city")
                if market and market not in markets:
                    markets.append(market)
                if city and city not in cities:
                    cities.append(city)
            else:
                cleaned_pois.append(
                    {
                        "poi_name": item,
                        "poi_address": item,
                        "latitude": None,
                        "longitude": None,
                    }
                )

    matched_profiles = detect_target_profiles(brief_text)

    for profile_name in matched_profiles:
        profile = TARGET_HINTS[profile_name]

        for market in profile.get("markets") or []:
            if market not in markets:
                markets.append(market)

        for city in profile.get("cities") or []:
            if city not in cities:
                cities.append(city)

        for unit_id in profile.get("primary_units") or []:
            if unit_id not in known_unit_ids:
                known_unit_ids.append(unit_id)

        poi = profile.get("poi")
        if poi:
            existing_names = [
                _clean_text(p.get("poi_name"))
                for p in cleaned_pois
                if isinstance(p, dict)
            ]
            if _clean_text(poi.get("poi_name")) not in existing_names:
                cleaned_pois.append(poi)

        if profile.get("max_distance_miles") and not requirements.get("max_distance_miles"):
            requirements["max_distance_miles"] = profile.get("max_distance_miles")

    for key, poi in KNOWN_POIS.items():
        if key in brief_lower:
            existing_names = [
                _clean_text(p.get("poi_name"))
                for p in cleaned_pois
                if isinstance(p, dict)
            ]
            if _clean_text(poi.get("poi_name")) not in existing_names:
                cleaned_pois.append(poi)

            market = poi.get("market")
            city = poi.get("city")
            if market and market not in markets:
                markets.append(market)
            if city and city not in cities:
                cities.append(city)

    if "los angeles" in brief_lower or "sofi" in brief_lower or "dtla" in brief_lower:
        if "Los Angeles" not in markets:
            markets.append("Los Angeles")

    if "sofi" in brief_lower or "inglewood" in brief_lower:
        for city in ["Los Angeles", "Inglewood"]:
            if city not in cities:
                cities.append(city)

    if "san francisco" in brief_lower or "san francsico" in brief_lower or "sf" in brief_lower:
        if "San Francisco" not in markets:
            markets.append("San Francisco")
        if "San Francisco" not in cities:
            cities.append("San Francisco")

    if "sacramento" in brief_lower or "sacto" in brief_lower:
        if "Sacramento" not in markets:
            markets.append("Sacramento")
        if "Sacramento" not in cities:
            cities.append("Sacramento")

    if "san jose" in brief_lower:
        if "San Jose" not in markets:
            markets.append("San Jose")
        if "San Jose" not in cities:
            cities.append("San Jose")

    if "santa cruz" in brief_lower:
        if "Santa Cruz" not in markets:
            markets.append("Santa Cruz")
        if "Santa Cruz" not in cities:
            cities.append("Santa Cruz")

    if "atlanta" in brief_lower:
        if "Atlanta" not in markets:
            markets.append("Atlanta")
        if "Atlanta" not in cities:
            cities.append("Atlanta")

    if "boston" in brief_lower:
        if "Boston" not in markets:
            markets.append("Boston")

    if "dallas" in brief_lower:
        if "Dallas" not in markets:
            markets.append("Dallas")

    if "houston" in brief_lower:
        if "Houston" not in markets:
            markets.append("Houston")
        if "Houston" not in cities:
            cities.append("Houston")

    if "kansas city" in brief_lower:
        if "Kansas City" not in markets:
            markets.append("Kansas City")
        if "Kansas City" not in cities:
            cities.append("Kansas City")

    if "philadelphia" in brief_lower:
        if "Philadelphia" not in markets:
            markets.append("Philadelphia")
        if "Philadelphia" not in cities:
            cities.append("Philadelphia")

    if "new york" in brief_lower or "ny/nj" in brief_lower or "new jersey" in brief_lower:
        if "New York" not in markets:
            markets.append("New York")
        if "New Jersey" not in markets:
            markets.append("New Jersey")

    if "digital bulletins" in brief_lower or "digital bulletin" in brief_lower:
        requirements["media_types"] = ["Digital Bulletin"]
    elif "bulletins" in brief_lower or "bulletin" in brief_lower:
        media_types = set(requirements.get("media_types") or [])
        media_types.update(["Static Bulletin", "Digital Bulletin", "Bulletin"])
        requirements["media_types"] = list(media_types)

    if (
        "less than $15,000" in brief_lower
        or "less than 15000" in brief_lower
        or "less than $15k" in brief_lower
        or "less than 15k" in brief_lower
        or "higher than $15k" in brief_lower
        or "higher than 15k" in brief_lower
        or "$15k" in brief_lower
        or "$15,000" in brief_lower
        or "15000" in brief_lower
    ):
        requirements["max_unit_rate"] = 15000

    if not requirements.get("max_distance_miles"):
        if "5 mile" in brief_lower or "5-mile" in brief_lower or "within 5" in brief_lower:
            requirements["max_distance_miles"] = 5
        elif "10 mile" in brief_lower or "10-mile" in brief_lower or "within 10" in brief_lower:
            requirements["max_distance_miles"] = 10
        elif "15 mile" in brief_lower or "15-mile" in brief_lower or "within 15" in brief_lower:
            requirements["max_distance_miles"] = 15
        elif "20 mile" in brief_lower or "20-mile" in brief_lower or "within 20" in brief_lower:
            requirements["max_distance_miles"] = 20
        elif "stadium" in brief_lower:
            requirements["max_distance_miles"] = 35

    if "world cup" in brief_lower or " wc" in brief_lower:
        if not requirements.get("number_of_units"):
            requirements["number_of_units"] = 25

    requirements["markets"] = markets
    requirements["cities"] = cities
    requirements["poi_requirements"] = cleaned_pois
    requirements["known_unit_ids"] = known_unit_ids
    requirements["matched_target_profiles"] = matched_profiles

    return requirements


def score_proposal_candidates(
    inventory: pd.DataFrame,
    requirements: dict[str, Any],
    brief_text: str,
) -> pd.DataFrame:
    """
    Planner-style scoring for RFP recommendations.

    Main behavior:
    - Exact matches score highest.
    - Budget and radius are ranking signals, not automatic blockers.
    - Strategic alternates are allowed when they support the same requested market.
    - Wrong markets are heavily penalized.
    - The output should not return zero if there is usable inventory in the right geography.
    """
    if inventory is None or inventory.empty:
        return inventory

    df = inventory.copy()
    brief_lower = _clean_text(brief_text)

    target_profiles = requirements.get("matched_target_profiles") or detect_target_profiles(brief_text)

    requested_media_types = [_clean_text(x) for x in (requirements.get("media_types") or [])]
    requested_markets = [_clean_text(x) for x in (requirements.get("markets") or [])]
    requested_cities = [_clean_text(x) for x in (requirements.get("cities") or [])]
    requested_unit_ids = {_clean_unit_id(x) for x in (requirements.get("known_unit_ids") or [])}

    max_unit_rate = requirements.get("max_unit_rate")
    requested_radius = requirements.get("max_distance_miles")
    requested_count = int(requirements.get("number_of_units") or 25)

    freeway_terms = [
        "freeway",
        "interstate",
        "highway",
        "i-5",
        "5 freeway",
        "the 5",
        "i5",
        "interstate 5",
        "101",
        "105",
        "110",
        "405",
        "605",
        "10 freeway",
        "710",
        "91 freeway",
    ]

    has_freeway_intent = any(term in brief_lower for term in freeway_terms)

    has_poi_or_distance_intent = bool(
        requirements.get("poi_requirements")
        or requested_radius
        or any(term in brief_lower for term in ["stadium", "store list", "radius", "miles", "nearby", "near"])
    )

    if "unit_id" in df.columns:
        df["unit_id_clean"] = df["unit_id"].apply(_clean_unit_id)
    else:
        df["unit_id_clean"] = ""

    def text_has_any(text: str, terms: list[str]) -> bool:
        return any(_clean_text(term) and _clean_text(term) in text for term in terms)

    def get_rate_value(row: pd.Series) -> float | None:
        for col in ["four_week_media_cost", "negotiated_rate_4wk", "contracted_media_cost", "rate_card"]:
            if col in row.index:
                value = _to_float(row.get(col))
                if value is not None:
                    return value
        return None

    def score_row(row: pd.Series) -> pd.Series:
        score = 0
        reasons = []
        flags = []
        tier = "Planner Review"

        unit_id = _clean_unit_id(row.get("unit_id"))
        city = _clean_text(row.get("city"))
        market = _clean_text(row.get("market"))
        media_type = _clean_text(row.get("media_type"))
        description = _clean_text(row.get("description"))
        comments = _clean_text(row.get("comments"))
        location = _clean_text(row.get("location"))
        freeway = _clean_text(row.get("freeway_street", row.get("freeway", "")))

        combined_text = " ".join(
            [
                unit_id,
                city,
                market,
                media_type,
                description,
                comments,
                location,
                freeway,
            ]
        )

        market_match = False
        city_match = False

        if requested_markets:
            market_match = any(m in market or m in combined_text for m in requested_markets)

        if requested_cities:
            city_match = any(c in city or c in combined_text for c in requested_cities)

        if requested_markets or requested_cities:
            if market_match:
                score += 45
                reasons.append("Matches requested market.")
            elif city_match:
                score += 45
                reasons.append("Matches requested city or target area.")
            else:
                score -= 120
                flags.append("Outside requested market or geography.")

        if requested_unit_ids and unit_id in requested_unit_ids:
            score += 80
            reasons.append("Specific requested unit.")

        if target_profiles:
            best_profile_points = 0

            for profile_name in target_profiles:
                profile = TARGET_HINTS.get(profile_name, {})
                primary_units = {_clean_unit_id(x) for x in profile.get("primary_units", [])}
                primary_cities = profile.get("primary_cities", [])
                nearby_cities = profile.get("nearby_cities", [])
                poi_name = profile.get("poi", {}).get("poi_name") if profile.get("poi") else profile_name

                if unit_id in primary_units:
                    best_profile_points = max(best_profile_points, 85)
                    reasons.append(f"Known strong fit for {poi_name}.")
                elif text_has_any(city, primary_cities) or text_has_any(combined_text, primary_cities):
                    best_profile_points = max(best_profile_points, 50)
                    reasons.append(f"Located in primary target area for {poi_name}.")
                elif text_has_any(city, nearby_cities) or text_has_any(combined_text, nearby_cities):
                    best_profile_points = max(best_profile_points, 28)
                    reasons.append(f"Nearby supporting area for {poi_name}.")
                elif profile_name in {"sofi", "dtla"} and "los angeles" in market:
                    best_profile_points = max(best_profile_points, 15)
                    flags.append(f"LA market alternate for {poi_name}.")
                elif profile_name == "san_francisco" and any(
                    x in combined_text for x in ["san francisco", "santa clara", "oakland", "san mateo"]
                ):
                    best_profile_points = max(best_profile_points, 35)
                    reasons.append("Relevant Bay Area inventory.")

            score += best_profile_points

            if best_profile_points == 0:
                score -= 35
                flags.append("Weaker fit versus named target area.")

        distance_num = _to_float(row.get("distance_to_poi_miles"))

        if distance_num is not None:
            radius = _to_float(requested_radius)

            if radius:
                if distance_num <= radius:
                    score += 55
                    reasons.append(f"Within requested radius of target POI ({distance_num:.1f} mi).")
                elif distance_num <= radius + 5:
                    score += 25
                    flags.append(f"Slightly outside requested radius but still nearby ({distance_num:.1f} mi).")
                elif distance_num <= radius + 15:
                    score += 5
                    flags.append(f"Outside requested radius but may work as a market support board ({distance_num:.1f} mi).")
                else:
                    score -= 40
                    flags.append(f"Far outside requested radius ({distance_num:.1f} mi).")
            else:
                if distance_num <= 5:
                    score += 40
                    reasons.append(f"Very close to target POI ({distance_num:.1f} mi).")
                elif distance_num <= 10:
                    score += 30
                    reasons.append(f"Close to target POI ({distance_num:.1f} mi).")
                elif distance_num <= 20:
                    score += 10
                    flags.append(f"Moderate POI distance ({distance_num:.1f} mi).")
                else:
                    score -= 15
                    flags.append(f"Farther from target POI ({distance_num:.1f} mi).")

        if requested_media_types:
            requested_text = " ".join(requested_media_types)

            if any(req in media_type or media_type in req for req in requested_media_types):
                score += 35
                reasons.append("Matches requested media format.")
            elif "digital bulletin" in requested_text and "digital" in media_type and "bulletin" in media_type:
                score += 35
                reasons.append("Matches requested digital bulletin format.")
            elif "bulletin" in requested_text and "bulletin" in media_type:
                score += 15
                flags.append("Bulletin format is relevant, but not exact requested format.")
            else:
                score -= 25
                flags.append("Media format is not an exact match.")

        if has_freeway_intent:
            freeway_keywords = [
                "i-5",
                "5 freeway",
                "the 5",
                "i5",
                "interstate 5",
                "101",
                "105",
                "110",
                "405",
                "605",
                "10 freeway",
                "710",
                "91",
                "freeway",
                "highway",
            ]

            matched_freeway_terms = [
                term for term in freeway_keywords
                if term in brief_lower and term in combined_text
            ]

            if matched_freeway_terms:
                score += 45
                reasons.append("Matches requested freeway or route.")
            elif "freeway" in combined_text or "highway" in combined_text:
                score += 20
                flags.append("Freeway board that may support the requested route or market.")

        rate_num = get_rate_value(row)

        if max_unit_rate and rate_num is not None:
            cap = _to_float(max_unit_rate)

            if cap:
                if rate_num <= cap:
                    score += 30
                    reasons.append("Within stated budget.")
                elif rate_num <= cap * 1.15:
                    score += 12
                    flags.append("Slightly over budget; worth considering due to fit.")
                elif rate_num <= cap * 1.35:
                    score -= 3
                    flags.append("Over budget, but may be worth reviewing if location fit is strong.")
                else:
                    score -= 18
                    flags.append("Well over stated budget; planner review recommended.")
        elif max_unit_rate and rate_num is None:
            flags.append("Missing rate; review budget manually.")

        strategic_terms = []

        if "world cup" in brief_lower or " wc" in brief_lower:
            strategic_terms += [
                "stadium",
                "sports",
                "event",
                "freeway",
                "airport",
                "traffic",
                "downtown",
            ]

        if "stadium" in brief_lower:
            strategic_terms += [
                "stadium",
                "sports",
                "event",
                "freeway",
                "traffic",
            ]

        if "golf" in brief_lower:
            strategic_terms += [
                "golf",
                "sports",
                "affluent",
                "coastal",
            ]

        if strategic_terms and any(term in combined_text for term in strategic_terms):
            score += 12
            reasons.append("Strategic context aligns with brief.")

        if score >= 120:
            tier = "Best Match"
        elif score >= 80:
            tier = "Strong Match"
        elif score >= 35:
            tier = "Strategic Alternate"
        else:
            tier = "Planner Review"

        if not reasons:
            reasons.append("Possible alternate; review strategic fit.")

        return pd.Series(
            {
                "proposal_score": score,
                "recommendation_tier": tier,
                "selection_reason": " ".join(dict.fromkeys(reasons)),
                "review_flags": " | ".join(dict.fromkeys(flags)),
            }
        )

    scored = df.apply(score_row, axis=1)
    df["proposal_score"] = scored["proposal_score"]
    df["recommendation_tier"] = scored["recommendation_tier"]
    df["selection_reason"] = scored["selection_reason"]
    df["review_flags"] = scored["review_flags"]

    if requested_markets or requested_cities:
        def is_inside_requested_geo(row: pd.Series) -> bool:
            market = _clean_text(row.get("market"))
            city = _clean_text(row.get("city"))
            description = _clean_text(row.get("description"))
            location = _clean_text(row.get("location"))
            comments = _clean_text(row.get("comments"))
            combined = " ".join([market, city, description, location, comments])

            if requested_markets and any(m in combined for m in requested_markets):
                return True

            if requested_cities and any(c in combined for c in requested_cities):
                return True

            if "sofi" in target_profiles and "los angeles" in combined:
                return True

            if "dtla" in target_profiles and "los angeles" in combined:
                return True

            if "san_francisco" in target_profiles and any(
                x in combined for x in ["san francisco", "santa clara", "oakland", "san mateo"]
            ):
                return True

            return False

        geo_df = df[df.apply(is_inside_requested_geo, axis=1)].copy()

        if not geo_df.empty:
            df = geo_df

    if requested_unit_ids:
        known_df = df[df["unit_id_clean"].isin(requested_unit_ids)].copy()
    else:
        known_df = df.iloc[0:0].copy()

    best_df = df[df["proposal_score"] >= 80].copy()

    alternate_df = df[
        (df["proposal_score"] >= 20)
        & (~df.index.isin(best_df.index))
    ].copy()

    if has_poi_or_distance_intent:
        selected = pd.concat([known_df, best_df, alternate_df], axis=0)
    else:
        selected = pd.concat([known_df, best_df], axis=0)

    selected = selected[~selected.index.duplicated(keep="first")].copy()
    selected = selected.sort_values("proposal_score", ascending=False)

    if selected.empty:
        fallback = df.sort_values("proposal_score", ascending=False).head(min(requested_count, 10)).copy()
        fallback["review_flags"] = fallback["review_flags"].fillna("").astype(str)
        fallback["review_flags"] = (
            fallback["review_flags"]
            + " | Fallback recommendation because no perfect matches were found."
        )
        selected = fallback

    if has_poi_or_distance_intent:
        return selected.head(max(requested_count, 10))

    return selected.head(requested_count)


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="RFP Grid Agent", layout="wide")

if "reset_counter" not in st.session_state:
    st.session_state["reset_counter"] = 0


def start_new_rfp() -> None:
    st.session_state["reset_counter"] += 1
    current_counter = st.session_state["reset_counter"]

    for key in list(st.session_state.keys()):
        if key != "reset_counter":
            del st.session_state[key]

    st.session_state["reset_counter"] = current_counter
    st.rerun()


st.title("RFP Grid Agent")
st.caption(
    "Reads your master pricing workbook, matches boards to an RFP brief, "
    "calculates pricing, and exports a filled grid with audit tabs."
)

reset_key = st.session_state["reset_counter"]

with st.sidebar:
    st.header("Inputs")

    if st.button("Start New RFP", type="secondary", use_container_width=True):
        start_new_rfp()

    st.divider()

    master_file = st.file_uploader(
        "1. Upload master pricing workbook",
        type=["xlsx"],
        key=f"master_file_{reset_key}",
    )

    template_file = st.file_uploader(
        "2. Optional blank agency grid",
        type=["xlsx", "xlsm"],
        key=f"template_file_{reset_key}",
    )

    brief_file = st.file_uploader(
        "3. Optional RFP brief",
        type=["txt", "pdf"],
        key=f"brief_file_{reset_key}",
    )

    use_ai = st.checkbox(
        "Use free cloud AI to read brief",
        value=True,
        key=f"use_ai_{reset_key}",
    )

    groq_model = st.text_input(
        "Groq model",
        value="llama-3.1-8b-instant",
        key=f"groq_model_{reset_key}",
    )

    st.divider()

    run_button = st.button(
        "Run RFP Agent",
        type="primary",
        use_container_width=True,
        key=f"run_button_{reset_key}",
    )

st.subheader("Brief text")
brief_text = ""

if brief_file is not None:
    if brief_file.name.lower().endswith(".pdf"):
        brief_text = extract_text_from_pdf(io.BytesIO(brief_file.getvalue()))
    else:
        brief_text = brief_file.getvalue().decode("utf-8", errors="ignore")

brief_text = st.text_area(
    "Paste or edit RFP brief text",
    value=brief_text,
    height=180,
    key=f"brief_text_{reset_key}",
)

st.subheader("Requirements JSON")

requirements_key = f"requirements_json_{reset_key}"

if requirements_key not in st.session_state:
    st.session_state[requirements_key] = json.dumps(default_requirements(), indent=2)

if st.button("Extract requirements from brief", key=f"extract_requirements_{reset_key}"):
    if not brief_text.strip():
        st.error("Please paste or upload the RFP brief first.")
    else:
        req = extract_requirements(
            brief_text,
            use_ai=use_ai,
            groq_model=groq_model,
        )
        req = apply_target_profiles(req, brief_text)
        st.session_state[requirements_key] = json.dumps(req, indent=2)
        st.success("Requirements extracted. Review them below, then run the agent.")

requirements_json = st.text_area(
    "Review and edit before running. For distance, include POI latitude and longitude in poi_requirements.",
    value=st.session_state[requirements_key],
    height=360,
    key=f"requirements_area_{reset_key}",
)

st.session_state[requirements_key] = requirements_json

if run_button:
    if master_file is None:
        st.error("Upload your master pricing workbook first.")
        st.stop()

    if not brief_text.strip():
        st.error("Please paste or upload the RFP brief before running.")
        st.stop()

    try:
        current_requirements_check = json.loads(requirements_json)
    except Exception:
        current_requirements_check = {}

    requirements_are_blank = not any(
        current_requirements_check.get(field)
        for field in ["markets", "cities", "media_types", "poi_requirements", "known_unit_ids"]
    )

    if requirements_are_blank:
        with st.spinner("Requirements were blank, extracting from brief before running..."):
            req = extract_requirements(
                brief_text,
                use_ai=use_ai,
                groq_model=groq_model,
            )
            req = apply_target_profiles(req, brief_text)
            requirements_json = json.dumps(req, indent=2)
            st.session_state[requirements_key] = requirements_json

    try:
        raw_requirements = json.loads(requirements_json)
        raw_requirements = apply_target_profiles(raw_requirements, brief_text)
        requirements = coerce_requirements(raw_requirements)
    except Exception as exc:
        st.error(f"Requirements JSON is invalid: {exc}")
        st.stop()

    has_geography = bool(
        requirements.get("markets")
        or requirements.get("cities")
        or requirements.get("poi_requirements")
        or requirements.get("matched_target_profiles")
    )

    if not has_geography:
        st.error(
            "No target geography was found. Please add markets, cities, or a POI before running. "
            "This prevents the agent from selecting boards outside the requested location."
        )
        st.stop()

    with st.spinner("Reading and normalizing inventory..."):
        master_bytes = io.BytesIO(master_file.getvalue())
        load_result = normalize_inventory(
            master_bytes,
            column_aliases_path=CONFIG_DIR / "column_aliases.json",
        )
        inventory = load_result.inventory

        st.info(f"Loaded {len(inventory)} inventory rows from the master workbook.")

        if inventory.empty:
            st.error(
                "The master pricing workbook loaded 0 inventory rows. "
                "Please confirm the correct sheet/tab is being read."
            )
            st.stop()

    with st.spinner("Calculating distances if POI lat/long is provided..."):
        poi = get_primary_poi(requirements)

        if poi:
            inventory = add_distance_to_poi(
                inventory,
                poi.get("latitude"),
                poi.get("longitude"),
                poi_name=poi.get("poi_name"),
                poi_address=poi.get("poi_address"),
            )
        else:
            inventory["distance_to_poi_miles"] = None
            inventory["target_location"] = ""
            inventory["distance_note"] = "No POI provided."

    with st.spinner("Calculating pricing..."):
        inventory = add_pricing(
            inventory,
            requirements,
            pricing_rules_path=CONFIG_DIR / "pricing_rules.json",
        )

    with st.spinner("Scoring proposal candidates..."):
        selected = score_proposal_candidates(inventory, requirements, brief_text)
        excluded = inventory[~inventory.index.isin(selected.index)].copy()

    st.success(f"Selected {len(selected)} units. Excluded {len(excluded)} units.")

    if not selected.empty:
        preview_cols = [
            c
            for c in [
                "media_owner",
                "unit_id",
                "media_type",
                "city",
                "availability",
                "description",
                "proposal_score",
                "recommendation_tier",
                "score",
                "four_week_media_cost",
                "install_cost_final",
                "production_cost_final",
                "taxes",
                "distance_to_poi_miles",
                "contracted_media_cost",
                "total_campaign_cost",
                "cpm",
                "selection_reason",
                "review_flags",
            ]
            if c in selected.columns
        ]

        st.dataframe(selected[preview_cols], use_container_width=True)

    with st.spinner("Writing output workbook..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_out:
            output_path = Path(tmp_out.name)

        template_path = None

        if template_file is not None:
            suffix = ".xlsm" if template_file.name.lower().endswith(".xlsm") else ".xlsx"

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_template:
                tmp_template.write(template_file.getvalue())
                template_path = Path(tmp_template.name)

        write_output_workbook(
            selected=selected,
            excluded=excluded,
            requirements=requirements,
            missing_fields=load_result.missing_fields,
            output_path=output_path,
            template_path=template_path,
            column_aliases_path=CONFIG_DIR / "column_aliases.json",
        )

    output_filename = "filled_rfp_grid.xlsx"

    st.download_button(
        label="Download filled RFP workbook (.xlsx)",
        data=output_path.read_bytes(),
        file_name=output_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"download_button_{reset_key}",
    )

    with st.expander("Missing fields report"):
        st.dataframe(load_result.missing_fields, use_container_width=True)
