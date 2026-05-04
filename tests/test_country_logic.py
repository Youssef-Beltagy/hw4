"""Tests for the deterministic game core in country_logic.py."""

from __future__ import annotations

import random

import pytest

from country_logic import (
    DIFFICULTY_LIMITS,
    FIELD_CATALOG,
    InvalidQuery,
    Query,
    answer_text,
    evaluate,
    find_country_by_guess,
    load_countries,
    match_guess,
    normalize_name,
    pick_secret,
    score_for_win,
    validate_query,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def countries():
    """The full vendored dataset."""
    return load_countries()


@pytest.fixture
def japan(countries):
    return _by_code(countries, "JPN")


@pytest.fixture
def france(countries):
    return _by_code(countries, "FRA")


@pytest.fixture
def nepal(countries):
    return _by_code(countries, "NPL")


def _by_code(countries, cca3):
    for c in countries:
        if c["cca3"] == cca3:
            return c
    raise AssertionError(f"test fixture: {cca3} missing from dataset")


# ---------------------------------------------------------------------------
# Dataset integrity
# ---------------------------------------------------------------------------


class TestDataset:
    def test_has_193_un_members(self, countries):
        assert len(countries) == 193

    def test_records_have_required_fields(self, countries):
        required = {
            "name", "cca2", "cca3", "aliases", "region", "languages",
            "borders", "landlocked", "is_island", "area_bucket",
            "population_bucket", "hemisphere_ns", "hemisphere_ew",
        }
        for c in countries:
            assert required.issubset(c.keys()), f"{c.get('cca3')} missing fields"

    def test_cca3_is_unique(self, countries):
        codes = [c["cca3"] for c in countries]
        assert len(codes) == len(set(codes))

    def test_guinea_bissau_present(self, countries):
        # Regression: upstream REST Countries marks GNB unMember=false; we patch it.
        gnb = _by_code(countries, "GNB")
        assert gnb["name"] == "Guinea-Bissau"


# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------


class TestValidateQuery:
    def test_valid_equals(self):
        q = validate_query({"field": "region", "op": "equals", "value": "Asia"})
        assert q == Query("region", "equals", "Asia")

    def test_valid_contains_list(self):
        q = validate_query({"field": "languages", "op": "contains", "value": "Japanese"})
        assert q.field == "languages"

    def test_valid_is_empty(self):
        q = validate_query({"field": "borders", "op": "is_empty"})
        assert q.value is None

    def test_valid_in(self):
        q = validate_query({"field": "area_bucket", "op": "in", "value": ["large", "very_large"]})
        assert q.value == ["large", "very_large"]

    def test_rejects_unknown_field(self):
        with pytest.raises(InvalidQuery, match="unknown field"):
            validate_query({"field": "gdp", "op": "equals", "value": 1})

    def test_rejects_wrong_operator(self):
        with pytest.raises(InvalidQuery, match="not supported"):
            validate_query({"field": "region", "op": "contains", "value": "Asia"})

    def test_rejects_missing_value(self):
        with pytest.raises(InvalidQuery, match="requires a value"):
            validate_query({"field": "region", "op": "equals"})

    def test_rejects_bad_value_for_enum(self):
        with pytest.raises(InvalidQuery, match="not in allowed values"):
            validate_query({"field": "region", "op": "equals", "value": "Middle Earth"})

    def test_rejects_empty_in_list(self):
        with pytest.raises(InvalidQuery, match="non-empty list"):
            validate_query({"field": "area_bucket", "op": "in", "value": []})

    def test_rejects_is_empty_with_value(self):
        with pytest.raises(InvalidQuery, match="takes no value"):
            validate_query({"field": "borders", "op": "is_empty", "value": []})

    def test_rejects_non_dict(self):
        with pytest.raises(InvalidQuery):
            validate_query("not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Query evaluation
# ---------------------------------------------------------------------------


class TestEvaluateEquals:
    def test_region_match(self, japan):
        assert evaluate(Query("region", "equals", "Asia"), japan) is True

    def test_region_mismatch(self, japan):
        assert evaluate(Query("region", "equals", "Europe"), japan) is False

    def test_region_case_insensitive(self, japan):
        assert evaluate(Query("region", "equals", "asia"), japan) is True

    def test_landlocked_true(self, nepal):
        assert evaluate(Query("landlocked", "equals", True), nepal) is True

    def test_landlocked_false(self, japan):
        assert evaluate(Query("landlocked", "equals", True), japan) is False


class TestEvaluateContains:
    def test_language_present(self, japan):
        assert evaluate(Query("languages", "contains", "Japanese"), japan) is True

    def test_language_absent(self, japan):
        assert evaluate(Query("languages", "contains", "French"), japan) is False

    def test_language_case_insensitive(self, japan):
        assert evaluate(Query("languages", "contains", "japanese"), japan) is True

    def test_borders_contains(self, france):
        # France borders Germany (DEU), Spain (ESP), etc.
        assert evaluate(Query("borders", "contains", "DEU"), france) is True

    def test_borders_does_not_contain(self, france):
        assert evaluate(Query("borders", "contains", "JPN"), france) is False


class TestEvaluateInAndIsEmpty:
    def test_area_in(self, japan):
        # Japan is 'large' in our dataset-relative bucketing.
        assert evaluate(Query("area_bucket", "in", ["large", "very_large"]), japan) is True

    def test_area_in_miss(self, japan):
        assert evaluate(Query("area_bucket", "in", ["small"]), japan) is False

    def test_borders_is_empty_for_island(self, japan):
        assert evaluate(Query("borders", "is_empty"), japan) is True

    def test_borders_is_empty_for_continental(self, france):
        assert evaluate(Query("borders", "is_empty"), france) is False


class TestEvaluateIsIsland:
    def test_japan_is_island(self, japan):
        assert evaluate(Query("is_island", "equals", True), japan) is True

    def test_france_not_island(self, france):
        assert evaluate(Query("is_island", "equals", True), france) is False


# ---------------------------------------------------------------------------
# Name normalization and guess matching
# ---------------------------------------------------------------------------


class TestNormalizeName:
    @pytest.mark.parametrize("inp,expected", [
        ("Japan", "japan"),
        ("  Japan  ", "japan"),
        ("São Tomé and Príncipe", "sao tome and principe"),
        ("Côte d'Ivoire", "cote d ivoire"),
        ("United-States", "united states"),
        ("U.S.A.", "u s a"),
    ])
    def test_normalization(self, inp, expected):
        assert normalize_name(inp) == expected

    def test_empty_is_empty(self):
        assert normalize_name("") == ""


class TestMatchGuess:
    def test_exact_match(self, japan):
        assert match_guess("Japan", japan) is True

    def test_alias_match(self, japan):
        # Japan's aliases include "Nippon"
        assert match_guess("Nippon", japan) is True

    def test_case_insensitive(self, japan):
        assert match_guess("JAPAN", japan) is True

    def test_diacritics_stripped(self, countries):
        stp = _by_code(countries, "STP")
        assert match_guess("Sao Tome and Principe", stp) is True

    def test_iso_code(self, japan):
        assert match_guess("JPN", japan) is True
        assert match_guess("JP", japan) is True

    def test_wrong_country(self, japan, france):
        assert match_guess("France", japan) is False

    def test_empty_guess(self, japan):
        assert match_guess("", japan) is False


class TestFindCountryByGuess:
    def test_finds_by_common_name(self, countries):
        match = find_country_by_guess("Germany", countries)
        assert match is not None and match["cca3"] == "DEU"

    def test_finds_via_alias(self, countries):
        # United States has aliases like "United States of America"
        match = find_country_by_guess("United States of America", countries)
        assert match is not None and match["cca3"] == "USA"

    def test_no_match(self, countries):
        assert find_country_by_guess("Wakanda", countries) is None

    def test_diacritics(self, countries):
        match = find_country_by_guess("Cote d'Ivoire", countries)
        assert match is not None and match["cca3"] == "CIV"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoreForWin:
    @pytest.mark.parametrize("q,expected", [
        (0, 200),    # impossible but defined
        (1, 190),
        (3, 170),    # Hard-mode max questions
        (5, 150),    # Normal-mode max questions
        (7, 130),    # Easy-mode max questions
        (10, 100),
        (19, 10),    # well past any difficulty, clamped
        (20, 10),
    ])
    def test_score_curve(self, q, expected):
        assert score_for_win(q) == expected


# ---------------------------------------------------------------------------
# Game setup helpers
# ---------------------------------------------------------------------------


class TestPickSecret:
    def test_returns_country_from_list(self, countries):
        secret = pick_secret(countries, rng=random.Random(42))
        assert secret in countries

    def test_deterministic_with_seed(self, countries):
        a = pick_secret(countries, rng=random.Random(0))
        b = pick_secret(countries, rng=random.Random(0))
        assert a["cca3"] == b["cca3"]

    def test_different_seeds_likely_differ(self, countries):
        # Not strictly guaranteed but extremely likely with 193 countries.
        picks = {pick_secret(countries, rng=random.Random(s))["cca3"] for s in range(20)}
        assert len(picks) > 1

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            pick_secret([])


class TestDifficultyLimits:
    def test_ordering(self):
        assert DIFFICULTY_LIMITS["Easy"] > DIFFICULTY_LIMITS["Normal"] > DIFFICULTY_LIMITS["Hard"]

    def test_all_present(self):
        assert set(DIFFICULTY_LIMITS.keys()) == {"Easy", "Normal", "Hard"}


class TestAnswerText:
    def test_yes(self):
        assert answer_text(True) == "Yes"

    def test_no(self):
        assert answer_text(False) == "No"

    def test_unknown(self):
        assert answer_text(None) == "I don't know"


# ---------------------------------------------------------------------------
# Sanity check on the schema itself
# ---------------------------------------------------------------------------


class TestFieldCatalog:
    def test_name_field_is_for_final_guess(self):
        assert "equals" in FIELD_CATALOG["name"]["ops"]

    def test_every_field_has_description(self):
        for field, meta in FIELD_CATALOG.items():
            assert meta.get("description"), f"{field} missing description"
            assert meta.get("ops"), f"{field} missing ops"
