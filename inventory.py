from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import clean_header, is_blank, load_json, normalize_key, safe_str, to_number


STANDARD_FIELDS = [
    "media_owner",
    "unit_id",
    "geopath_frame_id",
    "target_audience_index",
    "freeway_street",
    "description",
    "rate_card_4wk",
    "negotiated_rate_4wk",
    "four_week_media_cost",
    "location",
    "city",
    "market",
    "state",
    "zip_code",
    "line",
    "facing",
    "reads",
    "size",
    "reach_net",
    "target_in_market_rating_points",
    "in_market_reach",
    "in_market_frequency",
    "a18_4wk_reach_percent",
    "a18_4wk_freq",
    "geopath_a18_weekly_impressions",
    "geopath_a18_4wk_impressions",
    "latitude",
    "longitude",
    "comments",
    "media_type",
    "digital_or_static",
    "rate_basis",
    "production_cost",
    "install_cost",
    "production_cost_final",
    "install_cost_final",
    "taxes",
    "is_production_forced",
    "illuminated",
    "availability",
    "availability_start",
    "availability_end",
    "status",
    "number_of_units",
    "directional_tags",
    "qr_code_allowed",
    "spot_length_seconds",
    "spots_per_loop",
    "creative_specs",
    "target_location",
]


@dataclass
class InventoryLoadResult:
    inventory: pd.DataFrame
    missing_fields: pd.DataFrame
    sheet_name: str
    raw_columns: list[str]


