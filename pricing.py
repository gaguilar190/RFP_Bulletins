from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dateutil.parser import parse as parse_date

from utils import is_blank, load_json, safe_str, to_number


DEFAULT_BUSINESS_RULES = {
    "vendor": "Bulletin Displays",
    "illuminated": "Yes",
    "default_install_cost": 850,
    "install_cost_overrides": {"01134": 1600, "01134-SF": 1600},
    "default_production_cost": 750,
    "production_cost_overrides": {"01134": 950, "01134-SF": 950},
    "is_production_forced": "No",
    "taxes": 0,
}


def _parse_date(value: Any) -> date | None:
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return parse_date(str(value)).date()
    except Exception:
        return None


def _short_date(value: date | None) -> str:
    if not value:
        return ""
    return f"{value.month}/{value.day}/{str(value.year)[-2:]}"


def _campaign_date_range(period: dict[str, Any]) -> str:
    start = period.get("campaign_start")
    end = period.get("campaign_end")
    if start and end:
        return f"{_short_date(start)} - {_short_date(end)}"
    return "Missing campaign dates"


def calculate_contract_period(requirements: dict[str, Any], proration_rule: str = "round_up_to_full_week") -> dict[str, Any]:
    start = _parse_date(requirements.get("campaign_start"))
    end = _parse_date(requirements.get("campaign_end"))

    if not start or not end or end < start:
        return {
            "campaign_start": start,
            "campaign_end": end,
            "contracted_days": None,
            "contracted_weeks_exact": None,
            "contracted_weeks": 4,
            "period_note": "Missing or invalid campaign dates. Defaulted to 4 weeks. Review required.",
            "needs_review": True,
        }

    days = (end - start).days + 1
    weeks_exact = days / 7
    if proration_rule == "exact_days":
        weeks = weeks_exact
    elif proration_rule == "round_up_to_full_4_weeks":
        weeks = math.ceil(weeks_exact / 4) * 4
    else:
        weeks = math.ceil(weeks_exact)

    return {
        "campaign_start": start,
        "campaign_end": end,
        "contracted_days": days,
        "contracted_weeks_exact": weeks_exact,
        "contracted_weeks": weeks,
        "period_note": f"Calculated {days} days / {weeks_exact:.2f} exact weeks; priced as {weeks} weeks using {proration_rule}.",
        "needs_review": False,
    }


def _get_pricing_instruction(requirements: dict[str, Any]) -> dict[str, Any]:
    pricing = requirements.get("pricing") or {}
    if not isinstance(pricing, dict):
        pricing = {}
    return pricing


def _load_business_rules(pricing_rules_path: str | Path, business_rules_path: str | Path | None = None) -> dict[str, Any]:
    rules = DEFAULT_BUSINESS_RULES.copy()
    candidate = Path(business_rules_path) if business_rules_path else Path(pricing_rules_path).with_name("business_rules.json")
    if candidate.exists():
        loaded = load_json(candidate)
        rules.update(loaded)
    return rules


def _unit_override_keys(unit_id: Any) -> list[str]:
    unit = safe_str(unit_id)
    keys = [unit]
    # Unit 01134 appears in the source as 01134-SF, but the business rule may be written as 01134.
    m = re.match(r"^(\d+)", unit)
    if m and m.group(1) not in keys:
        keys.append(m.group(1))
    return keys


def _override_cost(unit_id: Any, default_value: float, overrides: dict[str, Any]) -> float:
    normalized = {safe_str(k).upper(): to_number(v) for k, v in overrides.items()}
    for key in _unit_override_keys(unit_id):
        value = normalized.get(safe_str(key).upper())
        if value is not None:
            return float(value)
    return float(default_value)


