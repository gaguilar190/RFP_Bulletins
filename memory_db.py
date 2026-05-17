from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from supabase import create_client


MEMORY_COLUMNS = [
    "created_at",
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


def get_supabase_client():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")

    if not url or not key:
        return None

    return create_client(url, key)


def load_planner_memory_from_db() -> pd.DataFrame:
    client = get_supabase_client()

    if client is None:
        return pd.DataFrame(columns=MEMORY_COLUMNS)

    try:
        response = (
            client.table("planner_memory")
            .select("*")
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )

        rows = response.data or []
        memory = pd.DataFrame(rows)

        for col in MEMORY_COLUMNS:
            if col not in memory.columns:
                memory[col] = ""

        return memory

    except Exception as exc:
        st.warning(f"Planner memory could not be loaded: {exc}")
        return pd.DataFrame(columns=MEMORY_COLUMNS)


def save_planner_memory_to_db(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False

    client = get_supabase_client()

    if client is None:
        st.error("Supabase is not connected. Add SUPABASE_URL and SUPABASE_KEY to Streamlit Secrets.")
        return False

    clean_rows = []

    for row in rows:
        clean_rows.append(
            {
                "advertiser": row.get("advertiser", ""),
                "unit_id": row.get("unit_id", ""),
                "action": row.get("action", ""),
                "market": row.get("market", ""),
                "city": row.get("city", ""),
                "media_type": row.get("media_type", ""),
                "recommendation_tier": row.get("recommendation_tier", ""),
                "proposal_role": row.get("proposal_role", ""),
                "proposal_score": row.get("proposal_score", None),
                "rfp_markets": row.get("rfp_markets", ""),
                "rfp_cities": row.get("rfp_cities", ""),
                "rfp_media_types": row.get("rfp_media_types", ""),
                "rfp_tags": row.get("rfp_tags", ""),
                "notes": row.get("notes", ""),
            }
        )

    try:
        client.table("planner_memory").insert(clean_rows).execute()
        return True
    except Exception as exc:
        st.error(f"Planner memory could not be saved: {exc}")
        return False
