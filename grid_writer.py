from __future__ import annotations

from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook, load_workbook


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _to_number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "").strip()
            if value == "":
                return default
        return float(value)
    except Exception:
        return default


def _format_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%-m/%-d/%y")
    text = str(value)
    try:
        return datetime.fromisoformat(text).strftime("%-m/%-d/%y")
    except Exception:
        return text


def _campaign_date_range(requirements: dict[str, Any]) -> str:
    start = _format_date(requirements.get("campaign_start"))
    end = _format_date(requirements.get("campaign_end"))
    if start and end:
        return f"{start} - {end}"
    return ""


def _normalize_header(value: Any) -> str:
    text = _safe_str(value).lower()
    replacements = {
        "#": " number ",
        "+": " plus ",
        "&": " and ",
        "/": " ",
        "-": " ",
        "_": " ",
        "(": " ",
        ")": " ",
        "%": " percent ",
        "?": " ",
        ".": " ",
        ",": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def _first_existing(row: pd.Series, candidates: list[str], default: Any = "") -> Any:
    for col in candidates:
        if col in row.index:
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                if str(val).strip() != "":
                    return val
    return default


def _money(value: Any) -> float:
    return round(_to_number(value, 0.0), 2)


def _int_or_blank(value: Any) -> Any:
    num = _to_number(value, None)
    if num is None:
        return ""
    return int(round(num))


def _get_unit_id(row: pd.Series) -> str:
    return _safe_str(
        _first_existing(
            row,
            ["unit_id", "unit", "vendor_inventory_number", "vendor_inventory", "panel_id"],
        )
    )


def _is_special_unit_01134(row: pd.Series) -> bool:
    unit_id = _get_unit_id(row).replace(" ", "").upper()
    return unit_id in {"01134", "01134-SF", "1134", "1134-SF"}


def _install_cost(row: pd.Series) -> int:
    return 1600 if _is_special_unit_01134(row) else 850


def _production_cost(row: pd.Series) -> int:
    return 950 if _is_special_unit_01134(row) else 750


def _target_location_from_requirements(requirements: dict[str, Any]) -> str:
    pois = requirements.get("poi_requirements") or []
    if pois and isinstance(pois, list):
        poi = pois[0] or {}
        return _safe_str(
            poi.get("poi_name")
            or poi.get("poi_address")
            or ""
        )
    return ""


def _contracted_periods(row: pd.Series) -> Any:
    value = _first_existing(row, ["number_of_4wk_periods", "contracted_4wk_periods"], "")
    if value != "":
        return value

    contracted_weeks = _to_number(_first_existing(row, ["contracted_weeks"], 0), 0)
    if contracted_weeks:
        return round(contracted_weeks / 4, 2)

    return ""


def _value_for_template_header(header: str, row: pd.Series, requirements: dict[str, Any]) -> Any:
    h = _normalize_header(header)

    campaign_start = _format_date(requirements.get("campaign_start"))
    campaign_end = _format_date(requirements.get("campaign_end"))

    # Fixed business rules
    if h in {"vendor", "media owner"}:
        return "Bulletin Displays"

    if h in {"availability"}:
        return _campaign_date_range(requirements)

    if h in {"illumination", "illuminated", "illuminated"}:
        return "Yes"

    if h in {
        "forced vendor production y n",
        "is production forced",
        "forced vendor production",
        "production forced",
    }:
        return "No"

    if h in {"taxes", "tax"}:
        return 0

    # Common RFP template fields
    if h in {"dma", "market", "client market name"}:
        return _first_existing(row, ["market", "city"], "")

    if h in {"format", "media type"}:
        return _first_existing(row, ["media_type", "format"], "")

    if h in {"vendor inventory number", "vendor inventory", "unit number", "unit"}:
        return _get_unit_id(row)

    if h in {"geopath id number", "geopath id", "geopath frame id"}:
        return _first_existing(row, ["geopath_frame_id", "geopath_id"], "")

    if h in {"location description", "description", "location"}:
        return _first_existing(row, ["description", "location"], "")

    if h in {"face", "facing"}:
        return _first_existing(row, ["facing", "face"], "")

    if h == "latitude":
        return _first_existing(row, ["latitude"], "")

    if h == "longitude":
        return _first_existing(row, ["longitude"], "")

    if h in {"zip code", "zipcode", "zip"}:
        return _first_existing(row, ["zip_code", "zipcode", "zip"], "")

    if h in {"size hxw", "size", "dimensions"}:
        return _first_existing(row, ["size"], "")

    if h in {"number of units", "of units"}:
        return 1

    # Digital fields
    if h in {"digital spot length", "spot length"}:
        return _first_existing(row, ["spot_length", "spot_length_seconds"], "")

    if h in {"number of spots per loop", "of spots per loop", "spots per loop"}:
        return _first_existing(row, ["spots_per_loop", "number_of_spots", "spots"], "")

    if h == "loop length seconds":
        return _first_existing(row, ["loop_length_seconds"], "")

    if h == "digital display type":
        media_type = _safe_str(_first_existing(row, ["media_type"], ""))
        if "digital" in media_type.lower():
            return "Digital"
        return ""

    if h == "pixel size h x w":
        return _first_existing(row, ["pixel_size", "pixel_size_hxw"], "")

    # Impressions
    if h in {
        "1 week a18 plus geopath impressions",
        "1 week a18 geopath impressions",
        "18 plus impressions week",
        "a18 plus weekly impressions",
        "weekly impressions",
    }:
        return _int_or_blank(
            _first_existing(
                row,
                [
                    "geopath_a18_weekly_impressions",
                    "a18_weekly_impressions",
                    "weekly_impressions",
                ],
                "",
            )
        )

    if h in {
        "4 week a18 plus geopath impressions",
        "4 week a18 geopath impressions",
        "18 plus impressions cycle",
        "18 plus 4 week impressions",
        "a18 plus 4 wk impressions",
    }:
        return _int_or_blank(
            _first_existing(
                row,
                [
                    "geopath_a18_4wk_impressions",
                    "a18_4wk_impressions",
                    "contracted_impressions",
                ],
                "",
            )
        )

    if h in {"18 plus total impressions", "total impressions"}:
        return _int_or_blank(
            _first_existing(
                row,
                [
                    "contracted_impressions",
                    "geopath_a18_4wk_impressions",
                    "a18_4wk_impressions",
                ],
                "",
            )
        )

    # Dates
    if h == "start date":
        return campaign_start

    if h == "end date":
        return campaign_end

    # Pricing
    if h in {"4 week rate card", "rate card cycle", "rate card"}:
        return _money(_first_existing(row, ["rate_card_4wk", "rate_card"], 0))

    # Your rule: 4 week media cost should always be negotiated rate
    if h in {
        "4 week rate",
        "4 week media cost",
        "4 week net media cost",
        "net media cost cycle",
    }:
        return _money(
            _first_existing(
                row,
                [
                    "four_week_media_cost",
                    "negotiated_rate_4wk",
                    "negotiated_rate",
                ],
                0,
            )
        )

    if h in {"number of 4 wk periods", "number of 4 week periods", "number of cycles", "of cycles"}:
        return _contracted_periods(row)

    if h == "cycle type":
        return "4 Week"

    if h in {"total spaces cost net", "net campaign media cost"}:
        return _money(_first_existing(row, ["contracted_media_cost", "total_media_cost"], 0))

    if h in {"total media install production net", "total campaign cost"}:
        total = _to_number(_first_existing(row, ["contracted_media_cost", "total_media_cost"], 0), 0)
        total += _install_cost(row)
        total += _production_cost(row)
        return _money(total)

    if h == "cpm":
        return _money(_first_existing(row, ["cpm"], 0))

    # Install and production rules
    if h in {
        "installation cost",
        "install cost",
        "initial installation cost net",
        "installation cost final",
        "initial install cost",
    }:
        return _install_cost(row)

    if h in {
        "production cost",
        "production cost net",
        "production cost final",
        "total to produce",
    }:
        return _production_cost(row)

    if h == "forced vendor production cost":
        return 0

    # Other production fields
    if h in {
        "number of copy changes included at no cost after initial install",
        "of copy changes included at no cost after initial install",
    }:
        return 0

    if h in {"number of paid installs", "of paid installs"}:
        return 1

    if h == "total postings":
        return 1

    if h == "print qty per posting":
        return 1

    if h == "production shipping address":
        return ""

    if h == "recommended material":
        return ""

    if h == "creative approval required":
        return ""

    if h == "creative due date":
        return ""

    if h == "production contact":
        return ""

    if h == "production rep name":
        return ""

    # Target/location/distance fields
    if h in {"store covered", "target area location"}:
        return _first_existing(row, ["target_location"], _target_location_from_requirements(requirements))

    if h in {"approximate distance mi", "distance from target miles", "distance to poi miles"}:
        value = _first_existing(row, ["distance_to_poi_miles"], "")
        if value == "":
            return ""
        return round(_to_number(value, 0), 2)

    # Notes
    if h in {"notes", "comments", "comment", "summary"}:
        return _first_existing(
            row,
            ["selection_reason", "review_flags", "comments", "description"],
            "",
        )

    # Misc fields
    if h == "popfacts persons 18 plus yrs 1wk total impressions":
        return _int_or_blank(
            _first_existing(
                row,
                [
                    "geopath_a18_weekly_impressions",
                    "a18_weekly_impressions",
                    "weekly_impressions",
                ],
                "",
            )
        )

    if h == "popfacts persons 18 plus yrs 1wk trp":
        return _first_existing(row, ["popfacts_a18_1wk_trp", "trp"], "")

    if h == "offer id":
        return _first_existing(row, ["offer_id"], "")

    # Fallback direct column match
    for col in row.index:
        if _normalize_header(col) == h:
            return row.get(col)

    return ""


def _find_header_row(ws) -> int:
    best_row = 1
    best_score = 0

    expected_headers = {
        "dma",
        "vendor",
        "media type",
        "unit",
        "geopath id",
        "location description",
        "latitude",
        "longitude",
        "4 week rate card",
        "4 week rate",
        "initial installation cost net",
        "production cost net",
        "notes",
    }

    max_scan = min(ws.max_row, 20)

    for row_idx in range(1, max_scan + 1):
        values = [_normalize_header(cell.value) for cell in ws[row_idx]]
        score = sum(1 for value in values if value in expected_headers)
        if score > best_score:
            best_score = score
            best_row = row_idx

    return best_row


def _copy_row_style(ws, source_row: int, target_row: int) -> None:
    for col_idx in range(1, ws.max_column + 1):
        source = ws.cell(source_row, col_idx)
        target = ws.cell(target_row, col_idx)

        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)
        if source.border:
            target.border = copy(source.border)
        if source.fill:
            target.fill = copy(source.fill)
        if source.font:
            target.font = copy(source.font)


