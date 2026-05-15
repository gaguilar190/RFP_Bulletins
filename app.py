from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import streamlit as st

from distance import add_distance_to_poi, get_primary_poi
from grid_writer import write_output_workbook
from inventory import normalize_inventory
from matcher import match_units
from pricing import add_pricing
from requirements_extractor import (
    coerce_requirements,
    default_requirements,
    extract_requirements,
    extract_text_from_pdf,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR

st.set_page_config(page_title="RFP Grid Agent", layout="wide")
st.title("RFP Grid Agent")
st.caption(
    "Reads your master pricing workbook, matches boards to an RFP brief, "
    "calculates pricing, and exports a filled grid with audit tabs."
)

with st.sidebar:
    st.header("Inputs")
    master_file = st.file_uploader("1. Upload master pricing workbook", type=["xlsx"])
    template_file = st.file_uploader("2. Optional blank agency grid", type=["xlsx", "xlsm"])
    brief_file = st.file_uploader("3. Optional RFP brief", type=["txt", "pdf"])
    use_ai = st.checkbox("Use free cloud AI to read brief", value=True)
    groq_model = st.text_input("Groq model", value="llama-3.1-8b-instant")
    st.divider()
    run_button = st.button("Run RFP Agent", type="primary")

st.subheader("Brief text")
brief_text = ""

if brief_file is not None:
    if brief_file.name.lower().endswith(".pdf"):
        brief_text = extract_text_from_pdf(io.BytesIO(brief_file.getvalue()))
    else:
        brief_text = brief_file.getvalue().decode("utf-8", errors="ignore")

brief_text = st.text_area("Paste or edit RFP brief text", value=brief_text, height=180)

st.subheader("Requirements JSON")

if "requirements_json" not in st.session_state:
    st.session_state["requirements_json"] = json.dumps(default_requirements(), indent=2)

if st.button("Extract requirements from brief"):
    req = extract_requirements(
        brief_text,
        use_ai=use_ai,
        groq_model=groq_model,
    )
    st.session_state["requirements_json"] = json.dumps(req, indent=2)

requirements_json = st.text_area(
    "Review and edit before running. For distance, include POI latitude and longitude in poi_requirements.",
    value=st.session_state["requirements_json"],
    height=360,
)

st.session_state["requirements_json"] = requirements_json

if run_button:
    if master_file is None:
        st.error("Upload your master pricing workbook first.")
        st.stop()

    try:
        requirements = coerce_requirements(json.loads(requirements_json))
    except Exception as exc:
        st.error(f"Requirements JSON is invalid: {exc}")
        st.stop()
 
    brief_lower = brief_text.lower()

    # If markets were extracted but cities were not, use markets as city/location filters too.
    if requirements.get("markets") and not requirements.get("cities"):
        requirements["cities"] = requirements["markets"]

    # If the brief specifically asks for Digital Bulletins, keep it digital only.
    # Do not expand to static bulletins in this case.
    if "digital bulletins" in brief_lower or "digital bulletin" in brief_lower:
        requirements["media_types"] = ["Digital Bulletin"]
    elif "bulletins" in brief_lower or "bulletin" in brief_lower:
        media_types = set(requirements.get("media_types") or [])
        media_types.update(["Static Bulletin", "Digital Bulletin", "Bulletin"])
        requirements["media_types"] = list(media_types)

    # SoFi Stadium fallback. This makes SoFi a hard POI when the brief mentions it.
    # This prevents the app from selecting DTLA boards when the target is SoFi.
    if "sofi stadium" in brief_lower or "sofi" in brief_lower:
        requirements["markets"] = ["Los Angeles"]
        requirements["cities"] = list(
            set((requirements.get("cities") or []) + ["Los Angeles", "Inglewood"])
        )
        requirements["poi_requirements"] = [
            {
                "poi_name": "SoFi Stadium",
                "poi_address": "1001 Stadium Dr, Inglewood, CA",
                "latitude": 33.9535,
                "longitude": -118.3392,
                "priority": 1,
            }
        ]
        requirements["max_distance_miles"] = requirements.get("max_distance_miles") or 15

    # Budget fallback. If the brief says less than $15,000, enforce it as max unit rate.
    if (
        "less than $15,000" in brief_lower
        or "less than 15000" in brief_lower
        or "higher than $15k" in brief_lower
        or "higher than 15k" in brief_lower
        or "$15k" in brief_lower
        or "$15,000" in brief_lower
    ):
        requirements["max_unit_rate"] = 15000
   

    has_geography = bool(
        requirements.get("markets")
        or requirements.get("cities")
        or requirements.get("poi_requirements")
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

     # Soft budget guidance.
    # Do NOT remove units just because they are slightly above budget.
    # Instead, flag them so we can still propose strong units near the target.
    max_unit_rate = requirements.get("max_unit_rate")
    if max_unit_rate:
        try:
            budget_cap = float(max_unit_rate)
            soft_overage_limit = budget_cap * 1.20  # allows up to 20% over budget as review-worthy

            rate_col = None
            if "four_week_media_cost" in inventory.columns:
                rate_col = "four_week_media_cost"
            elif "negotiated_rate_4wk" in inventory.columns:
                rate_col = "negotiated_rate_4wk"

            if rate_col:
                def budget_status(rate):
                    try:
                        rate = float(rate or 0)
                    except Exception:
                        return "Missing rate; review needed"

                    if rate <= budget_cap:
                        return "Within stated budget"
                    if rate <= soft_overage_limit:
                        return "Slightly over budget; review with client"
                    return "Over budget; include only if strategically strong"

                inventory["budget_status"] = inventory[rate_col].apply(budget_status)

                if "review_flags" in inventory.columns:
                    inventory["review_flags"] = inventory["review_flags"].fillna("").astype(str)
                    inventory["review_flags"] = inventory["review_flags"] + " | " + inventory["budget_status"]
                else:
                    inventory["review_flags"] = inventory["budget_status"]

        except Exception:
            pass
       # Soft distance guidance.
    # If the brief gives an exact radius, respect it.
    # If the app inferred a POI like SoFi, do not remove everything too aggressively.
    max_distance = requirements.get("max_distance_miles")
    if max_distance and "distance_to_poi_miles" in inventory.columns:
        try:
            max_distance_float = float(max_distance)
            soft_distance_limit = max_distance_float * 1.50

            inventory["distance_status"] = inventory["distance_to_poi_miles"].apply(
                lambda d: (
                    "Within requested distance"
                    if d is not None and float(d) <= max_distance_float
                    else "Slightly outside requested distance; review"
                    if d is not None and float(d) <= soft_distance_limit
                    else "Outside requested distance; include only if strategically strong"
                )
            )

            if "review_flags" in inventory.columns:
                inventory["review_flags"] = inventory["review_flags"].fillna("").astype(str)
                inventory["review_flags"] = inventory["review_flags"] + " | " + inventory["distance_status"]
            else:
                inventory["review_flags"] = inventory["distance_status"]

        except Exception:
            pass

    with st.spinner("Matching units..."):
        selected, excluded = match_units(inventory, requirements)
   
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
    )

    with st.expander("Missing fields report"):
        st.dataframe(load_result.missing_fields, use_container_width=True)
