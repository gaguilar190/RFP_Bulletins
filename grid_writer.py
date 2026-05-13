from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from rapidfuzz import fuzz, process

from .utils import is_blank, load_json, normalize_key, safe_str


DEFAULT_OUTPUT_FIELDS = [
    "media_owner",
    "market",
    "city",
    "state",
    "media_type",
    "number_of_units",
    "geopath_frame_id",
    "unit_id",
    "description",
    "facing",
    "size",
    "availability",
    "geopath_a18_weekly_impressions",
    "a18_4wk_reach_percent",
    "a18_4wk_freq",
    "illuminated",
    "rate_card_4wk",
    "four_week_media_cost",
    "install_cost_final",
    "production_cost_final",
    "is_production_forced",
    "taxes",
    "target_location",
    "distance_to_poi_miles",
    "comments",
    "latitude",
    "longitude",
    "spot_length_seconds",
    "spots_per_loop",
    "qr_code_allowed",
    "contracted_media_cost",
    "total_campaign_cost",
    "cpm",
    "selection_reason",
    "review_flags",
    "pricing_note",
]

FIELD_NUMBER_FORMATS = {
    "a18_4wk_reach_percent": "0.00%",
    "a18_4wk_freq": "0.00",
    "rate_card_4wk": "\"$\"#,##0",
    "four_week_media_cost": "\"$\"#,##0",
    "contracted_media_cost": "\"$\"#,##0",
    "total_campaign_cost": "\"$\"#,##0",
    "install_cost_final": "\"$\"#,##0",
    "production_cost_final": "\"$\"#,##0",
    "taxes": "\"$\"#,##0",
    "distance_to_poi_miles": "0.00",
    "latitude": "0.00000",
    "longitude": "0.00000",
    "cpm": "\"$\"0.00",
}


def _flatten_aliases(column_aliases: dict[str, list[str]]) -> dict[str, str]:
    alias_lookup = {}
    for standard, aliases in column_aliases.items():
        alias_lookup[normalize_key(standard)] = standard
        for alias in aliases:
            alias_lookup[normalize_key(alias)] = standard
    return alias_lookup


def _friendly_sheet_name(name: str) -> str:
    bad_chars = ["[", "]", "*", "?", "/", "\\", ":"]
    cleaned = name
    for ch in bad_chars:
        cleaned = cleaned.replace(ch, " ")
    return cleaned[:31]


def _safe_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    return value


def find_header_row_and_mapping(
    ws: Worksheet,
    column_aliases: dict[str, list[str]],
    max_scan_rows: int = 25,
) -> tuple[int | None, dict[int, str], list[str]]:
    alias_lookup = _flatten_aliases(column_aliases)
    alias_keys = list(alias_lookup.keys())
    best_row = None
    best_score = 0
    best_mapping: dict[int, str] = {}
    unmapped_headers: list[str] = []

    for row_idx in range(1, min(max_scan_rows, ws.max_row) + 1):
        mapping: dict[int, str] = {}
        nonblank_headers: list[str] = []
        for col_idx in range(1, ws.max_column + 1):
            value = ws.cell(row_idx, col_idx).value
            if is_blank(value):
                continue
            header = safe_str(value)
            nonblank_headers.append(header)
            key = normalize_key(header)
            standard = alias_lookup.get(key)
            if not standard and alias_keys:
                match = process.extractOne(key, alias_keys, scorer=fuzz.ratio)
                if match and match[1] >= 90:
                    standard = alias_lookup[match[0]]
            if standard:
                mapping[col_idx] = standard
        score = len(mapping)
        if score > best_score:
            best_score = score
            best_row = row_idx
            best_mapping = mapping
            unmapped_headers = [h for h in nonblank_headers if normalize_key(h) not in alias_lookup]

    if best_score == 0:
        return None, {}, []
    return best_row, best_mapping, unmapped_headers


def _write_dataframe(ws: Worksheet, df: pd.DataFrame, start_row: int = 1, start_col: int = 1) -> None:
    for c_idx, col in enumerate(df.columns, start=start_col):
        ws.cell(start_row, c_idx, str(col))
    for r_offset, (_, row) in enumerate(df.iterrows(), start=1):
        for c_idx, col in enumerate(df.columns, start=start_col):
            cell = ws.cell(start_row + r_offset, c_idx, _safe_value(row[col]))
            if col in FIELD_NUMBER_FORMATS:
                cell.number_format = FIELD_NUMBER_FORMATS[col]
    # Basic widths.
    for c_idx, col in enumerate(df.columns, start=start_col):
        values = [str(col)] + [safe_str(v) for v in df[col].head(50).tolist()]
        width = min(max(len(v) for v in values) + 2, 45)
        ws.column_dimensions[get_column_letter(c_idx)].width = width
    ws.freeze_panes = ws.cell(start_row + 1, start_col)


