from __future__ import annotations
import json
import re
import streamlit as st
from groq import Groq

import json
import re
from pathlib import Path
from typing import Any

from dateutil.parser import parse as parse_date
from pydantic import BaseModel, Field
from pypdf import PdfReader
def extract_with_groq(brief_text: str, model: str = "llama-3.1-8b-instant") -> dict:
    api_key = st.secrets.get("GROQ_API_KEY")

    if not api_key:
        req = default_requirements()
        req["special_instructions"] = (
            "Missing GROQ_API_KEY in Streamlit Secrets. Manual review required."
        )
        req["unclear_items"] = ["Missing GROQ_API_KEY"]
        return req

    client = Groq(api_key=api_key)

    prompt = f"""
You are extracting structured requirements for an out-of-home advertising RFP.

Return ONLY valid JSON. No markdown. No comments.

Use this exact schema:
{{
  "advertiser": "",
  "campaign_name": "",
  "campaign_start": null,
  "campaign_end": null,
  "markets": [],
  "cities": [],
  "media_types": [],
  "target_audience": "A18+",
  "poi_requirements": [],
  "max_distance_miles": null,
  "directional_keywords": [],
  "pricing": {{
    "pricing_basis": "contracted_period",
    "rate_increase_percent": 0,
    "increase_applies_to": "media_only",
    "discount_percent": 0,
    "costs_to_include": ["media", "production", "install"]
  }},
  "number_of_units": 25,
  "required_output_columns": [],
  "special_instructions": "",
  "unclear_items": []
}}

Rules:
- Extract all target geography from the brief.
- Never leave markets and cities empty if the brief mentions locations.
- If the brief says San Francisco or SF, include "San Francisco".
- If the brief says Sacramento or Sacto, include "Sacramento".
- If the brief says San Jose, include "San Jose".
- If the brief says Santa Cruz, include "Santa Cruz".
- If the brief says Monterey, include "Monterey".
- If the brief says Pebble Beach, include "Pebble Beach".
- If the brief mentions priority geography, include all priority locations.
- Extract campaign dates in YYYY-MM-DD format.
- Extract requested formats into media_types.
- If partial flights are accepted, mention that in special_instructions.
- Do not invent pricing, rates, impressions, or inventory.

Brief:
{brief_text}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON for the requested schema.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0,
    )

    content = response.choices[0].message.content.strip()

    # Remove accidental markdown fences if the model adds them.
    content = re.sub(r"^```json\s*", "", content)
    content = re.sub(r"^```\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except Exception:
        req = default_requirements()
        req["special_instructions"] = (
            "Groq returned invalid JSON. Manual review required."
        )
        req["unclear_items"] = [content[:1000]]
        return req


class PricingRequirement(BaseModel):
    pricing_basis: str = "contracted_period"
    rate_increase_percent: float = 0
    increase_applies_to: str = "media_only"
    discount_percent: float = 0
    costs_to_include: list[str] = Field(default_factory=lambda: ["media", "production", "install"])


class RFPRequirements(BaseModel):
    advertiser: str = ""
    campaign_name: str = ""
    campaign_start: str | None = None
    campaign_end: str | None = None
    markets: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    media_types: list[str] = Field(default_factory=list)
    target_audience: str = "A18+"
    poi_requirements: list[dict[str, Any]] = Field(default_factory=list)
    max_distance_miles: float | None = None
    directional_keywords: list[str] = Field(default_factory=list)
    pricing: PricingRequirement = Field(default_factory=PricingRequirement)
    number_of_units: int = 25
    required_output_columns: list[str] = Field(default_factory=list)
    special_instructions: str = ""
    unclear_items: list[str] = Field(default_factory=list)


def default_requirements() -> dict[str, Any]:
    return RFPRequirements().model_dump()


def coerce_requirements(data: dict[str, Any]) -> dict[str, Any]:
    return RFPRequirements(**data).model_dump()


def extract_text_from_pdf(path_or_file: str | Path | Any) -> str:
    reader = PdfReader(path_or_file)
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
    return "\n".join(parts)


def extract_json_block(text: str) -> dict[str, Any]:
    # Try fenced JSON first.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        return json.loads(fenced.group(1))
    # Then try the largest object-looking span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in model response.")


def _first_two_dates(text: str) -> tuple[str | None, str | None]:
    candidates = []
    # Match common dates such as 6/1/26, 06/01/2026, June 1 2026.
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,)?\s+\d{2,4}\b",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    parsed = []
    for raw in candidates:
        try:
            dt = parse_date(raw, fuzzy=True).date().isoformat()
            if dt not in parsed:
                parsed.append(dt)
        except Exception:
            continue
    if len(parsed) >= 2:
        return parsed[0], parsed[1]
    if len(parsed) == 1:
        return parsed[0], None
    return None, None


def _heuristic_requirements(brief_text: str) -> dict[str, Any]:
    req = default_requirements()
    text = brief_text or ""
    lower = text.lower()
    unclear = []

    market_patterns = [
        ("San Francisco", [r"\bsan\s+francisco\b", r"\bsf\b", r"\bs\.f\.\b"]),
        ("Los Angeles", [r"\blos\s+angeles\b", r"\bla\s+dma\b", r"\bl\.a\.\b"]),
        ("Sacramento", [r"\bsacramento\b"]),
        ("Fresno", [r"\bfresno\b"]),
    ]
    markets = []
    for label, patterns in market_patterns:
        if any(re.search(p, lower, flags=re.IGNORECASE) for p in patterns):
            markets.append(label)
    req["markets"] = markets

    media_types = []
    if re.search(r"\bdigital\b", lower):
        media_types.append("Digital Bulletin")
    if re.search(r"\bstatic\b|\bbulletin\b|\bbillboard\b", lower):
        # If the brief only says digital, do not force static. If it says billboards without digital, static is okay.
        if "Digital Bulletin" not in media_types or re.search(r"\bstatic\b|\bnon[-\s]?digital\b", lower):
            media_types.append("Static Bulletin")
    req["media_types"] = media_types

    start, end = _first_two_dates(text)
    req["campaign_start"] = start
    req["campaign_end"] = end

    rate_match = re.search(r"(?:increase|raise|markup|mark\s*up)[^\d]{0,30}(\d+(?:\.\d+)?)\s*%", lower)
    if rate_match:
        req["pricing"]["rate_increase_percent"] = float(rate_match.group(1))
    discount_match = re.search(r"(?:discount|decrease|reduce)[^\d]{0,30}(\d+(?:\.\d+)?)\s*%", lower)
    if discount_match:
        req["pricing"]["discount_percent"] = float(discount_match.group(1))

    distance_match = re.search(r"(?:within|under|no more than|max(?:imum)?|radius of)\s+(\d+(?:\.\d+)?)\s*(?:mile|mi)\b", lower)
    if distance_match:
        req["max_distance_miles"] = float(distance_match.group(1))

    unit_match = re.search(r"(?:recommend|include|provide|select|proposal for)?\s*(\d{1,3})\s+(?:boards|units|locations|billboards)\b", lower)
    if unit_match:
        req["number_of_units"] = int(unit_match.group(1))

    audience_match = re.search(r"\bA\s?\d{2}\+?(?:\s*-\s*\d{2})?\b", text, flags=re.IGNORECASE)
    if audience_match:
        req["target_audience"] = audience_match.group(0).replace(" ", "").upper()

    if re.search(r"\bdirectional\b|\btoward\b|\bheading\b|\binbound\b|\boutbound\b", lower):
        # Keep this broad. Matching uses comments/tags and flags uncertain rows.
        keywords = []
        for phrase in ["LAX", "airport", "downtown", "stadium", "SoFi", "SF Zoo", "zoo"]:
            if phrase.lower() in lower:
                keywords.append(phrase)
        req["directional_keywords"] = keywords
        if not keywords:
            unclear.append("Directional language found, but no specific directional destination was detected.")

    if not markets:
        unclear.append("No market detected by heuristic extraction. Review markets before running.")
    if not start or not end:
        unclear.append("Campaign start/end dates were not both detected. Review campaign dates before running.")

    req["special_instructions"] = "Heuristic extraction mode. Review this JSON before running. Use Ollama for better brief reading when available."
    req["unclear_items"] = unclear
    return coerce_requirements(req)


def extract_requirements_with_ollama(
    brief_text: str,
    model: str = "llama3.1:8b",
) -> dict[str, Any]:
    import ollama

    schema_hint = RFPRequirements.model_json_schema()
    prompt = f"""
