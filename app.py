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
    },
    "levi's stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
    },
    "levis stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
    },
    "levi’s stadium": {
        "poi_name": "Levi's Stadium",
        "poi_address": "4900 Marie P DeBartolo Way, Santa Clara, CA",
        "latitude": 37.403,
        "longitude": -121.970,
        "priority": 1,
    },
    "metlife stadium": {
        "poi_name": "MetLife Stadium",
        "poi_address": "1 MetLife Stadium Dr, East Rutherford, NJ",
        "latitude": 40.8135,
        "longitude": -74.0745,
        "priority": 3,
    },
    "gillette stadium": {
        "poi_name": "Gillette Stadium",
        "poi_address": "1 Patriot Pl, Foxborough, MA",
        "latitude": 42.0909,
        "longitude": -71.2643,
        "priority": 3,
    },
    "at&t stadium": {
        "poi_name": "AT&T Stadium",
        "poi_address": "1 AT&T Way, Arlington, TX",
        "latitude": 32.7473,
        "longitude": -97.0945,
        "priority": 3,
    },
    "nrg stadium": {
        "poi_name": "NRG Stadium",
        "poi_address": "NRG Pkwy, Houston, TX",
        "latitude": 29.6847,
        "longitude": -95.4107,
        "priority": 3,
    },
    "arrowhead stadium": {
        "poi_name": "Arrowhead Stadium",
        "poi_address": "1 Arrowhead Dr, Kansas City, MO",
        "latitude": 39.0490,
        "longitude": -94.4839,
        "priority": 3,
    },
    "mercedes-benz stadium": {
        "poi_name": "Mercedes-Benz Stadium",
        "poi_address": "1 AMB Dr NW, Atlanta, GA",
        "latitude": 33.7554,
        "longitude": -84.4008,
        "priority": 3,
    },
    "lincoln financial field": {
        "poi_name": "Lincoln Financial Field",
        "poi_address": "One Lincoln Financial Field Way, Philadelphia, PA",
        "latitude": 39.9008,
        "longitude": -75.1675,
        "priority": 3,
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
    This helps the app understand SoFi, DTLA, SF, Sacto, stadiums, and nearby areas.
    """
    requirements = dict(raw_requirements or {})
    brief_lower = _clean_text(brief_text)

    markets = list(requirements.get("markets") or [])
    cities = list(requirements.get("cities") or [])
    raw_pois = list(requirements.get("poi_requirements") or [])
    cleaned_pois = []
    known_unit_ids = list(requirements.get("known_unit_ids") or [])

    # Convert AI output like ["SoFi Stadium"] into proper POI dictionaries.
    for item in raw_pois:
        if isinstance(item, dict):
            cleaned_pois.append(item)
        elif isinstance(item, str):
            key = _clean_text(item)
            if key in KNOWN_POIS:
                cleaned_pois.append(KNOWN_POIS[key])
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

    # Add any known POIs from the brief text, even if AI did not extract them.
    for key, poi in KNOWN_POIS.items():
        if key in brief_lower:
            existing_names = [
                _clean_text(p.get("poi_name"))
                for p in cleaned_pois
                if isinstance(p, dict)
            ]
            if _clean_text(poi.get("poi_name")) not in existing_names:
                cleaned_pois.append(poi)

    # Fix common city/market extraction gaps.
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

    # Media type correction.
    if "digital bulletins" in brief_lower or "digital bulletin" in brief_lower:
        requirements["media_types"] = ["Digital Bulletin"]
    elif "bulletins" in brief_lower or "bulletin" in brief_lower:
        media_types = set(requirements.get("media_types") or [])
        media_types.update(["Static Bulletin", "Digital Bulletin", "Bulletin"])
        requirements["media_types"] = list(media_types)

    # Budget should be guidance, not a hard blocker.
    if (
        "less than $15,000" in brief_lower
        or "less than 15000" in brief_lower
        or "higher than $15k" in brief_lower
        or "higher than 15k" in brief_lower
        or "$15k" in brief_lower
        or "$15,000" in brief_lower
        or "15000" in brief_lower
    ):
        requirements["max_unit_rate"] = 15000

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
    Scores and ranks proposal-worthy units.

    Key behavior:
    - SoFi Stadium returns 40575 and 40576 only when available.
    - DTLA returns 10126 and 0103/103 only when available.
    - Other POI/stadium/neighborhood requests are limited to a reasonable POI radius.
    - Broader market requests can return more units.
    - Budget is guidance and gets flagged, not used as a hard blocker.
    """
    if inventory is None or inventory.empty:
        return inventory

    df = inventory.copy()
    brief_lower = _clean_text(brief_text)
    target_profiles = requirements.get("matched_target_profiles") or detect_target_profiles(brief_text)

    requested_media_types = [_clean_text(x) for x in (requirements.get("media_types") or [])]
    requested_markets = [_clean_text(x) for x in (requirements.get("markets") or [])]
    requested_cities = [_clean_text(x) for x in (requirements.get("cities") or [])]
    max_unit_rate = requirements.get("max_unit_rate")

    if "unit_id" in df.columns:
        df["unit_id_clean"] = df["unit_id"].apply(_clean_unit_id)
    else:
        df["unit_id_clean"] = ""

    def score_row(row: pd.Series) -> pd.Series:
        score = 0
        reasons = []
        flags = []

        unit_id = _clean_unit_id(row.get("unit_id"))
        city = _clean_text(row.get("city"))
        market = _clean_text(row.get("market"))
        media_type = _clean_text(row.get("media_type"))
        description = _clean_text(row.get("description"))
        comments = _clean_text(row.get("comments"))
        location = _clean_text(row.get("location"))
        combined_text = " ".join([city, market, media_type, description, comments, location])

        # 1. Target profile fit
        if target_profiles:
            best_profile_points = 0

            for profile_name in target_profiles:
                profile = TARGET_HINTS.get(profile_name, {})
                primary_units = {_clean_unit_id(x) for x in profile.get("primary_units", [])}
                primary_cities = profile.get("primary_cities", [])
                nearby_cities = profile.get("nearby_cities", [])
                poi_name = profile.get("poi", {}).get("poi_name") if profile.get("poi") else profile_name

                if unit_id in primary_units:
                    best_profile_points = max(best_profile_points, 70)
                    reasons.append(f"Known strong fit for {poi_name}.")
                elif any(c in city or c in combined_text for c in primary_cities):
                    best_profile_points = max(best_profile_points, 35)
                    reasons.append(f"Located in primary target area for {poi_name}.")
                elif any(c in city or c in combined_text for c in nearby_cities):
                    best_profile_points = max(best_profile_points, 18)
                    reasons.append(f"Nearby/supporting area for {poi_name}.")
                elif profile_name in {"sofi", "dtla"} and "los angeles" in market:
                    best_profile_points = max(best_profile_points, 5)
                    flags.append(f"Broad LA fit, but not closest to {poi_name}.")
                elif profile_name == "san_francisco" and any(
                    x in combined_text for x in ["san francisco", "santa clara", "oakland", "san mateo"]
                ):
                    best_profile_points = max(best_profile_points, 25)
                    reasons.append("Relevant Bay Area/SF-area inventory.")
                elif profile_name == "sacramento" and "sacramento" in combined_text:
                    best_profile_points = max(best_profile_points, 25)
                    reasons.append("Relevant Sacramento-area inventory.")

            score += best_profile_points

            if best_profile_points == 0:
                score -= 40
                flags.append("Weak location fit versus requested target area.")

        # 2. General market/city fit
        if requested_markets or requested_cities:
            if requested_markets and any(m in market or m in combined_text for m in requested_markets):
                score += 15
                reasons.append("Matches requested market.")
            elif requested_cities and any(c in city or c in combined_text for c in requested_cities):
                score += 15
                reasons.append("Matches requested city/area.")
            else:
                score -= 40
                flags.append("Outside requested market/city.")

        # 3. Distance fit
        distance = row.get("distance_to_poi_miles")
        try:
            distance_num = float(distance)
            if distance_num <= 5:
                score += 35
                reasons.append(f"Very close to target POI ({distance_num:.1f} mi).")
            elif distance_num <= 10:
                score += 25
                reasons.append(f"Close to target POI ({distance_num:.1f} mi).")
            elif distance_num <= 15:
                score += 15
                flags.append(f"Moderate POI distance ({distance_num:.1f} mi).")
            elif distance_num <= 20:
                score -= 5
                flags.append(f"Farther from POI ({distance_num:.1f} mi).")
            else:
                score -= 35
                flags.append(f"Too far from target POI ({distance_num:.1f} mi).")
        except Exception:
            pass

        # 4. Media format fit
        if requested_media_types:
            requested_text = " ".join(requested_media_types)

            if any(req in media_type or media_type in req for req in requested_media_types):
                score += 20
                reasons.append("Matches requested media format.")
            elif "digital bulletin" in requested_text and "digital" in media_type and "bulletin" in media_type:
                score += 20
                reasons.append("Matches requested digital bulletin format.")
            elif "bulletin" in requested_text and "bulletin" in media_type:
                score += 5
                flags.append("Bulletin format is relevant, but not exact requested format.")
            else:
                score -= 15
                flags.append("Media format is not an exact match.")

        # 5. Budget fit as guidance, not exclusion
        if max_unit_rate:
            rate = row.get("four_week_media_cost", row.get("negotiated_rate_4wk", None))
            try:
                rate_num = float(rate)
                cap = float(max_unit_rate)

                if rate_num <= cap:
                    score += 15
                    reasons.append("Within stated budget.")
                elif rate_num <= cap * 1.20:
                    score += 5
                    flags.append("Slightly over budget; worth reviewing.")
                else:
                    score -= 5
                    flags.append("Over stated budget; include only if strategically relevant.")
            except Exception:
                flags.append("Missing rate; review budget manually.")

        # 6. Strategic context
        strategic_terms = []
        if "world cup" in brief_lower:
            strategic_terms += ["stadium", "sports", "event", "freeway", "airport", "traffic"]
        if "golf" in brief_lower:
            strategic_terms += ["golf", "sports", "affluent", "coastal"]

        if strategic_terms and any(term in combined_text for term in strategic_terms):
            score += 8
            reasons.append("Strategic context aligns with brief.")

        if not reasons:
            reasons.append("Possible alternate; review strategic fit.")

        return pd.Series(
            {
                "proposal_score": score,
                "selection_reason": " ".join(dict.fromkeys(reasons)),
                "review_flags": " | ".join(dict.fromkeys(flags)),
            }
        )

    scored = df.apply(score_row, axis=1)
    df["proposal_score"] = scored["proposal_score"]
    df["selection_reason"] = scored["selection_reason"]
    df["review_flags"] = scored["review_flags"]

    # Very specific target logic. Do not backfill these with broad LA.
    if "sofi" in target_profiles:
        sofi_units = df[df["unit_id_clean"].isin({"40575", "40576"})].copy()
        if not sofi_units.empty:
            return sofi_units.sort_values("proposal_score", ascending=False)

    if "dtla" in target_profiles:
        dtla_units = df[df["unit_id_clean"].isin({"10126", "0103", "103"})].copy()
        if not dtla_units.empty:
            return dtla_units.sort_values("proposal_score", ascending=False)

    is_specific_poi_request = bool(
        target_profiles
        or requirements.get("poi_requirements")
        or any(term in brief_lower for term in ["stadium", "sofi", "dtla", "downtown", "levi"])
    )

    if is_specific_poi_request:
        def is_reasonable_specific_candidate(row: pd.Series) -> bool:
            try:
                score = float(row.get("proposal_score", 0))
            except Exception:
                score = 0

            distance = row.get("distance_to_poi_miles")
            try:
                distance_num = float(distance)
                if distance_num <= 10:
                    return score >= 20
                if distance_num <= 15:
                    return score >= 35
                return False
            except Exception:
                return score >= 60

        plausible = df[df.apply(is_reasonable_specific_candidate, axis=1)].copy()

        if plausible.empty:
            plausible = df.sort_values("proposal_score", ascending=False).head(3).copy()
            plausible["review_flags"] = plausible["review_flags"].fillna("").astype(str)
            plausible["review_flags"] = (
                plausible["review_flags"]
                + " | Fallback review candidate because no strong POI/location matches were found."
            )

        return plausible.sort_values("proposal_score", ascending=False).head(6)

    # Broader market/city requests can return more options, but still need to be plausible.
    plausible = df[df["proposal_score"] >= 25].copy()

    if plausible.empty:
        plausible = df.sort_values("proposal_score", ascending=False).head(5).copy()
        plausible["review_flags"] = plausible["review_flags"].fillna("").astype(str)
        plausible["review_flags"] = (
            plausible["review_flags"]
            + " | Fallback review candidate because no strong matches were found."
        )

    plausible = plausible.sort_values("proposal_score", ascending=False)
    requested_count = int(requirements.get("number_of_units") or 25)
    return plausible.head(requested_count)


# -----------------------------
# Streamlit UI
# -----------------------------

st.set_page_config(page_title="RFP Grid Agent", layout="wide")

# Keeps file uploaders and text areas fresh when starting a new RFP.
if "reset_counter" not in st.session_state:
    st.session_state["reset_counter"] = 0


def start_new_rfp() -> None:
    """
    Clears the current RFP submission and restarts the app with fresh widgets.
    """
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
    req = extract_requirements(
        brief_text,
        use_ai=use_ai,
        groq_model=groq_model,
    )
    req = apply_target_profiles(req, brief_text)
    st.session_state[requirements_key] = json.dumps(req, indent=2)

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

