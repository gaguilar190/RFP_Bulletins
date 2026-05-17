from __future__ import annotations

import io
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from distance import add_distance_to_poi
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


def _row_text(row: pd.Series) -> str:
    parts = []
    for col in [
        "unit_id",
        "media_owner",
        "media_type",
        "market",
        "city",
        "locale",
        "description",
        "comments",
        "location",
        "freeway_street",
        "freeway",
        "direction",
        "availability",
    ]:
        if col in row.index:
            parts.append(str(row.get(col) or ""))
    return _clean_text(" ".join(parts))


def _get_rate_value(row: pd.Series) -> float | None:
    for col in [
        "four_week_media_cost",
        "negotiated_rate_4wk",
        "negotiated_rate",
        "contracted_media_cost",
        "rate_card",
    ]:
        if col in row.index:
            value = _to_float(row.get(col))
            if value is not None:
                return value
    return None


def _extract_requested_freeways(brief_text: str) -> list[str]:
    brief_lower = _clean_text(brief_text)

    freeway_map = {
        "5": ["5 freeway", "i-5", "i5", "interstate 5", "the 5"],
        "10": ["10 freeway", "i-10", "i10", "interstate 10", "the 10"],
        "91": ["91 freeway", "i-91", "the 91"],
        "101": ["101 freeway", "i-101", "the 101"],
        "105": ["105 freeway", "i-105", "the 105"],
        "110": ["110 freeway", "i-110", "the 110"],
        "405": ["405 freeway", "i-405", "the 405"],
        "605": ["605 freeway", "i-605", "the 605"],
        "710": ["710 freeway", "i-710", "the 710"],
    }

    requested = []
    for freeway, terms in freeway_map.items():
        if any(term in brief_lower for term in terms):
            requested.append(freeway)

    return requested


def _board_mentions_freeway(row_text: str, freeway: str) -> bool:
    freeway_terms = [
        freeway,
        f"i-{freeway}",
        f"i{freeway}",
        f"{freeway} freeway",
        f"the {freeway}",
        f"interstate {freeway}",
    ]
    return any(term in row_text for term in freeway_terms)


def _is_wrong_market(row_text: str, requested_markets: list[str], requested_cities: list[str]) -> bool:
    if not requested_markets and not requested_cities:
        return False

    aliases = {
        "los angeles": [
            "los angeles",
            "inglewood",
            "lynwood",
            "compton",
            "carson",
            "la mirada",
            "buena park",
            "santa fe springs",
            "hawthorne",
            "el segundo",
            "westchester",
            "playa vista",
            "long beach",
        ],
        "san francisco": [
            "san francisco",
            "santa clara",
            "oakland",
            "san mateo",
            "south san francisco",
            "daly city",
        ],
        "new york": ["new york", "new jersey", "ny/nj", "east rutherford"],
        "new jersey": ["new york", "new jersey", "ny/nj", "east rutherford"],
        "atlanta": ["atlanta"],
        "boston": ["boston", "foxborough"],
        "dallas": ["dallas", "arlington"],
        "houston": ["houston"],
        "kansas city": ["kansas city"],
        "philadelphia": ["philadelphia"],
        "sacramento": ["sacramento", "west sacramento", "elk grove", "roseville"],
        "san jose": ["san jose", "santa clara", "sunnyvale", "cupertino"],
        "santa cruz": ["santa cruz", "capitola", "watsonville"],
    }

    expanded_terms = []
    for term in requested_markets + requested_cities:
        clean_term = _clean_text(term)
        expanded_terms.append(clean_term)
        expanded_terms.extend(aliases.get(clean_term, []))

    return not any(term and term in row_text for term in expanded_terms)


