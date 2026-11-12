"""AIFailure, AIResult and ErrorClassifier on their own, without AIManager."""
import unittest

from ai.ai_failure import AIFailure
from ai.ai_result import AIResult
from ai.error_classifier import ErrorClassifier
from plain_tests import case, function_suite


def api_fault(message, code=None):
    """An error with an optional HTTP status code, as a provider would raise."""
    error = RuntimeError(message)
    error.code = code
    return error


def test_failure_codes_are_normalised_and_results_are_one_or_the_other():
    assert AIFailure("made_up").code == "unavailable"
    assert AIFailure("timeout", "slow").as_dict() == {
        "error_code": "timeout", "detail": "slow", "source": "gemini"}
    assert AIFailure("timeout", "slow").detail == "slow"
    assert AIFailure("timeout").source == "gemini"
    assert AIResult(fields={"a": 1}).ok
    assert AIResult(fields={"a": 1}).fields == {"a": 1}
    assert AIResult(fields={"a": 1}).failure is None
    failure = AIFailure("timeout")
    assert not AIResult(failure=failure).ok
    assert AIResult(failure=failure).failure is failure
    assert AIResult(failure=failure).fields is None
    assert not AIResult().ok
    for code in AIFailure.CODES:
        with case(code=code):
            assert AIFailure(code).code == code


def test_error_classifier_maps_messages_and_status_codes():
    classifier = ErrorClassifier()
    assert classifier.classify(TimeoutError("deadline exceeded")) == "timeout"
    assert classifier.classify("GEMINI_API_KEY is missing") == "missing_api_key"
    assert classifier.classify(api_fault("slow down", 429)) == "rate_limited"
    assert classifier.classify("malformed JSON body") == "invalid_response"
    assert classifier.classify("retries exhausted") == "retries_exhausted"
    assert classifier.classify(None) == "unavailable"
    assert classifier.classify(ConnectionError("network unreachable")) == (
        "unavailable")
    extras = (
        ("schema validation failed", "invalid_response"),
        ("resource exhausted", "rate_limited"),
        ("timed out waiting", "timeout"),
        ("gemini_api_key is not set", "missing_api_key"),
    )
    for message, code in extras:
        with case(message=message):
            assert classifier.classify(message) == code


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's tests."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
