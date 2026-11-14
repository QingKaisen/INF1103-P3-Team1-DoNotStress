"""Gemini calculates the stress average from raw answers. These tests never call the API.

ai_manager holds no scoring rules. Where a test needs them it takes them from
logic_manager, exactly as main.process_checkin does.
"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_manager
import io_manager
import logic_manager
import survey
from plain_tests import capture_logs, function_suite


from logic_manager import LogicManager as _LogicManager
_LOGIC = _LogicManager()
from io_manager import IOManager as _IOManager
_IO = _IOManager()
from make_ai import analyse_student, call_gemini, build_prompt, validate_response

def _checkin():
    """A full record with scores and a reflection Gemini must not see."""
    return {
        "student_id": "2605581",
        "feelings_text": "PRIVATE REFLECTION that must stay off the prompt",
        "survey_version": survey.VERSION,
        "pss_1": 4,
        "pss_2": 2,
        "pss_3": 5,
        "pss_4": 3,
        "sleep_hours_avg": 6.5,
        "sleep_quality": 4,
        "pas_workload": 5,
        "pas_catchup": 2,
        "fin_stress": 1,
        "mspss_friends": 2,
        "mspss_family": 5,
        "stress_score": 9.99,
        "scored_item_count": 11,
        "support_mean": 1.25,
        "support_item_count": 2,
        "context_flags": {"SENTINEL_FLAG": True},
        "risk_category": "SENTINEL_CATEGORY",
        "soft_label": "SENTINEL_SOFT_LABEL",
        "speak_prominence": "SENTINEL_PROMINENCE",
        "safety_flag": True,
        "risk_score": 0.99,
    }


def _gemini(**overrides):
    """A Moderate reply. Overrides replace fields; they do not fill gaps."""
    payload = {
        "stress_score": 3.0,
        "risk_category": "Moderate",
        "primary_stressors": ["academic_overload"],
        "scored_items": {key: 3 for key in ai_manager.RAW_ANSWER_KEYS},
        "confidence": 0.8,
        "reasoning": (
            "School looks a bit heavy right now. "
            "A smaller plan for the week could take some of the pressure off."
        ),
        "soft_label": "Worth a check-in",
        "tips": ["workload_chunks", "short_breaks"],
        "speak_prominence": "medium",
    }
    payload.update(overrides)
    return payload


def _for_score(score, **overrides):
    """Tier fields that match score, plus any extra overrides."""
    if score < 2.5:
        category, label, prominence = "Low", "You're doing ok", "low"
    elif score <= 3.5:
        category, label, prominence = "Moderate", "Worth a check-in", "medium"
    else:
        category, label, prominence = "High", "Please reach out", "high"
    payload = _gemini(
        stress_score=score,
        risk_category=category,
        soft_label=label,
        speak_prominence=prominence,
    )
    payload.update(overrides)
    return payload


def _expected(answers):
    """What Gemini should return for these answers, from logic_manager (the formula's only owner)."""
    return _LOGIC.scored_items(answers), _LOGIC.score_answers(answers)["stress_score"]


def _all_threes():
    """Answers that score 3 on every item: stress_score 3.0 (Moderate)."""
    answers = {key: 3 for key in ai_manager.RAW_ANSWER_KEYS}
    answers["sleep_hours_avg"] = 6.5
    return answers


def _input_payload(prompt):
    """The JSON object at the end of the prompt is the only student data."""
    start = prompt.rfind("{")
    return json.loads(prompt[start:])


# --- Prompt ----------------------------------------------------------------

def test_prompt_has_raw_answers_and_wording_only():
    prompt = build_prompt(_checkin(), instructions=_LOGIC.scoring_instructions())
    payload = _input_payload(prompt)

    assert set(payload) == set(ai_manager.STUDENT_PROMPT_FIELDS)
    assert payload["pss_1"] == 4
    assert payload["pss_2"] == 2
    assert payload["sleep_hours_avg"] == 6.5
    assert payload["mspss_family"] == 5
    assert payload["survey_version"] == survey.VERSION
    for banned in (
        "student_id",
        "feelings_text",
        "stress_score",
        "context_flags",
        "risk_category",
        "soft_label",
        "speak_prominence",
        "support_mean",
        "scored_item_count",
        "risk_score",
        "safety_flag",
    ):
        assert banned not in payload

    assert survey.QUESTION_MAP["pss_1"]["prompt"] in prompt
    assert survey.QUESTION_MAP["fin_stress"]["prompt"] in prompt
    assert "1 = Never" in prompt
    assert "5 = Very often" in prompt
    assert "1 = No stress at all" in prompt
    assert "5 = Overwhelming stress" in prompt
    assert "half-hour" in prompt
    assert "positively worded" in prompt.lower()
    assert "Talk to someone" in prompt
    assert "SIT Counselling" in prompt

    assert "2605581" not in prompt
    assert "PRIVATE REFLECTION" not in prompt
    assert "student_id" not in prompt
    assert "feelings_text" not in prompt
    assert "context_flags" not in prompt
    assert "SENTINEL_FLAG" not in prompt
    assert "SENTINEL_CATEGORY" not in prompt
    assert "SENTINEL_SOFT_LABEL" not in prompt
    assert "SENTINEL_PROMINENCE" not in prompt
    assert "9.99" not in prompt
    assert "support_mean" not in prompt
    assert "risk_score" not in prompt
    assert "Speak to advisor" not in prompt
    assert "advisor dashboard" not in prompt
    assert "emotional_distress" not in prompt
    assert "feelings_check_in" not in prompt
    assert "6 - answer" in prompt
    assert "scored_items" in prompt
    assert "rounded to 2 decimal places" in prompt
    assert "8 or more hours -> 1" in prompt
    assert "otherwise 5" in prompt
    assert "estimate" not in prompt.lower()


def test_ai_manager_holds_no_scoring_rules():
    """The formula and tier cut-offs live only in logic_manager (brief: no domain logic here)."""
    for name in ("expected_stress_score", "LOW_BELOW", "HIGH_ABOVE", "POSITIVE_ITEM_KEYS",
                 "_category_for_stress_score", "_tier_consistency_error", "_formula_mismatch",
                 "_risk_score_from_stress", "_RECOMMENDED_SUPPORT"):
        assert not hasattr(ai_manager, name), name
    assert not hasattr(ai_manager, "logic_manager")

    bare = build_prompt(_checkin())
    assert "6 - answer" not in bare
    assert "below 2.5" not in bare
    with_rules = build_prompt(_checkin(), instructions=_LOGIC.scoring_instructions())
    assert _LOGIC.scoring_instructions() in with_rules

    # Schema only: a well-formed reply with the wrong tier still passes here.
    wrong_tier = _for_score(2.0, risk_category="High", soft_label="Please reach out",
                            speak_prominence="high")
    ok, result = validate_response(wrong_tier)
    assert ok, result
    assert "risk_score" not in result
    assert "recommended_support" not in result


def test_default_wording_matches_the_questionnaire():
    assert not hasattr(ai_manager, "survey")
    assert [item["key"] for item in ai_manager.DEFAULT_QUESTIONS] == [item["key"] for item in survey.QUESTIONS]
    for default, live in zip(ai_manager.DEFAULT_QUESTIONS, survey.QUESTIONS):
        assert default["prompt"] == live["prompt"]
        assert list(default.get("options") or []) == list(live.get("options") or [])


def test_caller_can_pass_question_wording():
    seen = {}

    def generate_fn(prompt, api_key, model_name):
        seen["prompt"] = prompt
        scored, stress = _expected(_checkin())
        return json.dumps(_for_score(
            stress, scored_items=scored, tips=["short_breaks"], primary_stressors=[],
        ))

    ok, enriched, error = analyse_student(
        _checkin(),
        api_key="stub",
        max_attempts=1,
        retry_delay_sec=0,
        generate_fn=generate_fn,
        questions=[{
            "key": "pss_1",
            "prompt": "UNIQUE WORDING XYZ",
            "options": ["No", "Yes"],
        }],
    )
    assert ok, error
    assert "UNIQUE WORDING XYZ" in seen["prompt"]
    assert "1 = No" in seen["prompt"]
    assert "2605581" not in seen["prompt"]
    scored, stress = _expected(_checkin())
    assert enriched["source"] == "gemini"
    assert enriched["stress_score"] == stress
    assert enriched["scored_items"] == scored


# --- Response Validation ---------------------------------------------------

def test_valid_response_is_normalised():
    supplied = _for_score(2.5)
    supplied["risk_score"] = 0.99
    supplied["recommended_support"] = "Speak to advisor"
    ok, result = validate_response(json.dumps(supplied))
    assert ok, result
    assert result["stress_score"] == 2.5
    assert result["risk_category"] == "Moderate"
    assert result["soft_label"] == "Worth a check-in"
    assert result["speak_prominence"] == "medium"
    # Extra keys from Gemini are dropped. Logic works these two out.
    assert "risk_score" not in result
    assert "recommended_support" not in result

    for score in (1.0, 3.0, 5.0):
        ok, result = validate_response(_for_score(score))
        assert ok, result
        assert result["stress_score"] == score


def test_band_edges_and_stripped_category():
    for score in (1, 2.49, 2.5, 3.5, 3.51, 5):
        ok, result = validate_response(_for_score(score))
        assert ok, (score, result)
        assert result["stress_score"] == float(score)

    padded = _for_score(3.0, risk_category="  Moderate  ")
    ok, result = validate_response(padded)
    assert ok, result
    assert result["risk_category"] == "Moderate"


def test_out_of_range_stress_score_fails():
    for bad in (0.9, 5.1, 0, 6, -1, True, False, None, "high", "",
                float("nan"), float("inf"), float("-inf")):
        ok, message = validate_response(_for_score(3.0, stress_score=bad))
        assert not ok, bad
        assert "stress_score" in message


def test_tier_mismatch_fails_the_logic_check():
    """A reply whose tier disagrees with its score is retried, then rejected.

    The tier rule is logic_manager's; ai_manager runs it through verify_fn.
    """
    low_score_high_tier = _for_score(
        2.0,
        risk_category="High",
        soft_label="Please reach out",
        speak_prominence="high",
    )
    label_mismatch = _for_score(
        4.0,
        risk_category="High",
        soft_label="You're doing ok",
        speak_prominence="high",
    )
    prominence_mismatch = _for_score(
        4.0,
        risk_category="High",
        soft_label="Please reach out",
        speak_prominence="low",
    )
    for reply in (low_score_high_tier, label_mismatch, prominence_mismatch):
        assert validate_response(reply)[0]

        def generate_fn(prompt, api_key, model_name, reply=reply):
            return json.dumps(reply)

        ok, result, error = call_gemini(
            "check-in", api_key="stub", max_attempts=1, retry_delay_sec=0,
            generate_fn=generate_fn, answers=_all_threes(),
            verify_fn=_LOGIC.verify_ai_scores,
        )
        assert not ok
        assert result is None
        assert error["error_code"] == "invalid_response"


def test_removed_ids_are_rejected():
    assert "emotional_distress" not in ai_manager.ALLOWED_PRIMARY_STRESSORS
    assert "feelings_check_in" not in ai_manager.ALLOWED_TIP_IDS
    assert not (validate_response(
        _for_score(3.0, primary_stressors=["emotional_distress"])
    )[0])
    assert not (validate_response(
        _for_score(3.0, primary_stressors=["academic_overload", "emotional_distress"])
    )[0])
    assert not (validate_response(
        _for_score(3.0, tips=["feelings_check_in"])
    )[0])
    assert not (validate_response(
        _for_score(3.0, tips=["short_breaks", "feelings_check_in"])
    )[0])


def test_remaining_tip_ids_still_format():
    assert ai_manager.ALLOWED_TIP_IDS <= set(io_manager.TIPS_ALLOWLIST)
    formatted = _IO.format_tips(sorted(ai_manager.ALLOWED_TIP_IDS))
    assert {item["id"] for item in formatted["items"]} == set(ai_manager.ALLOWED_TIP_IDS)
    legacy = _IO.format_tips(["feelings_check_in"])
    assert legacy["items"][0]["id"] == "feelings_check_in"


# --- Gemini Call -----------------------------------------------------------

def test_mismatch_is_an_invalid_response():
    def generate_fn(prompt, api_key, model_name):
        return json.dumps(_for_score(
            4.2,
            risk_category="Low",
            soft_label="You're doing ok",
            speak_prominence="low",
        ))

    ok, result, error = call_gemini(
        "check-in",
        api_key="stub",
        max_attempts=1,
        retry_delay_sec=0,
        generate_fn=generate_fn,
        answers=_all_threes(),
        verify_fn=_LOGIC.verify_ai_scores,
    )
    assert not ok
    assert result is None
    assert error["error_code"] == "invalid_response"
    assert error["source"] == "gemini"


def test_missing_api_key_does_not_call_generator():
    ok, result, error = call_gemini("check-in", api_key="  ")
    assert not ok
    assert result is None
    assert error["error_code"] == "missing_api_key"
    assert error["source"] == "gemini"

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        ok, result, error = call_gemini("check-in")
    assert not ok
    assert error["error_code"] == "missing_api_key"


def test_structured_schema_is_sent_with_the_mime_type():
    captured = {}

    def fake_generate_content(*, model, contents, config):
        captured["model"] = model
        captured["contents"] = contents
        captured["config"] = config
        return SimpleNamespace(text="{}")

    def fake_client(*, api_key, http_options):
        captured["api_key"] = api_key
        return SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))

    with patch("google.genai.Client", fake_client):
        from ai.gemini_provider import GeminiProvider
        text = GeminiProvider(api_key="secret-key").generate(
            "prompt text", "gemini-3.6-flash", schema=ai_manager.RESPONSE_SCHEMA)

    assert text == "{}"
    assert captured["api_key"] == "secret-key"
    assert captured["model"] == "gemini-3.6-flash"
    config = captured["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] is ai_manager.RESPONSE_SCHEMA
    assert config["temperature"] == 0.0
    assert "risk_score" not in config["response_schema"]["properties"]

    properties = ai_manager.RESPONSE_SCHEMA["properties"]
    assert properties["risk_category"]["enum"] == ["Low", "Moderate", "High"]
    assert properties["soft_label"]["enum"] == list(ai_manager.SOFT_LABELS)
    assert properties["speak_prominence"]["enum"] == ["low", "medium", "high"]
    assert set(properties["primary_stressors"]["items"]["enum"]) == set(ai_manager.ALLOWED_PRIMARY_STRESSORS)
    assert set(properties["tips"]["items"]["enum"]) == set(ai_manager.ALLOWED_TIP_IDS)
    assert "emotional_distress" not in properties["primary_stressors"]["items"]["enum"]
    assert "feelings_check_in" not in properties["tips"]["items"]["enum"]
    scored_items = properties["scored_items"]
    assert scored_items["type"] == "OBJECT"
    assert set(scored_items["required"]) == set(ai_manager.RAW_ANSWER_KEYS)
    assert set(scored_items["properties"]) == set(ai_manager.RAW_ANSWER_KEYS)
    for spec in scored_items["properties"].values():
        assert spec == {"type": "INTEGER", "minimum": 1, "maximum": 5}
    assert "estimate" not in json.dumps(ai_manager.RESPONSE_SCHEMA).lower()

    from google.genai import types
    types.GenerateContentConfig.model_validate({
        "temperature": 0.0,
        "response_mime_type": "application/json",
        "response_schema": ai_manager.RESPONSE_SCHEMA,
        "automatic_function_calling": {"disable": True},
    })


def _minimum_stress():
    """Answers that convert to 1 on every item. stress_score is 1.00."""
    return {
        "pss_1": 1,
        "pss_2": 5,
        "pss_3": 5,
        "pss_4": 1,
        "sleep_hours_avg": 8,
        "sleep_quality": 1,
        "pas_workload": 1,
        "pas_catchup": 1,
        "fin_stress": 1,
        "mspss_friends": 5,
        "mspss_family": 5,
        "survey_version": "evidence-v3",
    }


def _exact_reply(answers, **overrides):
    scored, stress = _expected(answers)
    payload = _for_score(stress, scored_items=scored)
    payload.update(overrides)
    return payload


# --- Formula check ---------------------------------------------------------
# The formula itself is tested in test_logic_manager. These tests check that
# ai_manager runs logic_manager's verify_ai_scores on every reply it is given.

def test_exact_response_passes():
    answers = _minimum_stress()
    ok, result = validate_response(_exact_reply(answers))
    assert ok, result
    assert _LOGIC.verify_ai_scores(result, answers) is None
    assert result["scored_items"] == {key: 1 for key in ai_manager.RAW_ANSWER_KEYS}
    assert result["stress_score"] == 1.0
    assert result["risk_category"] == "Low"

    def generate_fn(prompt, api_key, model_name):
        return json.dumps(_exact_reply(answers))

    ok, enriched, error = analyse_student(
        answers, api_key="stub", max_attempts=1, retry_delay_sec=0, generate_fn=generate_fn,
        instructions=_LOGIC.scoring_instructions(),
        verify_fn=_LOGIC.verify_ai_scores,
    )
    assert ok, error
    assert enriched["stress_score"] == 1.0
    assert enriched["scored_items"]["sleep_hours_avg"] == 1


def test_off_by_one_scored_item_fails():
    answers = _minimum_stress()
    reply = _exact_reply(answers)
    reply["scored_items"] = dict(reply["scored_items"])
    reply["scored_items"]["pss_1"] = 2
    message = _LOGIC.verify_ai_scores(reply, answers)
    assert "scored_items" in message

    def generate_fn(prompt, api_key, model_name):
        return json.dumps(reply)

    ok, result, error = call_gemini(
        "check-in", api_key="stub", max_attempts=1, retry_delay_sec=0,
        generate_fn=generate_fn, answers=answers, verify_fn=_LOGIC.verify_ai_scores,
    )
    assert not ok
    assert result is None
    assert error["error_code"] == "invalid_response"

    # Without the check, ai_manager only validates the schema.
    skipped, _ = validate_response(reply)
    assert skipped
    ok, _, error = call_gemini(
        "check-in", api_key="stub", max_attempts=1, retry_delay_sec=0,
        generate_fn=generate_fn, answers=answers,
    )
    assert ok, error


def test_stress_score_off_by_a_tenth_fails():
    answers = _minimum_stress()
    reply = _exact_reply(answers, stress_score=1.1)
    message = _LOGIC.verify_ai_scores(reply, answers)
    assert "stress_score" in message

    def generate_fn(prompt, api_key, model_name):
        return json.dumps(reply)

    ok, result, error = analyse_student(
        answers, api_key="stub", max_attempts=1, retry_delay_sec=0, generate_fn=generate_fn,
        instructions=_LOGIC.scoring_instructions(),
        verify_fn=_LOGIC.verify_ai_scores,
    )
    assert not ok
    assert error["error_code"] == "invalid_response"


def test_a_broken_check_is_an_invalid_response_not_a_crash():
    def broken_check(ai_fields, answers):
        raise ValueError("bug in the check")

    def generate_fn(prompt, api_key, model_name):
        return json.dumps(_for_score(3.0))

    ok, result, error = call_gemini(
        "check-in", api_key="stub", max_attempts=1, retry_delay_sec=0,
        generate_fn=generate_fn, answers=_all_threes(), verify_fn=broken_check,
    )
    assert not ok
    assert result is None
    assert error["error_code"] == "invalid_response"


def api_fault(message, code=None):
    """Build the error a fake Gemini raises, with an optional HTTP status code."""
    error = RuntimeError(message)
    error.code = code
    return error


def _ok_body():
    return json.dumps(_for_score(3.0))


# --- Fallback --------------------------------------------------------------

def _call(generate, **overrides):
    """Call call_gemini with the fake generator, a stub key and no retry delay."""
    options = dict(
        api_key="stub-key",
        max_attempts=3,
        retry_delay_sec=0,
        generate_fn=generate,
    )
    options.update(overrides)
    return call_gemini("check-in", **options)


def test_model_order_and_call_cap():
    assert ai_manager.MODEL_CHAIN == ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite")
    assert "gemini-flash-latest" not in ai_manager.MODEL_CHAIN
    assert ai_manager.MAX_TOTAL_CALLS == 4


def test_timeout_then_success_retries_the_same_model():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        if len(calls) == 1:
            raise api_fault("timed out")
        return _ok_body()

    ok, result, error = _call(generate)
    assert ok, error
    assert error is None
    assert result["source"] == "gemini"
    assert calls == ["gemini-3.6-flash", "gemini-3.6-flash"]


def test_503_on_first_model_falls_back():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        if model_name == "gemini-3.6-flash":
            raise api_fault("503 unavailable high demand", 503)
        return _ok_body()

    ok, result, error = _call(generate)
    assert ok, error
    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash"]


def test_404_falls_back_without_retrying_the_same_model():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        if len(calls) == 1:
            raise api_fault("404 not found", 404)
        return _ok_body()

    ok, _, error = _call(generate)
    assert ok, error
    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash"]


def test_429_moves_to_the_next_model_without_a_retry():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        if len(calls) == 1:
            raise api_fault("429 too many requests", 429)
        return _ok_body()

    ok, _, error = _call(generate)
    assert ok, error
    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash"]


def test_every_model_rate_limited():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        raise api_fault("resource exhausted quota", 429)

    ok, result, error = _call(generate)
    assert not ok
    assert result is None
    assert error["error_code"] == "rate_limited"
    assert calls == list(ai_manager.MODEL_CHAIN)
    copy = _IO.format_ai_error("rate_limited")
    assert copy["error_code"] == "rate_limited"
    assert copy["body"] == ("Our check-in assistant is busy right now. "
        "Your answers have been saved and will be reviewed soon.")
    assert copy["error"] is None


def test_bad_json_is_invalid_after_the_call_cap():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        return "not json"

    ok, result, error = _call(generate)
    assert not ok
    assert result is None
    assert error["error_code"] == "invalid_response"
    assert len(calls) == ai_manager.MAX_TOTAL_CALLS


def test_missing_key_never_calls_the_generator():
    ok, result, error = call_gemini("check-in", api_key="  ")
    assert not ok
    assert result is None
    assert error["error_code"] == "missing_api_key"


def test_total_calls_never_exceed_the_cap():
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        raise api_fault("timed out")

    ok, _, error = _call(generate)
    assert not ok
    assert error["error_code"] == "timeout"
    assert len(calls) <= ai_manager.MAX_TOTAL_CALLS
    assert len(calls) == ai_manager.MAX_TOTAL_CALLS
    assert max(calls.count(name) for name in calls) <= 2


def test_api_key_is_absent_from_logs_and_error_detail():
    secret = "sk-live-DO-NOT-PRINT-991122"
    calls = []

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        raise api_fault(f"403 permission denied using {api_key}", 403)

    with capture_logs("ai_manager", level="DEBUG") as captured:
        ok, result, error = _call(generate, api_key=secret)
    assert not ok
    assert result is None
    assert error["error_code"] != "missing_api_key"
    blob = "\n".join(captured.output) + "\n" + error["detail"]
    assert secret not in blob
    assert "[redacted]" in error["detail"]
    assert len(calls) <= ai_manager.MAX_TOTAL_CALLS


def test_rate_limit_still_saves_a_pending_checkin():
    import tempfile
    from pathlib import Path

    from main import create_app

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "records.json"

        def generate(prompt, api_key, model_name):
            raise api_fault("429 quota", 429)

        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only",
            "AI_GENERATE_FN": generate,
            "AI_MAX_ATTEMPTS": 3,
            "AI_RETRY_DELAY_SEC": 0,
            "DATA_PATH": str(path),
        })
        client = app.test_client()
        client.get("/")
        with client.session_transaction() as session:
            token = session["csrf_token"]
        values = dict(
            student_id="2605581", pss_1=3, pss_2=3, pss_3=3, pss_4=3,
            sleep_hours_avg=6.5, sleep_quality=3, pas_workload=3, pas_catchup=3,
            fin_stress=3, mspss_friends=3, mspss_family=3,
            feelings_text="PRIVATE NOTE that must not be stored",
        )
        response = client.post("/", json=values, headers={"X-CSRF-Token": token})
        assert response.status_code == 503
        assert response.json["error_code"] == "rate_limited"
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored[0]["status"] == "pending"
        assert "feelings_text" not in stored[0]
        assert "PRIVATE NOTE" not in path.read_text(encoding="utf-8")


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