def _classify_media_fit(media_type: str, requested_media_types: list[str]) -> tuple[int, str, str]:
    media_type_clean = _clean_text(media_type)
    requested = [_clean_text(x) for x in requested_media_types]
    requested_text = " ".join(requested)

    if not requested:
        return 0, "", ""

    if any(req in media_type_clean or media_type_clean in req for req in requested):
        return 35, "Matches requested media format.", ""

    if "digital bulletin" in requested_text and "digital" in media_type_clean and "bulletin" in media_type_clean:
        return 35, "Matches requested digital bulletin format.", ""

    if "bulletin" in requested_text and "bulletin" in media_type_clean:
        return 10, "", "Bulletin format is relevant, but not exact requested format."

    return -30, "", "Media format does not match the request."


# -----------------------------
# Planner Memory helpers
# -----------------------------

MEMORY_COLUMNS = [
    "timestamp",
    "advertiser",
    "unit_id",
    "action",
    "market",
    "city",
    "media_type",
    "recommendation_tier",
    "proposal_role",
    "proposal_score",
    "rfp_markets",
    "rfp_cities",
    "rfp_media_types",
    "rfp_tags",
    "notes",
]


def load_planner_memory(memory_file) -> pd.DataFrame:
    if memory_file is None:
        return pd.DataFrame(columns=MEMORY_COLUMNS)

    try:
        memory = pd.read_csv(memory_file)
    except Exception:
        return pd.DataFrame(columns=MEMORY_COLUMNS)

    for col in MEMORY_COLUMNS:
        if col not in memory.columns:
            memory[col] = ""

    memory["unit_id_clean"] = memory["unit_id"].apply(_clean_unit_id)
    return memory


def get_rfp_tags(requirements: dict[str, Any], brief_text: str) -> list[str]:
    brief_lower = _clean_text(brief_text)
    tags = []

    tag_keywords = {
        "stadium": ["stadium", "sofi", "levi", "metlife", "gillette", "nrg", "arrowhead"],
        "world_cup": ["world cup", " wc"],
        "freeway": ["freeway", "highway", "i-5", "405", "105", "110", "710", "605", "91"],
        "radius": ["radius", "mile", "within"],
        "store_list": ["store list", "stores", "locations"],
        "digital": ["digital bulletin", "digital bulletins", "digital"],
        "budget_sensitive": ["budget", "under", "less than", "$15k", "$15,000"],
    }

    for tag, keywords in tag_keywords.items():
        if any(keyword in brief_lower for keyword in keywords):
            tags.append(tag)

    if requirements.get("poi_requirements"):
        tags.append("poi")

    if requirements.get("max_distance_miles"):
        tags.append("distance_based")

    return sorted(set(tags))


def planner_memory_adjustment(
    row: pd.Series,
    planner_memory: pd.DataFrame,
    requirements: dict[str, Any],
    brief_text: str,
) -> tuple[int, str, str]:
    if planner_memory is None or planner_memory.empty:
        return 0, "", ""

    unit_id = _clean_unit_id(row.get("unit_id"))
    if not unit_id:
        return 0, "", ""

    memory = planner_memory.copy()

    if "unit_id_clean" not in memory.columns:
        memory["unit_id_clean"] = memory["unit_id"].apply(_clean_unit_id)

    unit_history = memory[memory["unit_id_clean"] == unit_id].copy()

    if unit_history.empty:
        return 0, "", ""

    current_tags = set(get_rfp_tags(requirements, brief_text))
    current_markets = {_clean_text(x) for x in (requirements.get("markets") or [])}
    current_media = {_clean_text(x) for x in (requirements.get("media_types") or [])}

    adjustment = 0
    positive_hits = 0
    negative_hits = 0

    for _, memory_row in unit_history.iterrows():
        action = _clean_text(memory_row.get("action"))
        memory_market = _clean_text(memory_row.get("market"))
        memory_media = _clean_text(memory_row.get("media_type"))
        memory_tags = set(str(memory_row.get("rfp_tags") or "").split("|"))

        context_match = False

        if memory_market and any(market in memory_market or memory_market in market for market in current_markets):
            context_match = True

        if memory_media and any(media in memory_media or memory_media in media for media in current_media):
            context_match = True

        if current_tags and memory_tags and current_tags.intersection(memory_tags):
            context_match = True

        if not context_match:
            continue

        if action in ["kept", "submitted", "client approved", "client_approved"]:
            adjustment += 18
            positive_hits += 1
        elif action in ["premium exception", "premium_exception"]:
            adjustment += 8
            positive_hits += 1
        elif action in ["removed", "rejected", "do not propose", "do_not_propose"]:
            adjustment -= 22
            negative_hits += 1

    if adjustment > 0:
        return adjustment, f"Planner memory boost based on {positive_hits} similar past selection(s).", ""

    if adjustment < 0:
        return adjustment, "", f"Planner memory penalty based on {negative_hits} similar past removal(s)."

    return 0, "", ""


