from __future__ import annotations

import math
from typing import Any

import pandas as pd

from utils import is_blank, safe_str, to_number


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_miles * c


def _poi_label(poi_name: Any = None, poi_address: Any = None) -> str:
    name = safe_str(poi_name)
    address = safe_str(poi_address)
    if name and address and name.lower() not in address.lower():
        return f"{name} - {address}"
    return name or address


def add_distance_to_poi(
    inventory: pd.DataFrame,
    poi_latitude: Any | None,
    poi_longitude: Any | None,
    output_col: str = "distance_to_poi_miles",
    poi_name: Any | None = None,
    poi_address: Any | None = None,
) -> pd.DataFrame:
    df = inventory.copy()
    poi_lat = to_number(poi_latitude)
    poi_lon = to_number(poi_longitude)
    df["target_location"] = _poi_label(poi_name, poi_address)

    if poi_lat is None or poi_lon is None:
        df[output_col] = None
        df["distance_note"] = "No POI latitude/longitude provided."
        return df

    distances = []
    notes = []
    for _, row in df.iterrows():
        lat = to_number(row.get("latitude"))
        lon = to_number(row.get("longitude"))
        if lat is None or lon is None:
            distances.append(None)
            notes.append("Missing unit latitude/longitude.")
        else:
            dist = haversine_miles(lat, lon, poi_lat, poi_lon)
            distances.append(round(dist, 2))
            notes.append("Straight-line distance calculated from unit lat/long to POI lat/long.")

    df[output_col] = distances
    df["distance_note"] = notes
    return df


def get_primary_poi(requirements: dict[str, Any]) -> dict[str, Any] | None:
    pois = requirements.get("poi_requirements") or []
    if not pois:
        return None
    if isinstance(pois[0], dict):
        return pois[0]
    return None
