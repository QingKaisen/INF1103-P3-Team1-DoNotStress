"""Regression tests for the OOP AIManager and AIProvider contract.

All tests use fake providers and are safe to run without the Gemini SDK,
Flask or an API key. Run via unittest discovery like the existing suite.
"""

from __future__ import annotations

import json
import unittest

from ai.ai_provider import AIProvider
from ai.function_provider import FunctionProvider
from ai.score_auditor import ScoreAuditor
from ai.validators.response_validator import ResponseValidator
from ai_manager import AIManager, MAX_TOTAL_CALLS, RESPONSE_SCHEMA
from fake_provider import FakeProvider
from logic.stress_scorer import StressScorer
from plain_tests import function_suite


ANSWERS = {
    "student_id": "2605581",
    "feelings_text": "PRIVATE REFLECTION: do not send to AI",
    "survey_version": "evidence-v3",
    "pss_1": 3,
    "pss_2": 3,
    "pss_3": 3,
    "pss_4": 3,
    "sleep_hours_avg": 6.5,
    "sleep_quality": 3,
    "pas_workload": 3,
    "pas_catchup": 3,
    "fin_stress": 3,
    "mspss_friends": 3,
    "mspss_family": 3,
}


def _reply(scorer: StressScorer) -> str:
    score = scorer.score(ANSWERS)
    return json.dumps({
        "scored_items": scorer.scored_items(ANSWERS),
        "stress_score": score.stress_score,
        "risk_category": "Moderate",
        "primary_stressors": ["academic_overload"],
        "confidence": 0.8,
        "reasoning": "School can feel busy. Small breaks may help.",
        "soft_label": "Worth a check-in",
        "tips": ["short_breaks"],
        "speak_prominence": "medium",
    })


def test_manager_uses_injected_provider_and_auditor_end_to_end():
    scorer = StressScorer()
    provider = FakeProvider(_reply(scorer), models=("fake-1",))
    manager = AIManager(provider, auditor=scorer, retry_delay_sec=0)
    assert isinstance(manager.provider, AIProvider)
    assert isinstance(scorer, ScoreAuditor)

    ok, result, error = manager.analyse_student(ANSWERS)
    assert ok and error is None
    assert result["source"] == "gemini"
    assert result["stress_score"] == 3.0
    assert result["student_id"] == ANSWERS["student_id"]
    assert result["soft_label"] == "Worth a check-in"
    assert len(provider.calls) == 1
    sent_prompt, model = provider.calls[0]
    assert model == "fake-1"
    assert scorer.instructions() in sent_prompt
    assert "PRIVATE REFLECTION" not in sent_prompt
    assert "2605581" not in sent_prompt


def test_invalid_json_recovers_on_same_model_and_checks_schema():
    scorer = StressScorer()
    provider = FakeProvider("{bad json", _reply(scorer), models=("fake-1",))
    manager = AIManager(provider, auditor=scorer, max_attempts=2, retry_delay_sec=0)
    from unittest.mock import patch
    with patch.object(provider, "generate", wraps=provider.generate) as generated:
        ok, fields, err = manager.call_api("prompt", answers=ANSWERS)
    assert ok and err is None
    assert fields["stress_score"] == 3.0
    assert provider.models_called == ["fake-1", "fake-1"]
    assert generated.call_args.kwargs["schema"] is RESPONSE_SCHEMA


def test_rate_limit_falls_back_without_retrying_same_model():
    scorer = StressScorer()
    provider = FakeProvider(RuntimeError("429 quota exhausted"), _reply(scorer),
                            models=("fake-1", "fake-2"))
    manager = AIManager(provider, auditor=scorer, max_attempts=3, retry_delay_sec=0)
    ok, data, err = manager.call_api("prompt", answers=ANSWERS)
    assert ok and err is None
    assert data["stress_score"] == 3.0
    assert provider.models_called == ["fake-1", "fake-2"]


def test_total_call_limit_and_no_fabricated_result():
    provider = FakeProvider("bad json", models=("a", "b", "c"))
    manager = AIManager(provider, max_attempts=99, retry_delay_sec=0)
    ok, enriched, err = manager.analyse_student(ANSWERS)
    assert not ok and enriched is None
    assert err["error_code"] == "invalid_response"
    assert len(provider.calls) == MAX_TOTAL_CALLS