def build_memory_rows(
    selected_df: pd.DataFrame,
    requirements: dict[str, Any],
    brief_text: str,
    kept_unit_ids: list[str],
    removed_unit_ids: list[str],
    advertiser: str,
    notes: str,
) -> pd.DataFrame:
    rows = []
    timestamp = datetime.now().isoformat(timespec="seconds")
    rfp_tags = "|".join(get_rfp_tags(requirements, brief_text))

    kept_clean = {_clean_unit_id(x) for x in kept_unit_ids}
    removed_clean = {_clean_unit_id(x) for x in removed_unit_ids}

    for _, row in selected_df.iterrows():
        unit_id = str(row.get("unit_id") or "")
        unit_id_clean = _clean_unit_id(unit_id)

        if unit_id_clean in removed_clean:
            action = "removed"
        elif unit_id_clean in kept_clean:
            action = "kept"
        else:
            action = "removed"

        rows.append(
            {
                "timestamp": timestamp,
                "advertiser": advertiser,
                "unit_id": unit_id,
                "action": action,
                "market": row.get("market", ""),
                "city": row.get("city", ""),
                "media_type": row.get("media_type", ""),
                "recommendation_tier": row.get("recommendation_tier", ""),
                "proposal_role": row.get("proposal_role", ""),
                "proposal_score": row.get("proposal_score", ""),
                "rfp_markets": "|".join(str(x) for x in (requirements.get("markets") or [])),
                "rfp_cities": "|".join(str(x) for x in (requirements.get("cities") or [])),
                "rfp_media_types": "|".join(str(x) for x in (requirements.get("media_types") or [])),
                "rfp_tags": rfp_tags,
                "notes": notes,
            }
        )

    return pd.DataFrame(rows, columns=MEMORY_COLUMNS)


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
            "lynwood",
            "compton",
            "carson",
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

    market_keywords = {
        "Los Angeles": ["los angeles", "sofi", "inglewood", "dtla"],
        "San Francisco": ["san francisco", "san francsico", " sf ", "levi", "santa clara"],
        "Sacramento": ["sacramento", "sacto"],
        "San Jose": ["san jose"],
        "Santa Cruz": ["santa cruz"],
        "Atlanta": ["atlanta", "mercedes-benz"],
        "Boston": ["boston", "gillette", "foxborough"],
        "Dallas": ["dallas", "at&t stadium", "arlington"],
        "Houston": ["houston", "nrg"],
        "Kansas City": ["kansas city", "arrowhead"],
        "Philadelphia": ["philadelphia", "lincoln financial"],
        "New York": ["new york", "ny/nj", "metlife"],
        "New Jersey": ["new jersey", "ny/nj", "metlife"],
    }

    for market_name, keywords in market_keywords.items():
        if any(keyword in f" {brief_lower} " for keyword in keywords):
            if market_name not in markets:
                markets.append(market_name)

    city_keywords = {
        "Los Angeles": ["los angeles", "dtla"],
        "Inglewood": ["sofi", "inglewood"],
        "San Francisco": ["san francisco", "san francsico", " sf "],
        "Santa Clara": ["levi", "santa clara"],
        "Sacramento": ["sacramento", "sacto"],
        "San Jose": ["san jose"],
        "Santa Cruz": ["santa cruz"],
        "Atlanta": ["atlanta", "mercedes-benz"],
        "Houston": ["houston", "nrg"],
        "Kansas City": ["kansas city", "arrowhead"],
        "Philadelphia": ["philadelphia", "lincoln financial"],
    }

    for city_name, keywords in city_keywords.items():
        if any(keyword in f" {brief_lower} " for keyword in keywords):
            if city_name not in cities:
                cities.append(city_name)

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
        or "under $15,000" in brief_lower
        or "under 15000" in brief_lower
        or "under $15k" in brief_lower
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
    planner_memory: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if inventory is None or inventory.empty:
        return inventory

    df = inventory.copy()
    brief_lower = _clean_text(brief_text)

    requested_markets = [_clean_text(x) for x in (requirements.get("markets") or [])]
    requested_cities = [_clean_text(x) for x in (requirements.get("cities") or [])]
    requested_media_types = [_clean_text(x) for x in (requirements.get("media_types") or [])]
    requested_unit_ids = {_clean_unit_id(x) for x in (requirements.get("known_unit_ids") or [])}

    max_unit_rate = _to_float(requirements.get("max_unit_rate"))
    requested_radius = _to_float(requirements.get("max_distance_miles"))
    requested_count = int(requirements.get("number_of_units") or 25)

    target_profiles = requirements.get("matched_target_profiles") or detect_target_profiles(brief_text)
    requested_freeways = _extract_requested_freeways(brief_text)

    has_stadium_intent = "stadium" in brief_lower or bool(requirements.get("poi_requirements"))
    has_radius_intent = requested_radius is not None or "radius" in brief_lower or "mile" in brief_lower
    has_freeway_intent = bool(requested_freeways) or "freeway" in brief_lower or "highway" in brief_lower

    if "unit_id" in df.columns:
        df["unit_id_clean"] = df["unit_id"].apply(_clean_unit_id)
    else:
        df["unit_id_clean"] = ""

    def score_row(row: pd.Series) -> pd.Series:
        score = 0
        reasons = []
        flags = []
        recommendation_tier = "Planner Review"
        proposal_role = "Review"

        row_text = _row_text(row)

        unit_id = _clean_unit_id(row.get("unit_id"))
        media_type = str(row.get("media_type") or "")
        rate_num = _get_rate_value(row)
        distance_num = _to_float(row.get("distance_to_poi_miles"))

        wrong_market = _is_wrong_market(row_text, requested_markets, requested_cities)

        if wrong_market:
            score -= 200
            flags.append("Outside requested market/geography.")
        else:
            if requested_markets or requested_cities:
                score += 50
                reasons.append("Supports the requested market/geography.")

        if requested_unit_ids and unit_id in requested_unit_ids:
            score += 90
            reasons.append("Specific requested or known priority unit.")

        for profile_name in target_profiles:
            profile = TARGET_HINTS.get(profile_name, {})
            primary_units = {_clean_unit_id(x) for x in profile.get("primary_units", [])}
            primary_cities = [_clean_text(x) for x in profile.get("primary_cities", [])]
            nearby_cities = [_clean_text(x) for x in profile.get("nearby_cities", [])]
            poi_name = profile.get("poi", {}).get("poi_name") if profile.get("poi") else profile_name

            if unit_id in primary_units:
                score += 95
                reasons.append(f"Known strong fit for {poi_name}.")
            elif any(city in row_text for city in primary_cities):
                score += 55
                reasons.append(f"Located in the primary target area for {poi_name}.")
            elif any(city in row_text for city in nearby_cities):
                score += 30
                reasons.append(f"Nearby/supporting area for {poi_name}.")
            elif profile_name in {"sofi", "dtla"} and "los angeles" in row_text:
                score += 15
                flags.append(f"LA market support board for {poi_name}, but not the closest option.")
            elif profile_name == "san_francisco" and any(
                term in row_text for term in ["san francisco", "santa clara", "oakland", "san mateo"]
            ):
                score += 25
                reasons.append("Relevant Bay Area support inventory.")

        if distance_num is not None:
            if requested_radius:
                if distance_num <= requested_radius:
                    score += 65
                    reasons.append(f"Within requested radius of target POI ({distance_num:.1f} mi).")
                elif distance_num <= requested_radius + 5:
                    score += 35
                    flags.append(f"Slightly outside requested radius, but still nearby ({distance_num:.1f} mi).")
                elif distance_num <= requested_radius + 15:
                    score += 15
                    flags.append(f"Outside requested radius, but may work as market/freeway support ({distance_num:.1f} mi).")
                else:
                    score -= 35
                    flags.append(f"Far outside requested radius ({distance_num:.1f} mi).")
            else:
                if distance_num <= 5:
                    score += 55
                    reasons.append(f"Very close to target POI ({distance_num:.1f} mi).")
                elif distance_num <= 10:
                    score += 40
                    reasons.append(f"Close to target POI ({distance_num:.1f} mi).")
                elif distance_num <= 20:
                    score += 20
                    flags.append(f"Moderate POI distance, but still useful as market support ({distance_num:.1f} mi).")
                elif distance_num <= 35 and has_stadium_intent:
                    score += 5
                    flags.append(f"Farther from stadium, but may still support the market ({distance_num:.1f} mi).")
                else:
                    score -= 20
                    flags.append(f"Far from target POI ({distance_num:.1f} mi).")

        media_points, media_reason, media_flag = _classify_media_fit(media_type, requested_media_types)
        score += media_points

        if media_reason:
            reasons.append(media_reason)
        if media_flag:
            flags.append(media_flag)

        if has_freeway_intent:
            if requested_freeways:
                matched_freeways = [
                    freeway for freeway in requested_freeways
                    if _board_mentions_freeway(row_text, freeway)
                ]

                if matched_freeways:
                    score += 55
                    reasons.append(f"Matches requested freeway route: {', '.join(matched_freeways)}.")
                elif "freeway" in row_text or "highway" in row_text:
                    score += 25
                    flags.append("Freeway board that may support the requested market or route.")
            else:
                if "freeway" in row_text or "highway" in row_text:
                    score += 25
                    reasons.append("Freeway board that can support market traffic flow.")

        if "world cup" in brief_lower or " wc" in brief_lower or has_stadium_intent:
            if any(term in row_text for term in ["stadium", "airport", "lax", "freeway", "traffic", "downtown"]):
                score += 15
                reasons.append("Strategic context aligns with event/stadium traffic.")

        if max_unit_rate and rate_num is not None:
            if rate_num <= max_unit_rate:
                score += 35
                reasons.append("Within stated budget.")
            elif rate_num <= max_unit_rate * 1.15:
                score += 15
                flags.append("Slightly over budget; worth considering due to strategic fit.")
            elif rate_num <= max_unit_rate * 1.40:
                score -= 5
                flags.append("Over budget, but may be worth reviewing if location fit is strong.")
            else:
                score -= 25
                flags.append("Well over stated budget; include only as a premium exception.")
        elif max_unit_rate and rate_num is None:
            flags.append("Missing rate; review budget manually.")

        memory_delta, memory_reason, memory_flag = planner_memory_adjustment(
            row=row,
            planner_memory=planner_memory,
            requirements=requirements,
            brief_text=brief_text,
        )

        score += memory_delta

        if memory_reason:
            reasons.append(memory_reason)

        if memory_flag:
            flags.append(memory_flag)

        if wrong_market and score < 0:
            recommendation_tier = "Exclude"
            proposal_role = "Wrong Market"
        elif max_unit_rate and rate_num and rate_num > max_unit_rate and score >= 90:
            recommendation_tier = "Premium Exception"
            proposal_role = "Premium Exception"
        elif score >= 130:
            recommendation_tier = "Best Match"
            proposal_role = "Primary Recommendation"
        elif score >= 90:
            recommendation_tier = "Strong Match"
            proposal_role = "Primary Recommendation"
        elif score >= 45:
            recommendation_tier = "Strategic Alternate"
            proposal_role = "Strategic Alternate"
        else:
            recommendation_tier = "Planner Review"
            proposal_role = "Planner Review"

        if not reasons:
            reasons.append("Possible alternate; review strategic fit.")

        return pd.Series(
            {
                "proposal_score": score,
                "recommendation_tier": recommendation_tier,
                "proposal_role": proposal_role,
                "selection_reason": " ".join(dict.fromkeys(reasons)),
                "review_flags": " | ".join(dict.fromkeys(flags)),
            }
        )

    scored = df.apply(score_row, axis=1)
    df["proposal_score"] = scored["proposal_score"]
    df["recommendation_tier"] = scored["recommendation_tier"]
    df["proposal_role"] = scored["proposal_role"]
    df["selection_reason"] = scored["selection_reason"]
    df["review_flags"] = scored["review_flags"]

    usable = df[df["recommendation_tier"] != "Exclude"].copy()

    if usable.empty:
        usable = df.sort_values("proposal_score", ascending=False).head(min(requested_count, 10)).copy()
        usable["review_flags"] = usable["review_flags"].fillna("").astype(str)
        usable["review_flags"] = usable["review_flags"] + " | Fallback only because no in-market boards were found."

    primary = usable[usable["proposal_role"] == "Primary Recommendation"].copy()
    strategic = usable[usable["proposal_role"] == "Strategic Alternate"].copy()
    premium = usable[usable["proposal_role"] == "Premium Exception"].copy()
    review = usable[usable["proposal_role"] == "Planner Review"].copy()

    primary = primary.sort_values("proposal_score", ascending=False)
    strategic = strategic.sort_values("proposal_score", ascending=False)
    premium = premium.sort_values("proposal_score", ascending=False)
    review = review.sort_values("proposal_score", ascending=False)

    selected_parts = []

    if not primary.empty:
        selected_parts.append(primary.head(requested_count))

    if has_radius_intent or has_freeway_intent or has_stadium_intent:
        selected_parts.append(strategic.head(max(5, requested_count // 2)))

    selected_parts.append(premium.head(3))

    if selected_parts:
        selected = pd.concat(selected_parts, axis=0)
    else:
        selected = review.head(min(requested_count, 10))

    selected = selected[~selected.index.duplicated(keep="first")].copy()
    selected = selected.sort_values("proposal_score", ascending=False)

    if selected.empty:
        selected = usable.sort_values("proposal_score", ascending=False).head(min(requested_count, 10)).copy()
        selected["review_flags"] = selected["review_flags"].fillna("").astype(str)
        selected["review_flags"] = selected["review_flags"] + " | Fallback recommendation because no perfect matches were found."

    return selected.head(max(requested_count, 10))


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

    planner_memory_file = st.file_uploader(
        "4. Optional planner memory CSV",
        type=["csv"],
        key=f"planner_memory_file_{reset_key}",
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

    planner_memory = load_planner_memory(planner_memory_file)

    if planner_memory_file is not None:
        st.info(f"Loaded {len(planner_memory)} planner memory rows.")

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
        pois = requirements.get("poi_requirements") or []

        if len(pois) == 1 and isinstance(pois[0], dict):
            poi = pois[0]
            inventory = add_distance_to_poi(
                inventory,
                poi.get("latitude"),
                poi.get("longitude"),
                poi_name=poi.get("poi_name"),
                poi_address=poi.get("poi_address"),
            )

        elif len(pois) > 1:
            if "distance_to_poi_miles" not in inventory.columns:
                inventory["distance_to_poi_miles"] = None
            inventory["target_location"] = "Multiple POIs"
            inventory["distance_note"] = (
                "Multiple POIs provided. Scoring uses market, city, freeway, media type, "
                "budget, and any available distance data."
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
        selected = score_proposal_candidates(
            inventory=inventory,
            requirements=requirements,
            brief_text=brief_text,
            planner_memory=planner_memory,
        )
        excluded = inventory[~inventory.index.isin(selected.index)].copy()

    st.success(f"Selected {len(selected)} units. Excluded {len(excluded)} units.")

    st.session_state[f"last_selected_{reset_key}"] = selected
    st.session_state[f"last_requirements_{reset_key}"] = requirements
    st.session_state[f"last_brief_text_{reset_key}"] = brief_text
    st.session_state[f"last_planner_memory_{reset_key}"] = planner_memory

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
                "proposal_role",
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


# -----------------------------
# Planner Memory feedback section
# -----------------------------

last_selected_key = f"last_selected_{reset_key}"
last_requirements_key = f"last_requirements_{reset_key}"
last_brief_key = f"last_brief_text_{reset_key}"
last_memory_key = f"last_planner_memory_{reset_key}"

if last_selected_key in st.session_state:
    selected_for_memory = st.session_state[last_selected_key]
    requirements_for_memory = st.session_state[last_requirements_key]
    brief_for_memory = st.session_state[last_brief_key]
    existing_memory = st.session_state.get(last_memory_key, pd.DataFrame(columns=MEMORY_COLUMNS))

    with st.expander("Teach the agent from this RFP"):
        st.write(
            "Mark which units you would actually keep or remove. "
            "Then download the updated planner memory CSV and upload it on future RFPs."
        )

        unit_options = [
            str(x)
            for x in selected_for_memory.get("unit_id", pd.Series(dtype=str)).dropna().tolist()
        ]

        advertiser_name = st.text_input(
            "Advertiser or RFP name",
            value="",
            key=f"memory_advertiser_{reset_key}",
        )

        kept_units = st.multiselect(
            "Units you would keep/propose",
            options=unit_options,
            default=unit_options,
            key=f"memory_kept_units_{reset_key}",
        )

        removed_units = st.multiselect(
            "Units you would remove/not propose",
            options=unit_options,
            default=[],
            key=f"memory_removed_units_{reset_key}",
        )

        memory_notes = st.text_area(
            "Planner notes",
            value="",
            placeholder="Example: Keep 105/405 corridor for SoFi. Use 40575/40576 only as premium exceptions.",
            key=f"memory_notes_{reset_key}",
        )

        if st.button("Create updated planner memory CSV", key=f"create_memory_{reset_key}"):
            new_memory_rows = build_memory_rows(
                selected_df=selected_for_memory,
                requirements=requirements_for_memory,
                brief_text=brief_for_memory,
                kept_unit_ids=kept_units,
                removed_unit_ids=removed_units,
                advertiser=advertiser_name,
                notes=memory_notes,
            )

            updated_memory = pd.concat(
                [existing_memory[MEMORY_COLUMNS], new_memory_rows],
                ignore_index=True,
            )

            st.session_state[f"updated_memory_{reset_key}"] = updated_memory
            st.success("Planner memory updated. Download it below and use it on your next RFP.")

        updated_memory_key = f"updated_memory_{reset_key}"

        if updated_memory_key in st.session_state:
            updated_memory = st.session_state[updated_memory_key]

            st.download_button(
                label="Download updated planner memory CSV",
                data=updated_memory.to_csv(index=False).encode("utf-8"),
                file_name="planner_memory.csv",
                mime="text/csv",
                key=f"download_memory_{reset_key}",
            )