def add_pricing(
    inventory: pd.DataFrame,
    requirements: dict[str, Any],
    pricing_rules_path: str | Path = "config/pricing_rules.json",
    business_rules_path: str | Path | None = None,
) -> pd.DataFrame:
    rules = load_json(pricing_rules_path)
    business_rules = _load_business_rules(pricing_rules_path, business_rules_path)
    pricing = _get_pricing_instruction(requirements)
    period = calculate_contract_period(requirements, rules.get("default_proration_rule", "round_up_to_full_week"))

    rate_increase = to_number(pricing.get("rate_increase_percent")) or 0.0
    discount = to_number(pricing.get("discount_percent")) or 0.0
    include_production = "production" in (pricing.get("costs_to_include") or []) or rules.get("include_production_in_total", True)
    include_install = "install" in (pricing.get("costs_to_include") or []) or rules.get("include_install_in_total", True)
    default_cost_col = rules.get("default_cost_column", "negotiated_rate_4wk")
    fallback_cost_col = rules.get("fallback_cost_column", "rate_card_4wk")
    allow_fallback = bool(rules.get("allow_fallback_cost_column", False))
    round_currency = bool(rules.get("round_currency_to_nearest_dollar", True))

    df = inventory.copy()
    out_rows = []
    campaign_availability = _campaign_date_range(period)

    for _, row in df.iterrows():
        base_4wk = to_number(row.get(default_cost_col))
        cost_source = default_cost_col
        review_flags = []
        if base_4wk is None and allow_fallback:
            base_4wk = to_number(row.get(fallback_cost_col))
            cost_source = fallback_cost_col
            review_flags.append(f"Used fallback cost column: {fallback_cost_col}.")

        if base_4wk is None:
            base_4wk = 0.0
            review_flags.append(f"Missing {default_cost_col}. Pricing set to 0 for review.")

        adjusted_4wk = base_4wk
        if rate_increase:
            adjusted_4wk = adjusted_4wk * (1 + rate_increase / 100)
        if discount:
            adjusted_4wk = adjusted_4wk * (1 - discount / 100)

        contracted_weeks = period["contracted_weeks"] or 4
        weekly_rate = adjusted_4wk / 4
        contracted_media_cost = weekly_rate * contracted_weeks

        production = _override_cost(
            row.get("unit_id"),
            to_number(business_rules.get("default_production_cost")) or 0.0,
            business_rules.get("production_cost_overrides", {}),
        )
        install = _override_cost(
            row.get("unit_id"),
            to_number(business_rules.get("default_install_cost")) or 0.0,
            business_rules.get("install_cost_overrides", {}),
        )
        taxes = to_number(business_rules.get("taxes")) or 0.0

        total = contracted_media_cost
        if include_production:
            total += production
        if include_install:
            total += install
        total += taxes

        weekly_imps = to_number(row.get("geopath_a18_weekly_impressions"))
        four_week_imps = to_number(row.get("geopath_a18_4wk_impressions"))
        if weekly_imps is None and four_week_imps is not None:
            weekly_imps = four_week_imps / 4
        contracted_impressions = weekly_imps * contracted_weeks if weekly_imps is not None else None
        cpm = (contracted_media_cost / contracted_impressions * 1000) if contracted_impressions and contracted_impressions > 0 else None
        if cpm is None:
            review_flags.append("Missing or invalid impressions for CPM.")

        if period["needs_review"]:
            review_flags.append(period["period_note"])

        four_week_media_cost = to_number(row.get("negotiated_rate_4wk"))
        if four_week_media_cost is None:
            review_flags.append("4-week media cost should be Negotiated Rate, but Negotiated Rate is missing.")

        in_market_reach = to_number(row.get("in_market_reach"))
        in_market_frequency = to_number(row.get("in_market_frequency"))
        a18_4wk_reach_percent = in_market_reach * 4 if in_market_reach is not None else None
        a18_4wk_freq = in_market_frequency * 4 if in_market_frequency is not None else None

        pricing_note = (
            f"4-week media cost uses Negotiated Rate: ${four_week_media_cost or 0:,.0f}. "
            f"Pricing base from {cost_source}: ${base_4wk:,.0f}. "
            f"Increase: {rate_increase:.2f}%. Discount: {discount:.2f}%. "
            f"Priced over {contracted_weeks} contracted weeks. "
            f"Availability shown as campaign dates: {campaign_availability}."
        )
        if production or install or taxes:
            pricing_note += f" Production ${production:,.0f}; install ${install:,.0f}; taxes ${taxes:,.0f}."

        def money(x: float | None) -> float | None:
            if x is None:
                return None
            return round(x) if round_currency else x

        out_rows.append(
            {
                "media_owner": business_rules.get("vendor", "Bulletin Displays"),
                "availability": campaign_availability,
                "illuminated": business_rules.get("illuminated", "Yes"),
                "four_week_media_cost": money(four_week_media_cost),
                "base_4wk_rate": money(base_4wk),
                "cost_source": cost_source,
                "rate_increase_percent": rate_increase,
                "discount_percent": discount,
                "adjusted_4wk_rate": money(adjusted_4wk),
                "adjusted_weekly_rate": money(weekly_rate),
                "contracted_weeks": contracted_weeks,
                "contracted_days": period["contracted_days"],
                "contracted_media_cost": money(contracted_media_cost),
                "production_cost_final": money(production),
                "install_cost_final": money(install),
                "is_production_forced": business_rules.get("is_production_forced", "No"),
                "taxes": money(taxes),
                "total_campaign_cost": money(total),
                "contracted_impressions": round(contracted_impressions) if contracted_impressions is not None else None,
                "cpm": round(cpm, 2) if cpm is not None else None,
                "a18_4wk_reach_percent": a18_4wk_reach_percent,
                "a18_4wk_freq": a18_4wk_freq,
                "pricing_note": pricing_note,
                "pricing_review_flags": " | ".join(review_flags),
            }
        )

    price_df = pd.DataFrame(out_rows)
    # Remove duplicate derived columns from the source before concatenating, so final business rules win.
    derived_cols = [c for c in price_df.columns if c in df.columns]
    if derived_cols:
        df = df.drop(columns=derived_cols)
    return pd.concat([df.reset_index(drop=True), price_df], axis=1)
