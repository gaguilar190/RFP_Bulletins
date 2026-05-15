from __future__ import annotations

import math
from typing import Any

import pandas as pd
import requests


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None

        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "null"}:
            return None

        return float(text)
    except Exception:
        return None


def _haversine_miles(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    Straight-line distance between two latitude/longitude points.
    This is useful as a fallback but is not the same as driving distance.
    """
    earth_radius_miles = 3958.8

    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_miles * c


def _osrm_driving_distance_miles(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    timeout_seconds: int = 8,
) -> float | None:
    """
    Free road-network driving distance using OSRM.

    Important:
    - This is closer to Google Maps than straight-line distance.
    - It may still differ from Google Maps because routes, traffic, and map data can vary.
    - If OSRM fails, the app falls back to straight-line distance.
    """
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
        )

        params = {
            "overview": "false",
            "alternatives": "false",
            "steps": "false",
        }

        response = requests.get(url, params=params, timeout=timeout_seconds)
        response.raise_for_status()

        data = response.json()

        routes = data.get("routes") or []
        if not routes:
            return None

        distance_meters = routes[0].get("distance")
        if distance_meters is None:
            return None

        return float(distance_meters) / 1609.344

    except Exception:
        return None


def _get_first_existing_value(row: pd.Series, candidates: list[str]) -> Any:
    for col in candidates:
        if col in row.index:
            value = row.get(col)
            if value is not None and str(value).strip() != "":
                return value
    return None


def get_primary_poi(requirements: dict[str, Any]) -> dict[str, Any] | None:
    """
    Gets the first POI from requirements.
    Expected format:
    {
        "poi_name": "SoFi Stadium",
        "poi_address": "...",
        "latitude": 33.9535,
        "longitude": -118.3392
    }
    """
    pois = requirements.get("poi_requirements") or []

    if not pois:
        return None

    for poi in pois:
        if not isinstance(poi, dict):
            continue

        lat = _safe_float(poi.get("latitude"))
        lon = _safe_float(poi.get("longitude"))

        if lat is not None and lon is not None:
            return poi

    return None


def add_distance_to_poi(
    inventory: pd.DataFrame,
    poi_latitude: Any,
    poi_longitude: Any,
    poi_name: str | None = None,
    poi_address: str | None = None,
) -> pd.DataFrame:
    """
    Adds POI distance columns to inventory.

    Output columns:
    - straight_line_distance_to_poi_miles
    - driving_distance_to_poi_miles
    - distance_to_poi_miles
    - distance_method
    - target_location
    - distance_note

    Main behavior:
    - Tries to calculate driving distance using OSRM.
    - Falls back to straight-line distance if routing fails.
    - Uses driving distance as the primary distance when available.
    """
    df = inventory.copy()

    poi_lat = _safe_float(poi_latitude)
    poi_lon = _safe_float(poi_longitude)

    target_location = poi_name or poi_address or "Target POI"

    if poi_lat is None or poi_lon is None:
        df["straight_line_distance_to_poi_miles"] = None
        df["driving_distance_to_poi_miles"] = None
        df["distance_to_poi_miles"] = None
        df["distance_method"] = "none"
        df["target_location"] = target_location
        df["distance_note"] = "No valid POI latitude/longitude provided."
        return df

    straight_line_distances = []
    driving_distances = []
    final_distances = []
    methods = []
    notes = []

    route_cache: dict[tuple[float, float, float, float], float | None] = {}

    for _, row in df.iterrows():
        unit_lat = _safe_float(
            _get_first_existing_value(
                row,
                ["latitude", "lat", "Latitude", "LATITUDE"],
            )
        )

        unit_lon = _safe_float(
            _get_first_existing_value(
                row,
                ["longitude", "lon", "lng", "Longitude", "LONGITUDE"],
            )
        )

        if unit_lat is None or unit_lon is None:
            straight_line_distances.append(None)
            driving_distances.append(None)
            final_distances.append(None)
            methods.append("missing_coordinates")
            notes.append("Missing unit latitude/longitude.")
            continue

        straight_line = _haversine_miles(unit_lat, unit_lon, poi_lat, poi_lon)

        cache_key = (
            round(unit_lat, 6),
            round(unit_lon, 6),
            round(poi_lat, 6),
            round(poi_lon, 6),
        )

        if cache_key in route_cache:
            driving_distance = route_cache[cache_key]
        else:
            driving_distance = _osrm_driving_distance_miles(
                origin_lat=unit_lat,
                origin_lon=unit_lon,
                dest_lat=poi_lat,
                dest_lon=poi_lon,
            )
            route_cache[cache_key] = driving_distance

        straight_line_distances.append(round(straight_line, 2))

        if driving_distance is not None:
            driving_distances.append(round(driving_distance, 2))
            final_distances.append(round(driving_distance, 2))
            methods.append("driving_distance_osrm")
            notes.append(
                f"Driving distance to {target_location} calculated using road routing."
            )
        else:
            driving_distances.append(None)
            final_distances.append(round(straight_line, 2))
            methods.append("straight_line_fallback")
            notes.append(
                f"Road routing unavailable. Straight-line distance to {target_location} used."
            )

    df["straight_line_distance_to_poi_miles"] = straight_line_distances
    df["driving_distance_to_poi_miles"] = driving_distances
    df["distance_to_poi_miles"] = final_distances
    df["distance_method"] = methods
    df["target_location"] = target_location
    df["distance_note"] = notes

    return df
