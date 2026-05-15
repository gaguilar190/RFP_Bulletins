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
            value = value.replace("$", "").replace(",", "").strip()
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


def _value_for_template_header(header: str, row: pd.Series, requirements: dict[str, Any]) -> Any:
    h = _normalize_header(header)

    campaign_start = _format_date(requirements.get("campaign_start"))
    campaign_end = _format_date(requirements.get("campaign_end"))

    # Fixed business rules
    if h in {"vendor", "media owner"}:
        return "Bulletin Displays"

    if h in {"availability"}:
        return _campaign_date_range(requirements)

    if h in {"illumination", "illuminated"}:
        return "Yes"

    if h in {"forced vendor production y n", "is production forced", "forced vendor production"}:
        return "No"

    if h in {"taxes", "tax"}:
        return 0

    # Original RFP template fields
    if h == "market":
        return _first_existing(row, ["market", "city"], "")

    if h == "client market name":
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

    if h in {"size hxw", "size", "dimensions"}:
        return _first_existing(row, ["size"], "")

    if h in {"number of units", "of units"}:
        return 1

    if h in {"18 plus impressions cycle", "18 plus 4 week impressions", "a18 plus 4 wk impressions"}:
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

    if h in {"18 plus impressions week", "a18 plus weekly impressions", "weekly impressions"}:
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

    if h in {"rate card cycle", "rate card", "4 week rate card"}:
        return _money(_first_existing(row, ["rate_card_4wk", "rate_card"], 0))

    if h in {"net media cost cycle", "4 week media cost", "4 week net media cost"}:
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

    if h == "start date":
        return campaign_start

    if h == "end date":
        return campaign_end

    if h in {"number of cycles", "of cycles"}:
        contracted_weeks = _to_number(_first_existing(row, ["contracted_weeks"], 0), 0)
        if contracted_weeks:
            return round(contracted_weeks / 4, 2)
        return ""

    if h == "cycle type":
        return "4 Week"

    if h in {"number of copy changes included at no cost after initial install", "of copy changes included at no cost after initial install"}:
        return 0

    if h in {"number of paid installs", "of paid installs"}:
        return 1

    if h == "total postings":
        return 1

    if h == "installation cost":
        return 1600 if _is_special_unit_01134(row) else 850

    if h == "print qty per posting":
        return 1

    if h in {"total to produce", "production cost"}:
        return 950 if _is_special_unit_01134(row) else 750

    if h == "forced vendor production cost":
        return 0

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

    if h in {"number of spots", "of spots"}:
        return _first_existing(row, ["number_of_spots", "spots"], "")

    if h == "spot length":
        return _first_existing(row, ["spot_length", "spot_length_seconds"], "")

    if h == "net campaign media cost":
        return _money(_first_existing(row, ["contracted_media_cost", "total_media_cost"], 0))

    if h == "cancellation clause":
        return ""

    if h == "summary":
        return _first_existing(row, ["selection_reason", "comments", "description"], "")

    if h == "target area location":
        return _first_existing(row, ["target_location"], "")

    if h == "distance from target miles":
        value = _first_existing(row, ["distance_to_poi_miles"], "")
        if value == "":
            return ""
        return round(_to_number(value, 0), 2)

    if h == "production rep name":
        return ""

    if h == "loop length seconds":
        return _first_existing(row, ["loop_length_seconds"], "")

    if h == "digital display type":
        media_type = _safe_str(_first_existing(row, ["media_type"], ""))
        if "digital" in media_type.lower():
            return "Digital"
        return ""

    if h == "pixel size h x w":
        return _first_existing(row, ["pixel_size", "pixel_size_hxw"], "")

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

    # Business rule fields that may appear in other agency templates
    if h in {"install cost", "installation cost final"}:
        return 1600 if _is_special_unit_01134(row) else 850

    if h in {"production cost final", "production cost"}:
        return 950 if _is_special_unit_01134(row) else 750

    if h in {"is production forced"}:
        return "No"

    # Fallback direct column match
    for col in row.index:
        if _normalize_header(col) == h:
            return row.get(col)

    return ""


def _find_header_row(ws) -> int:
    best_row = 1
    best_score = 0

    expected_headers = {
        "market",
        "format",
        "vendor inventory number",
        "geopath id number",
        "location description",
        "latitude",
        "longitude",
        "rate card cycle",
        "net media cost cycle",
        "start date",
        "end date",
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


def _write_dataframe_sheet(wb, sheet_name: str, df: pd.DataFrame) -> None:
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]

    ws = wb.create_sheet(sheet_name)

    if df is None or df.empty:
        ws.cell(1, 1).value = "No records."
        return

    headers = list(df.columns)
    ws.append(headers)

    for _, row in df.iterrows():
        ws.append([row.get(col) for col in headers])

    for cell in ws[1]:
        cell.font = copy(cell.font)
        cell.font = cell.font.copy(bold=True)

    ws.freeze_panes = "A2"

    for col_cells in ws.columns:
        max_len = 0
        col_letter = col_cells[0].column_letter
        for cell in col_cells[:100]:
            max_len = max(max_len, len(str(cell.value or "")))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 40)


def _write_requirements_sheet(wb, requirements: dict[str, Any]) -> None:
    if "Requirement Checklist" in wb.sheetnames:
        del wb["Requirement Checklist"]

    ws = wb.create_sheet("Requirement Checklist")
    ws.cell(1, 1).value = "Requirement"
    ws.cell(1, 2).value = "Value"

    row_idx = 2
    for key, value in requirements.items():
        ws.cell(row_idx, 1).value = key
        ws.cell(row_idx, 2).value = str(value)
        row_idx += 1

    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 80


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

    _write_requirements_sheet(wb, requirements)
    _write_dataframe_sheet(wb, "Selected Units Review", selected)
    _write_dataframe_sheet(wb, "Excluded Units", excluded)
    _write_dataframe_sheet(wb, "Missing Fields Report", missing_fields)

    wb.save(output_path)