You are reading an out-of-home advertising RFP brief.
Extract the requirements as strict JSON only. Do not add commentary.

Important rules:
- Do not invent pricing, impressions, availability, or distances.
- If a detail is unclear, put it in unclear_items.
- Use null when dates or numbers are not provided.
- For markets, capture exact requested market names. If the brief says San Francisco or SF, markets must include San Francisco. Do not include Los Angeles unless the brief explicitly asks for it.
- For media_types, use labels like Static Bulletin, Digital Bulletin, Poster, Wallscape, Transit.
- For pricing, capture contracted period, rate increase percent, discounts, and costs to include.
- For POI distance, include poi_name, poi_address if available, latitude and longitude if provided, and max_distance_miles if stated.

JSON schema shape:
{json.dumps(schema_hint, indent=2)}

RFP brief:
{brief_text}
""".strip()

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    content = response["message"]["content"]
    parsed = extract_json_block(content)
    return coerce_requirements(parsed)


def extract_requirements(
    brief_text: str,
    use_ai: bool = False,
    groq_model: str = "llama-3.1-8b-instant",
    use_ollama: bool = False,
    ollama_model: str = "llama3.1:8b",
) -> dict[str, Any]:
    if not brief_text.strip():
        return default_requirements()

    if use_ai:
        try:
            return coerce_requirements(extract_with_groq(brief_text, model=groq_model))
        except Exception as exc:
            req = _heuristic_requirements(brief_text)
            req["special_instructions"] = (
                "Groq extraction failed. Heuristic extraction was used; review before running."
            )
            req["unclear_items"] = req.get("unclear_items", []) + [str(exc)]
            return coerce_requirements(req)

    if use_ollama:
        try:
            return extract_requirements_with_ollama(brief_text, model=ollama_model)
        except Exception as exc:
            req = _heuristic_requirements(brief_text)
            req["special_instructions"] = (
                "Ollama extraction failed. Heuristic extraction was used; review before running."
            )
            req["unclear_items"] = req.get("unclear_items", []) + [str(exc)]
            return coerce_requirements(req)

    return coerce_requirements(_heuristic_requirements(brief_text))
