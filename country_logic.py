"""
Deterministic core for the Country 20 Questions game.

This module deliberately contains no LLM calls, no Streamlit imports, and no
I/O beyond reading the vendored dataset at import time. Everything here is
pure so it can be unit-tested exhaustively.

Responsibilities:
  * Load data/countries.json
  * Define the query schema the classifier must produce
  * Evaluate a structured query against a country record (the yes/no answer)
  * Fuzzy-match a free-text final guess against a country's aliases
  * Compute the final score
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parent / "data" / "countries.json"


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_countries(path: Path | None = None) -> list[dict]:
    """Load the vendored country dataset. Cached in module state."""
    global _CACHED_COUNTRIES
    if path is None and _CACHED_COUNTRIES is not None:
        return _CACHED_COUNTRIES
    target = path or DATASET_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    countries = payload["countries"]
    if path is None:
        _CACHED_COUNTRIES = countries
    return countries


_CACHED_COUNTRIES: list[dict] | None = None


# ---------------------------------------------------------------------------
# Query schema
# ---------------------------------------------------------------------------

# The set of queryable fields and the operators each supports. This is the
# contract the classifier must produce and is also embedded into the LLM
# prompt so the model cannot invent new fields.
FIELD_CATALOG: dict[str, dict[str, Any]] = {
    "region": {
        "type": "string",
        "ops": ["equals"],
        "values": ["Africa", "Americas", "Asia", "Europe", "Oceania", "Antarctic"],
        "description": "Broad continental region.",
    },
    "subregion": {
        "type": "string",
        "ops": ["equals"],
        "description": "Sub-region such as 'Southern Europe', 'Eastern Asia'.",
    },
    "hemisphere_ns": {
        "type": "string",
        "ops": ["equals"],
        "values": ["N", "S"],
        "description": "Northern (N) or Southern (S) hemisphere by country centroid.",
    },
    "hemisphere_ew": {
        "type": "string",
        "ops": ["equals"],
        "values": ["E", "W"],
        "description": "Eastern (E) or Western (W) hemisphere by country centroid.",
    },
    "capital": {
        "type": "string",
        "ops": ["equals"],
        "description": "Capital city name (case-insensitive).",
    },
    "languages": {
        "type": "list[string]",
        "ops": ["contains"],
        "description": "Official or widely spoken languages. Use the English language name.",
    },
    "borders": {
        "type": "list[string]",
        "ops": ["contains", "is_empty"],
        "description": "List of bordering countries as ISO 3166-1 alpha-3 codes (e.g. 'FRA'). Use `contains` for 'does it border X?' and `is_empty` for 'does it border any country?'.",
    },
    "landlocked": {
        "type": "boolean",
        "ops": ["equals"],
        "description": "True if the country has no ocean coastline.",
    },
    "is_island": {
        "type": "boolean",
        "ops": ["equals"],
        "description": "True if the country has no land borders and is not landlocked (i.e. surrounded by water).",
    },
    "area_bucket": {
        "type": "string",
        "ops": ["equals", "in"],
        "values": ["small", "medium", "large", "very_large"],
        "description": "Relative land area bucket across all countries.",
    },
    "population_bucket": {
        "type": "string",
        "ops": ["equals", "in"],
        "values": ["tiny", "small", "medium", "large", "huge"],
        "description": "Relative population bucket across all countries.",
    },
    "name": {
        "type": "string",
        "ops": ["equals"],
        "description": "The country name. Only used when the user is making a final guess like 'Is it Japan?'.",
    },
}


@dataclass(frozen=True)
class Query:
    field: str
    op: str
    value: Any | None = None  # None for zero-arg ops like is_empty

    def as_dict(self) -> dict:
        d = {"field": self.field, "op": self.op}
        if self.value is not None:
            d["value"] = self.value
        return d


class InvalidQuery(ValueError):
    """Raised when a query fails schema validation."""


def validate_query(raw: dict) -> Query:
    """
    Validate a dict-shaped query against FIELD_CATALOG.

    Raises InvalidQuery with a human-readable message on any mismatch.
    Returns a Query dataclass on success.
    """
    if not isinstance(raw, dict):
        raise InvalidQuery("query must be a JSON object")

    field = raw.get("field")
    op = raw.get("op")

    if field is None:
        raise InvalidQuery("query.field is required")
    if field not in FIELD_CATALOG:
        raise InvalidQuery(f"unknown field: {field!r}")
    if op not in FIELD_CATALOG[field]["ops"]:
        raise InvalidQuery(
            f"operator {op!r} is not supported on field {field!r}; "
            f"allowed: {FIELD_CATALOG[field]['ops']}"
        )

    # value requirements depend on the operator
    value = raw.get("value")
    if op == "is_empty":
        if value is not None:
            raise InvalidQuery("is_empty takes no value")
    elif op == "in":
        if not isinstance(value, list) or not value:
            raise InvalidQuery("'in' requires a non-empty list value")
    else:
        if value is None:
            raise InvalidQuery(f"operator {op!r} requires a value")

    # allowed-value check where applicable
    allowed = FIELD_CATALOG[field].get("values")
    if allowed and op in ("equals",):
        if value not in allowed:
            raise InvalidQuery(
                f"value {value!r} not in allowed values for {field}: {allowed}"
            )
    if allowed and op == "in":
        bad = [v for v in value if v not in allowed]
        if bad:
            raise InvalidQuery(
                f"values {bad!r} not in allowed values for {field}: {allowed}"
            )

    return Query(field=field, op=op, value=value)


# ---------------------------------------------------------------------------
# Query evaluation
# ---------------------------------------------------------------------------


def evaluate(query: Query, country: dict) -> bool:
    """Evaluate a validated Query against a country record. Returns True/False."""
    field_value = country.get(query.field)

    if query.op == "equals":
        # Case-insensitive for strings, direct compare otherwise.
        if isinstance(field_value, str) and isinstance(query.value, str):
            return field_value.casefold() == query.value.casefold()
        return field_value == query.value

    if query.op == "in":
        return field_value in query.value

    if query.op == "contains":
        if not isinstance(field_value, list):
            return False
        # Case-insensitive membership for string lists (e.g. languages).
        target = query.value
        if isinstance(target, str):
            t = target.casefold()
            return any(isinstance(v, str) and v.casefold() == t for v in field_value)
        return target in field_value

    if query.op == "is_empty":
        return field_value == [] or field_value is None

    raise InvalidQuery(f"operator not implemented: {query.op}")


# ---------------------------------------------------------------------------
# Final-guess fuzzy matching
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(s: str) -> str:
    """
    Normalize a country name for loose equality:
    * strip diacritics (São Tomé -> Sao Tome)
    * lowercase
    * collapse any non-alphanumeric run to a single space, then strip
    """
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    return _PUNCT_RE.sub(" ", lowered).strip()


def match_guess(guess: str, country: dict) -> bool:
    """True if `guess` plausibly names `country`."""
    norm_guess = normalize_name(guess)
    if not norm_guess:
        return False
    for alias in country.get("aliases", []):
        if normalize_name(alias) == norm_guess:
            return True
    # Also accept ISO codes ("JPN", "JP") as a convenience.
    if norm_guess.upper() in {country.get("cca2"), country.get("cca3")}:
        return True
    return False


def find_country_by_guess(guess: str, countries: list[dict]) -> dict | None:
    """Return the single country that matches this guess, or None."""
    for c in countries:
        if match_guess(guess, c):
            return c
    return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

MAX_SCORE = 200
PER_QUESTION_PENALTY = 10
MIN_WIN_SCORE = 10


def score_for_win(questions_used: int) -> int:
    """Score awarded when the player correctly guesses the country."""
    raw = MAX_SCORE - PER_QUESTION_PENALTY * questions_used
    return max(MIN_WIN_SCORE, raw)


# ---------------------------------------------------------------------------
# Game setup
# ---------------------------------------------------------------------------

DIFFICULTY_LIMITS: dict[str, int] = {
    "Easy": 7,
    "Normal": 5,
    "Hard": 3,
}


def pick_secret(countries: list[dict], rng: random.Random | None = None) -> dict:
    """Randomly select a country to be the secret. rng injectable for tests."""
    if not countries:
        raise ValueError("no countries to pick from")
    r = rng or random
    return r.choice(countries)


def answer_text(result: bool | None) -> str:
    """Human-readable label for a yes/no/unknown answer."""
    if result is True:
        return "Yes"
    if result is False:
        return "No"
    return "I don't know"