def _add_or_replace_sheet(wb: Workbook, name: str, df: pd.DataFrame) -> None:
    sheet_name = _friendly_sheet_name(name)
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    if df is None or df.empty:
        ws.cell(1, 1, "No rows")
    else:
        _write_dataframe(ws, df)


def write_output_workbook(
    selected: pd.DataFrame,
    excluded: pd.DataFrame,
    requirements: dict[str, Any],
    missing_fields: pd.DataFrame,
    output_path: str | Path,
    template_path: str | Path | None = None,
    column_aliases_path: str | Path = "config/column_aliases.json",
) -> Path:
    column_aliases = load_json(column_aliases_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    unmapped_df = pd.DataFrame()
    if template_path:
        wb = load_workbook(template_path)
        ws = wb.active
        header_row, col_mapping, unmapped_headers = find_header_row_and_mapping(ws, column_aliases)
        if header_row and col_mapping:
            data_start_row = header_row + 1
            style_row = data_start_row
            # Clear old rows in mapped columns under the header, without destroying the whole template.
            for r in range(data_start_row, ws.max_row + 1):
                for c in col_mapping:
                    ws.cell(r, c).value = None

            for r_offset, (_, unit) in enumerate(selected.iterrows()):
                target_row = data_start_row + r_offset
                for col_idx, standard_field in col_mapping.items():
                    src = ws.cell(style_row, col_idx)
                    dst = ws.cell(target_row, col_idx)
                    if target_row != style_row:
                        dst._style = copy.copy(src._style)
                        if src.number_format:
                            dst.number_format = src.number_format
                        if src.alignment:
                            dst.alignment = copy.copy(src.alignment)
                    if standard_field in selected.columns:
                        dst.value = _safe_value(unit.get(standard_field))
                        if standard_field in FIELD_NUMBER_FORMATS:
                            dst.number_format = FIELD_NUMBER_FORMATS[standard_field]
            unmapped_df = pd.DataFrame({"unmapped_template_headers": unmapped_headers})
        else:
            # No recognizable grid headers, so create a new fill sheet.
            ws = wb.create_sheet("Filled RFP Grid")
            fields = [c for c in DEFAULT_OUTPUT_FIELDS if c in selected.columns]
            _write_dataframe(ws, selected[fields])
            unmapped_df = pd.DataFrame({"note": ["No recognizable header row found in uploaded template."]})
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Filled RFP Grid"
        fields = [c for c in DEFAULT_OUTPUT_FIELDS if c in selected.columns]
        if not fields:
            fields = list(selected.columns)
        _write_dataframe(ws, selected[fields])

    # Audit sheets.
    req_df = pd.DataFrame([requirements])
    _add_or_replace_sheet(wb, "Requirement Checklist", req_df)

    selected_review_cols = [c for c in [
        "unit_id", "score", "selection_reason", "review_flags", "matched_requirements",
        "distance_to_poi_miles", "contracted_media_cost", "total_campaign_cost", "cpm"
    ] if c in selected.columns]
    _add_or_replace_sheet(wb, "Selected Units Review", selected[selected_review_cols] if selected_review_cols else selected)

    pricing_cols = [c for c in [
        "unit_id", "availability", "four_week_media_cost", "base_4wk_rate", "cost_source",
        "rate_increase_percent", "discount_percent", "adjusted_4wk_rate", "contracted_weeks",
        "contracted_media_cost", "production_cost_final", "install_cost_final", "is_production_forced",
        "taxes", "total_campaign_cost", "a18_4wk_reach_percent", "a18_4wk_freq",
        "contracted_impressions", "cpm", "pricing_note", "pricing_review_flags"
    ] if c in selected.columns]
    _add_or_replace_sheet(wb, "Pricing Audit", selected[pricing_cols] if pricing_cols else pd.DataFrame())

    distance_cols = [c for c in ["unit_id", "description", "latitude", "longitude", "distance_to_poi_miles", "distance_note"] if c in selected.columns]
    _add_or_replace_sheet(wb, "Distance Audit", selected[distance_cols] if distance_cols else pd.DataFrame())

    excluded_cols = [c for c in ["unit_id", "description", "city", "media_type", "excluded_reason"] if c in excluded.columns]
    _add_or_replace_sheet(wb, "Excluded Units", excluded[excluded_cols].head(500) if excluded_cols else excluded.head(500))

    _add_or_replace_sheet(wb, "Missing Fields Report", missing_fields)
    _add_or_replace_sheet(wb, "Unmapped Columns", unmapped_df)

    wb.save(output_path)
    return output_path