def test_unavailable_provider_never_calls_generate():
    provider = FakeProvider("{}", ready=False)
    manager = AIManager(provider, retry_delay_sec=0)
    ok, result, err = manager.analyse_student(ANSWERS)
    assert not ok and result is None
    assert err["error_code"] == "missing_api_key"
    assert provider.calls == []


def test_auditor_rejects_valid_shape_but_wrong_math():
    scorer = StressScorer()
    wrong = json.loads(_reply(scorer))
    wrong["stress_score"] = 3.25
    provider = FakeProvider(json.dumps(wrong), models=("fake-1",))
    manager = AIManager(provider, auditor=scorer, max_attempts=1, retry_delay_sec=0)
    ok, result, err = manager.analyse_student(ANSWERS)
    assert not ok and result is None
    assert err["error_code"] == "invalid_response"


def test_function_provider_preserves_legacy_signature_and_key_redaction():
    secret = "private-demo-key"
    calls = []
    def generate(prompt, api_key, model_name):
        calls.append((prompt, model_name))
        raise RuntimeError(f"access denied for {api_key}")
    provider = FunctionProvider(generate, secret, models=("fake-1",))
    manager = AIManager(provider, max_attempts=1, retry_delay_sec=0)
    ok, result, error = manager.call_api("prompt")
    assert not ok and result is None
    assert secret not in error["detail"]
    assert "[redacted]" in error["detail"]
    assert calls == [("prompt", "fake-1")]



def test_live_gemini_adapter_config_is_preserved_with_a_mock_sdk():
    """The OOP route still sends schema, temperature 0 and a single SDK try."""
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import patch
    from ai.gemini_provider import GeminiProvider

    capture = []
    scorer = StressScorer()
    def fake_generate_content(*, model, contents, config):
        capture.append((model, contents, config))
        return SimpleNamespace(text=_reply(scorer))
    def fake_client(*, api_key, http_options):
        capture.append((api_key, http_options))
        return SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))

    sdk = ModuleType("google.genai")
    sdk.Client = fake_client
    google = ModuleType("google")
    google.__path__ = []
    google.genai = sdk
    with patch.dict(sys.modules, {"google": google, "google.genai": sdk}):
        manager = AIManager(GeminiProvider(api_key="secret"), auditor=scorer,
                            max_attempts=1, retry_delay_sec=0)
        ok, record, err = manager.analyse_student(ANSWERS)

    assert ok and err is None
    assert record["source"] == "gemini"
    client_args, request_args = capture
    assert client_args[0] == "secret"
    assert client_args[1]["retry_options"] == {"attempts": 1}
    assert client_args[1]["timeout"] == 30_000
    model, prompt, config = request_args
    assert model == "gemini-3.6-flash"
    assert "PRIVATE REFLECTION" not in prompt
    assert config["temperature"] == 0.0
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] is RESPONSE_SCHEMA
    assert config["automatic_function_calling"] == {"disable": True}



def test_gemini_empty_reply_is_classified_as_invalid_response():
    """Empty SDK text should follow the original bad-JSON retry path."""
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import patch
    from ai.gemini_provider import GeminiProvider

    sdk = ModuleType("google.genai")
    def fake_client(*, api_key, http_options):
        def generate_content(*, model, contents, config):
            return SimpleNamespace(text="")
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    sdk.Client = fake_client
    google = ModuleType("google")
    google.__path__ = []
    google.genai = sdk
    with patch.dict(sys.modules, {"google": google, "google.genai": sdk}):
        manager = AIManager(GeminiProvider(api_key="demo"), max_attempts=1,
                            retry_delay_sec=0)
        ok, record, err = manager.analyse_student(ANSWERS)
    assert not ok and record is None
    assert err["error_code"] == "invalid_response"


def test_validation_runs_the_response_validator_list():
    """The live path uses the validator objects, not a second copy of the rules."""
    manager = AIManager(FakeProvider("{}", ready=False))
    validators = manager.contract.validators
    assert len(validators) == 9
    assert all(isinstance(item, ResponseValidator) for item in validators)
    assert len({type(item) for item in validators}) >= 6
    assert manager.contract.schema() is RESPONSE_SCHEMA
    ok, message = manager.validate_response({})
    assert not ok and message.startswith("AI response missing required fields:")


def test_invalid_provider_type_is_rejected_at_construction():
    try:
        AIManager(object())
    except TypeError as exc:
        assert "AIProvider" in str(exc)
    else:
        raise AssertionError("An incompatible provider must not be accepted")

def load_tests(loader, tests, pattern):
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
