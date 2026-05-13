from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict[str, Any], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clean_header(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_key(value: Any) -> str:
    s = clean_header(value).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def first_present(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and not is_blank(row[key]):
            return row[key]
    return default


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if isinstance(value, float) and math.isnan(value):
            return True
    except Exception:
        pass
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def to_number(value: Any) -> float | None:
    if is_blank(value):
        return None
    if isinstance(value, (int, float)):
        try:
            if math.isnan(float(value)):
                return None
        except Exception:
            pass
        return float(value)
    s = str(value).replace("$", "").replace(",", "").strip()
    if s.upper() in {"TBD", "N/A", "NA", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def safe_str(value: Any) -> str:
    if is_blank(value):
        return ""
    return str(value).strip()


def contains_any(text: Any, needles: Iterable[str]) -> bool:
    t = safe_str(text).lower()
    return any(safe_str(n).lower() in t for n in needles if safe_str(n))
