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
CONFIG_DIR = BASE_DIR / "config"

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
    use_ollama = st.checkbox("Use local Ollama to read brief", value=False)
    ollama_model = st.text_input("Ollama model", value="llama3.1:8b")
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
        use_ollama=use_ollama,
        ollama_model=ollama_model,
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

    st.download_button(
        "Download filled RFP workbook",
        data=output_path.read_bytes(),
        file_name="filled_rfp_grid.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with st.expander("Missing fields report"):
        st.dataframe(load_result.missing_fields, use_container_width=True)

    