def _clear_template_body(ws, header_row: int, rows_to_clear: int) -> None:
    start_row = header_row + 1
    end_row = max(ws.max_row, start_row + rows_to_clear + 5)

    for row_idx in range(start_row, end_row + 1):
        for col_idx in range(1, ws.max_column + 1):
            ws.cell(row_idx, col_idx).value = None


def _write_selected_to_template(ws, selected: pd.DataFrame, requirements: dict[str, Any]) -> None:
    header_row = _find_header_row(ws)
    headers = [ws.cell(header_row, col_idx).value for col_idx in range(1, ws.max_column + 1)]
    data_start_row = header_row + 1

    _clear_template_body(ws, header_row, len(selected))

    style_source_row = data_start_row

    for df_idx, (_, selected_row) in enumerate(selected.iterrows()):
        excel_row = data_start_row + df_idx

        if excel_row != style_source_row:
            _copy_row_style(ws, style_source_row, excel_row)

        for col_idx, header in enumerate(headers, start=1):
            if header is None or str(header).strip() == "":
                continue

            value = _value_for_template_header(str(header), selected_row, requirements)
            ws.cell(excel_row, col_idx).value = value


def _write_generic_output(wb, selected: pd.DataFrame) -> None:
    ws = wb.active
    ws.title = "Filled RFP Grid"

    if selected.empty:
        ws.cell(1, 1).value = "No selected units."
        return

    headers = list(selected.columns)
    ws.append(headers)

    for _, row in selected.iterrows():
        ws.append([row.get(col) for col in headers])

    ws.freeze_panes = "A2"


def write_output_workbook(
    selected: pd.DataFrame,
    excluded: pd.DataFrame,
    requirements: dict[str, Any],
    missing_fields: pd.DataFrame,
    output_path: str | Path,
    template_path: str | Path | None = None,
    column_aliases_path: str | Path | None = None,
) -> None:
    output_path = Path(output_path)

    if template_path:
        template_path = Path(template_path)
        keep_vba = template_path.suffix.lower() == ".xlsm"
        wb = load_workbook(template_path, keep_vba=keep_vba)
        ws = wb[wb.sheetnames[0]]
        _write_selected_to_template(ws, selected, requirements)
    else:
        wb = Workbook()
        _write_generic_output(wb, selected)

    # Do not add extra audit tabs to the client-facing workbook.
    # The output should preserve the uploaded agency grid format only.
    wb.save(output_path)
