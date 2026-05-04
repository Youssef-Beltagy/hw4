"""
Natural-language question -> structured query classifier for the country game.

The LLM's ONLY job is to map a user's free-text yes/no question to a Query
object against the field catalog. It does NOT see the secret country and does
NOT answer from its own knowledge. The deterministic evaluator in
country_logic.evaluate() is what actually produces the yes/no.

This file is the LLM boundary. Everything downstream of classify_question()
is pure Python.

Design notes:
  * Uses google-genai with response_mime_type=application/json + response_json_schema
    to force valid JSON output. No regex-extraction of code fences.
  * A null field in the response means "not in schema" and the app should
    answer "I don't know". The LLM is instructed to prefer null over guessing.
  * If GOOGLE_API_KEY is missing, we fall back to a tiny keyword classifier
    so the app is still runnable (with reduced quality). Primarily useful
    for local dev and tests.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from country_logic import FIELD_CATALOG, InvalidQuery, Query, validate_query

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class ClassifyResult:
    """Outcome of classifying one user question."""

    query: Query | None  # None if the question is out-of-schema
    reason: str | None = None  # human-readable "why no query" when query is None
    source: str = "unknown"  # "gemini", "fallback", or "error"


# ---------------------------------------------------------------------------
# Prompt + schema
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gemini-2.5-flash"
API_KEY_ENV = "GOOGLE_API_KEY"


def _build_system_prompt() -> str:
    """System prompt describing the schema the model must produce."""
    field_lines = []
    for name, meta in FIELD_CATALOG.items():
        ops = ", ".join(meta["ops"])
        desc = meta["description"]
        extras = ""
        if "values" in meta:
            extras = f" Allowed values: {meta['values']}."
        field_lines.append(f"- {name} (ops: {ops}): {desc}{extras}")
    fields_block = "\n".join(field_lines)

    return (
        "You translate a player's free-text yes/no question from a 20-questions style "
        "country-guessing game into a single structured query against a fixed schema. "
        "You DO NOT answer the question. You DO NOT know the secret country. "
        "Your only job is to produce JSON describing what fact the player is asking about.\n\n"
        "Schema (each field lists its allowed operators):\n"
        f"{fields_block}\n\n"
        "Rules:\n"
        "1. Respond with a JSON object with keys: field (string|null), op (string|null), "
        "value (any|null), reason (string|null).\n"
        "2. If the question maps cleanly to the schema, fill field/op/value and leave reason null.\n"
        "3. If the question cannot be answered from the schema, set field=null, op=null, "
        "value=null, and put a short explanation in reason. Do NOT guess.\n"
        "4. For borders, value must be an ISO 3166-1 alpha-3 code (e.g. 'FRA' for France, "
        "'DEU' for Germany). If the player names a country, convert it.\n"
        "5. For languages, use the English language name (e.g. 'Spanish', 'Arabic').\n"
        "6. For size/population questions ('is it big?', 'does it have many people?'), "
        "use area_bucket / population_bucket with the 'in' operator and an appropriate "
        "subset of allowed values.\n"
        "7. For 'is it an island?' prefer is_island=true over borders is_empty.\n"
        "8. For 'is it <CountryName>?' (a final guess), use field='name', op='equals', "
        "value=<CountryName>.\n"
        "9. NEVER invent fields or operators not listed above.\n"
    )


# JSON schema enforced at the API level. Keys intentionally permissive (all
# nullable) so the model can signal out-of-schema via field=null without a
# separate error channel.
RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "nullable": True},
        "op": {"type": "string", "nullable": True},
        # value is intentionally omitted from the top-level schema because
        # Gemini's structured-output subset doesn't model union types well.
        # We validate value in Python after the call.
        "value_string": {"type": "string", "nullable": True},
        "value_bool": {"type": "boolean", "nullable": True},
        "value_list": {
            "type": "array",
            "items": {"type": "string"},
            "nullable": True,
        },
        "reason": {"type": "string", "nullable": True},
    },
    "required": ["field", "op", "reason"],
}


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------


def _extract_value(raw: dict) -> Any:
    """Pick whichever of the three value_* slots the model populated."""
    for key in ("value_string", "value_bool", "value_list"):
        v = raw.get(key)
        if v is not None:
            return v
    return None


def _classify_with_gemini(question: str, model: str, api_key: str) -> ClassifyResult:
    """Call Gemini and turn its JSON response into a ClassifyResult."""
    # Import lazily so tests and the fallback path don't need google-genai installed.
    from google import genai  # type: ignore

    client = genai.Client(api_key=api_key)

    system_prompt = _build_system_prompt()

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                {"role": "user", "parts": [{"text": f"Player question: {question!r}"}]},
            ],
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "response_json_schema": RESPONSE_JSON_SCHEMA,
                "temperature": 0,
            },
        )
    except Exception as exc:  # network / auth / quota / transient
        logger.warning("Gemini call failed: %s", exc)
        return ClassifyResult(query=None, reason=f"LLM error: {exc}", source="error")

    try:
        raw = json.loads(response.text)
    except (ValueError, AttributeError) as exc:
        logger.warning("Gemini returned non-JSON: %s", exc)
        return ClassifyResult(query=None, reason="LLM returned invalid JSON", source="error")

    field = raw.get("field")
    op = raw.get("op")
    reason = raw.get("reason")

    if not field or not op:
        return ClassifyResult(query=None, reason=reason or "out of schema", source="gemini")

    value = _extract_value(raw)
    try:
        query = validate_query({"field": field, "op": op, "value": value})
    except InvalidQuery as exc:
        logger.info("Gemini produced invalid query %s: %s", raw, exc)
        return ClassifyResult(query=None, reason=f"invalid query: {exc}", source="gemini")

    return ClassifyResult(query=query, source="gemini")


# ---------------------------------------------------------------------------
# Fallback keyword classifier (no API key needed)
# ---------------------------------------------------------------------------

# Very small rule-based classifier used when GOOGLE_API_KEY is not set. It
# handles the most common question shapes so the app is still playable (with
# reduced quality) in offline demos and tests. Order matters - more specific
# patterns first.
_FALLBACK_RULES: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"\bisland\b", re.I), {"field": "is_island", "op": "equals", "value": True}),
    (re.compile(r"\blandlocked\b", re.I), {"field": "landlocked", "op": "equals", "value": True}),
    (re.compile(r"\bin (africa|asia|europe|oceania)\b", re.I), None),  # filled in below
    (re.compile(r"\bin the americas\b", re.I), {"field": "region", "op": "equals", "value": "Americas"}),
    (re.compile(r"\bnorthern hemisphere\b", re.I), {"field": "hemisphere_ns", "op": "equals", "value": "N"}),
    (re.compile(r"\bsouthern hemisphere\b", re.I), {"field": "hemisphere_ns", "op": "equals", "value": "S"}),
    (re.compile(r"\b(big|large|huge) country\b", re.I), {"field": "area_bucket", "op": "in", "value": ["large", "very_large"]}),
    (re.compile(r"\b(small|tiny) country\b", re.I), {"field": "area_bucket", "op": "in", "value": ["small"]}),
    (re.compile(r"\b(many|lots of|populous)\b", re.I), {"field": "population_bucket", "op": "in", "value": ["large", "huge"]}),
    (re.compile(r"\bspeak ([A-Za-z]+)", re.I), "_language"),  # special handler
    (re.compile(r"\bis it ([A-Z][A-Za-z .\-']+)\??$"), "_name_guess"),  # final guess
]


def _classify_with_fallback(question: str) -> ClassifyResult:
    """Rule-based question classifier. Covers common cases only."""
    q = question.strip()

    # Region
    m = re.search(r"\bin (africa|asia|europe|oceania)\b", q, re.I)
    if m:
        region = m.group(1).capitalize()
        try:
            return ClassifyResult(
                query=validate_query({"field": "region", "op": "equals", "value": region}),
                source="fallback",
            )
        except InvalidQuery:
            pass

    # Language
    m = re.search(r"\b(?:speak|language is|spoken)\s+([A-Z][A-Za-z]+)", q)
    if m:
        lang = m.group(1).capitalize()
        return ClassifyResult(
            query=validate_query({"field": "languages", "op": "contains", "value": lang}),
            source="fallback",
        )

    # Simpler patterns
    for pattern, action in _FALLBACK_RULES:
        if action is None or isinstance(action, str):
            continue  # handled above
        if pattern.search(q):
            try:
                return ClassifyResult(query=validate_query(action), source="fallback")
            except InvalidQuery:
                continue

    # Final-guess shortcut — checked LAST because "Is it in Asia?" would
    # otherwise greedy-match "in Asia" as a country name. We only accept
    # the guess if the captured phrase actually resolves to a known country.
    m = re.search(r"\bis it ([A-Z][A-Za-z .\-']+?)\??$", q, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip().rstrip(".?!")
        # Defer to the caller for the actual lookup; we just pass it through.
        # Caller (app) will run find_country_by_guess and either win or record a miss.
        try:
            query = validate_query({"field": "name", "op": "equals", "value": candidate})
            return ClassifyResult(query=query, source="fallback")
        except InvalidQuery:
            pass

    return ClassifyResult(
        query=None,
        reason="fallback classifier could not map this question; set GOOGLE_API_KEY for better coverage",
        source="fallback",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_question(
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> ClassifyResult:
    """
    Classify a free-text question into a structured query.

    api_key precedence: explicit argument > env[GOOGLE_API_KEY] > fallback.
    Never raises — errors are represented in the returned ClassifyResult.
    """
    if not question or not question.strip():
        return ClassifyResult(query=None, reason="empty question", source="fallback")

    key = api_key or os.environ.get(API_KEY_ENV)
    if key:
        result = _classify_with_gemini(question, model=model, api_key=key)
        # If Gemini hard-errors, try the fallback so the game stays playable.
        if result.source == "error":
            logger.info("Gemini errored; using fallback classifier")
            fb = _classify_with_fallback(question)
            if fb.query is not None:
                return fb
        return result

    return _classify_with_fallback(question)
