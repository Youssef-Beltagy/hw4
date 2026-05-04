"""
Tests for the classifier's guardrails.

The Gemini calls are mocked at the `google.genai.Client` boundary so these
tests run offline and are deterministic. The goal here is NOT to test Gemini;
it's to prove that *every* weird output we could plausibly receive from the
model is safely funneled into answer=None by answer_directly().
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import classifier
from classifier import answer_directly


# ---------------------------------------------------------------------------
# Fixtures: a fake `google.genai` module we control completely.
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    text: str


class _FakeModels:
    """Stands in for client.models; returns whatever response we configure."""

    def __init__(self, response_text: str | None, raise_exc: Exception | None = None):
        self._response_text = response_text
        self._raise = raise_exc

    def generate_content(self, **kwargs):
        if self._raise:
            raise self._raise
        return _FakeResponse(text=self._response_text)


class _FakeClient:
    def __init__(self, response_text: str | None = None, raise_exc: Exception | None = None):
        self.models = _FakeModels(response_text, raise_exc)


def _install_fake_genai(monkeypatch, *, response_text=None, raise_exc=None):
    """Install a fake `google.genai` module into sys.modules for this test."""
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = lambda api_key=None: _FakeClient(
        response_text=response_text, raise_exc=raise_exc
    )

    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key")


@pytest.fixture
def secret_country():
    return {"name": "Japan", "cca3": "JPN"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAnswerDirectlyHappyPath:
    def test_yes(self, monkeypatch, secret_country):
        _install_fake_genai(monkeypatch, response_text='{"answer": "yes"}')
        result = answer_directly("Is it famous for sushi?", secret_country)
        assert result.answer is True
        assert result.source == "llm_direct"

    def test_no(self, monkeypatch, secret_country):
        _install_fake_genai(monkeypatch, response_text='{"answer": "no"}')
        assert answer_directly("Does it have a rainforest?", secret_country).answer is False

    def test_unknown(self, monkeypatch, secret_country):
        _install_fake_genai(monkeypatch, response_text='{"answer": "unknown"}')
        assert answer_directly("Any question", secret_country).answer is None


# ---------------------------------------------------------------------------
# Guardrail: every weird response becomes answer=None
# ---------------------------------------------------------------------------


class TestAnswerDirectlyGuardrail:
    """The whole point of the guardrail: bad input -> answer=None, never raises."""

    @pytest.mark.parametrize("bad_text,description", [
        ('{"answer": "maybe"}', "non-enum string value"),
        ('{"answer": ""}', "empty string"),
        ('{"answer": null}', "null value"),
        ('{"answer": true}', "boolean instead of string"),
        ('{"answer": ["yes"]}', "list instead of string"),
        ('{"answer": {"nested": "yes"}}', "nested dict instead of string"),
        ('{"wrong_field": "yes"}', "wrong key name"),
        ('{}', "empty JSON object"),
        ('[]', "array instead of object"),
        ('"yes"', "bare string, not object"),
        ('null', "bare null"),
        ('true', "bare boolean"),
        ('not json at all', "invalid JSON"),
        ('', "empty string response"),
        ('{"answer": "yes", "extra":', "truncated JSON"),
        # Things a chatty model might do despite the schema:
        ('Sure! Here is the answer: {"answer": "yes"}', "preamble before JSON"),
        ('```json\n{"answer": "yes"}\n```', "markdown-fenced"),
    ])
    def test_weird_responses_become_unknown(self, monkeypatch, secret_country, bad_text, description):
        _install_fake_genai(monkeypatch, response_text=bad_text)
        result = answer_directly("q", secret_country)
        assert result.answer is None, f"case ({description}) leaked through: {result!r}"
        assert result.source == "llm_direct"

    def test_network_error_becomes_unknown(self, monkeypatch, secret_country):
        _install_fake_genai(monkeypatch, raise_exc=RuntimeError("network down"))
        result = answer_directly("q", secret_country)
        assert result.answer is None
        assert "network down" in (result.raw or "")

    def test_none_response_text_becomes_unknown(self, monkeypatch, secret_country):
        _install_fake_genai(monkeypatch, response_text=None)
        assert answer_directly("q", secret_country).answer is None

    def test_whitespace_is_tolerated_on_valid_enum(self, monkeypatch, secret_country):
        # We strip+lowercase before the enum check, so padding is fine.
        _install_fake_genai(monkeypatch, response_text='{"answer": "  yes  "}')
        assert answer_directly("q", secret_country).answer is True


# ---------------------------------------------------------------------------
# Preconditions: missing inputs short-circuit without calling the API
# ---------------------------------------------------------------------------


class TestAnswerDirectlyPreconditions:
    def test_missing_api_key(self, monkeypatch, secret_country):
        # Force a scenario where no key is available at all.
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        # Intentionally don't install the fake genai; the function must NOT import it.
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        result = answer_directly("q", secret_country)
        assert result.answer is None
        assert result.raw == "no api key"

    def test_empty_question(self, monkeypatch, secret_country):
        monkeypatch.setenv("GOOGLE_API_KEY", "fake")
        assert answer_directly("", secret_country).answer is None
        assert answer_directly("   ", secret_country).answer is None