def _build_alias_lookup(column_aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for standard, aliases in column_aliases.items():
        lookup[normalize_key(standard)] = standard
        for alias in aliases:
            key = normalize_key(alias)
            if key not in lookup:
                lookup[key] = standard
    return lookup


def choose_inventory_sheet(excel_file: str | Path | Any) -> str:
    xls = pd.ExcelFile(excel_file)
    for sheet in xls.sheet_names:
        if "master" in sheet.lower() and "list" in sheet.lower():
            return sheet
    for sheet in xls.sheet_names:
        if "master" in sheet.lower() or "inventory" in sheet.lower():
            return sheet
    return xls.sheet_names[0]


def read_raw_inventory(excel_file: str | Path | Any, sheet_name: str | None = None) -> tuple[pd.DataFrame, str]:
    sheet = sheet_name or choose_inventory_sheet(excel_file)
    # Your current master pricing workbook uses row 2 as the header row on the Master List sheet.
    header_row = 1 if "master" in sheet.lower() else 0
    df = pd.read_excel(excel_file, sheet_name=sheet, header=header_row, engine="openpyxl")
    df.columns = [clean_header(c) if clean_header(c) else f"unnamed_{i}" for i, c in enumerate(df.columns)]
    return df, sheet


def _looks_like_unit_id(value: Any) -> bool:
    """Keep real board IDs while dropping footnotes/section labels from the master workbook."""
    unit = safe_str(value)
    if not unit:
        return False
    if unit.lower() in {"nan", "none", "digital", "digital bulletins", "digital bulletin", "static", "static bulletin"}:
        return False
    # Real IDs in the current workbook start with a digit: 0103, 01134-SF, 99129-Fresno, etc.
    if not re.match(r"^\d", unit):
        return False
    if len(re.findall(r"\d", unit)) < 2:
        return False
    # Avoid long explanatory lines that accidentally contain numbers.
    if len(unit) > 25:
        return False
    return True


def normalize_inventory(
    excel_file: str | Path | Any,
    column_aliases_path: str | Path = "config/column_aliases.json",
    sheet_name: str | None = None,
) -> InventoryLoadResult:
    aliases = load_json(column_aliases_path)
    alias_lookup = _build_alias_lookup(aliases)
    raw, sheet = read_raw_inventory(excel_file, sheet_name)

    rename_map: dict[str, str] = {}
    seen: set[str] = set()
    for col in raw.columns:
        standard = alias_lookup.get(normalize_key(col))
        if standard and standard not in seen:
            rename_map[col] = standard
            seen.add(standard)

    df = raw.rename(columns=rename_map).copy()

    # Add missing standard columns with blanks so downstream code can rely on stable names.
    for field in STANDARD_FIELDS:
        if field not in df.columns:
            df[field] = None

    # Track the media type section. In your current workbook, rows before the 'Digital' marker are static;
    # rows after it are digital. If you later add an explicit media_type column, it will override this.
    current_media_type = "Static Bulletin"
    media_types: list[str | None] = []
    keep_rows: list[bool] = []

    for _, row in df.iterrows():
        unit_raw = safe_str(row.get("unit_id"))
        free_raw = safe_str(row.get("freeway_street"))
        desc_raw = safe_str(row.get("description"))
        row_text = " ".join([safe_str(v) for v in row.tolist()]).lower()

        is_section = False
        if unit_raw.lower() in {"digital", "digital bulletins", "digital bulletin"} or free_raw.lower().strip() == "digital":
            current_media_type = "Digital Bulletin"
            is_section = True
        elif unit_raw.lower() in {"static", "static displays", "static bulletins", "static bulletin"}:
            current_media_type = "Static Bulletin"
            is_section = True

        # Footnotes in this workbook start with '*' or include explanatory language and should not be treated as inventory rows.
        is_footnote = (
            unit_raw.startswith("*")
            or "all rates quoted" in row_text
            or "source:" in row_text
            or "bulletin displays will deliver" in row_text
            or "extensions:" in row_text
            or unit_raw.lower().strip() == "geopath"
        )
        valid_unit = _looks_like_unit_id(unit_raw) and not is_section and not is_footnote

        media_types.append(current_media_type if valid_unit else None)
        keep_rows.append(valid_unit)

    df["media_type"] = [m if is_blank(existing) else existing for m, existing in zip(media_types, df["media_type"])]
    df = df.loc[keep_rows].copy()

    # Clean numeric fields.
    numeric_fields = [
        "geopath_frame_id",
        "target_audience_index",
        "rate_card_4wk",
        "negotiated_rate_4wk",
        "four_week_media_cost",
        "zip_code",
        "reach_net",
        "target_in_market_rating_points",
        "in_market_reach",
        "in_market_frequency",
        "a18_4wk_reach_percent",
        "a18_4wk_freq",
        "geopath_a18_weekly_impressions",
        "geopath_a18_4wk_impressions",
        "latitude",
        "longitude",
        "production_cost",
        "install_cost",
        "production_cost_final",
        "install_cost_final",
        "taxes",
        "spot_length_seconds",
        "spots_per_loop",
        "number_of_units",
    ]
    for field in numeric_fields:
        df[field] = df[field].map(to_number)

    # Clean strings.
    string_fields = [
        "media_owner",
        "unit_id",
        "freeway_street",
        "description",
        "location",
        "city",
        "market",
        "state",
        "line",
        "facing",
        "reads",
        "size",
        "comments",
        "media_type",
        "digital_or_static",
        "rate_basis",
        "status",
        "directional_tags",
        "qr_code_allowed",
        "creative_specs",
        "availability",
        "is_production_forced",
        "illuminated",
        "target_location",
    ]
    for field in string_fields:
        df[field] = df[field].map(safe_str)

    # Derive helpful fields.
    df["digital_or_static"] = df["media_type"].map(lambda x: "Digital" if "digital" in safe_str(x).lower() else "Static")
    df["rate_basis"] = df["rate_basis"].replace("", "4_weeks")
    df["market"] = df.apply(lambda r: r["market"] or r["city"], axis=1)
    df["state"] = df["state"].replace("", "CA")
    # Hard business rule from Cori: Vendor is always Bulletin Displays.
    df["media_owner"] = "Bulletin Displays"
    df["number_of_units"] = df["number_of_units"].map(lambda x: 1 if pd.isna(x) else x)
    df["illuminated"] = "Yes"
    df["is_production_forced"] = "No"
    df["taxes"] = 0
    # Hard business rule: any 4-week media cost field should show the Negotiated Rate.
    df["four_week_media_cost"] = df["negotiated_rate_4wk"]
    # Reach/frequency are normalized later in pricing too, but calculate early for output-only runs.
    df["a18_4wk_reach_percent"] = df["in_market_reach"].map(lambda x: x * 4 if not pd.isna(x) else None)
    df["a18_4wk_freq"] = df["in_market_frequency"].map(lambda x: x * 4 if not pd.isna(x) else None)

    def infer_status(row: pd.Series) -> str:
        status = safe_str(row.get("status"))
        full_row_text = " ".join(safe_str(v) for v in row.tolist()).lower()
        if "taken down" in full_row_text:
            return "Taken Down"
        if status:
            return status
        if is_blank(row.get("negotiated_rate_4wk")) and is_blank(row.get("rate_card_4wk")):
            return "Needs Review"
        return "Active"

    df["status"] = df.apply(infer_status, axis=1)

    # Normalize unit_id to text and keep leading zeroes from the source workbook.
    df["unit_id"] = df["unit_id"].map(safe_str)

    missing_report_rows = []
    for field in STANDARD_FIELDS:
        present = field in df.columns
        nonblank = int(df[field].notna().sum()) if present else 0
        blank_count = int(df[field].isna().sum()) if present else len(df)
        status = "Found" if present and nonblank > 0 else "Missing or blank"
        missing_report_rows.append(
            {
                "standard_field": field,
                "status": status,
                "nonblank_rows": nonblank,
                "blank_rows": blank_count,
            }
        )
    missing_fields = pd.DataFrame(missing_report_rows)

    # Order columns so the important ones are first, then preserve the extras.
    ordered = [c for c in STANDARD_FIELDS if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    df = df[ordered + extras]

    return InventoryLoadResult(
        inventory=df.reset_index(drop=True),
        missing_fields=missing_fields,
        sheet_name=sheet,
        raw_columns=list(raw.columns),
    )
