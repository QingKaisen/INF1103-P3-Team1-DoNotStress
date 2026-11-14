"""
DoNotStress — AI Processing Layer (ai_manager)

Sole owner of Gemini prompt construction, structured JSON API calls,
schema validation, and graceful retries.

Audience: students (local Flask web UI). Gemini calculates the stress
average from the 11 raw answers. This layer holds no domain logic: the
caller (main.py) passes in the scoring instructions for the prompt and a
check function, both from logic_manager, so the rules live in one place.
A reply that fails that check is treated like any other invalid response
and retried. This layer itself only builds the prompt, calls the API and
validates the reply's schema: required keys, types, ranges and allow-lists.

It does not send a student id, a reflection, or any precomputed score,
flag, or tier. It never invents soft_label / tips / speak_prominence when
Gemini fails, and it never falls back to logic_manager.

AIManager is the object-oriented application service. Existing module-level
functions remain only as backwards-compatible entry points for Phase 1 tests.
No terminal I/O (print / input), no business rules, no file persistence.

Pipeline position:
  User → io_manager → ai_manager (required) → data_manager
  On failure: structured error for io_manager.format_ai_error (no Logic substitute)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Mapping, Sequence

from ai.ai_failure import AIFailure
from ai.ai_provider import AIProvider
from ai.error_classifier import ErrorClassifier
from ai.response_contract import (
    ANSWER_KEYS,
    default_response_contract,
    ResponseContract,
)
from ai.score_auditor import ScoreAuditor
from ai.validators.score_validator import ScoreValidator
from ai.validators.stressors_validator import StressorsValidator
from ai.validators.tips_validator import TipsValidator
from base_manager import BaseManager
from json_types import JSONObject

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# I/O fields this layer may send to Gemini (student check-in only)
# ---------------------------------------------------------------------------

# Raw answers plus the questionnaire version. No student id, reflection,
# score, flag, or tier. Gemini calculates stress_score from these answers.
RAW_ANSWER_KEYS = ANSWER_KEYS
STUDENT_PROMPT_FIELDS = RAW_ANSWER_KEYS + ("survey_version",)

# Wording and answer labels only. Scoring stays outside this module.
# Callers may pass survey.QUESTIONS (same key / prompt / options shape).
DEFAULT_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "pss_1",
        "prompt": (
            "In the past month, how often have you felt you couldn't "
            "control the important things in your life?"
        ),
        "options": ("Never", "Almost never", "Sometimes", "Fairly often", "Very often"),
    },
    {
        "key": "pss_2",
        "prompt": (
            "In the past month, how often have you felt confident "
            "handling your personal problems?"
        ),
        "options": ("Never", "Almost never", "Sometimes", "Fairly often", "Very often"),
        "reverse": True,
    },
    {
        "key": "pss_3",
        "prompt": (
            "In the past month, how often have you felt things were "
            "going well for you?"
        ),
        "options": ("Never", "Almost never", "Sometimes", "Fairly often", "Very often"),
        "reverse": True,
    },
    {
        "key": "pss_4",
        "prompt": (
            "In the past month, how often have you felt problems were "
            "piling up too much to handle?"
        ),
        "options": ("Never", "Almost never", "Sometimes", "Fairly often", "Very often"),
    },
    {
        "key": "sleep_hours_avg",
        "prompt": (
            "On average, how many hours of sleep have you gotten "
            "each night this week?"
        ),
        "min": 0,
        "max": 14,
        "step": 0.5,
        "low": "0 hours",
        "high": "14 hours",
    },
    {
        "key": "sleep_quality",
        "prompt": "During the past week, how would you rate your sleep quality overall?",
        "options": ("Very good", "Fairly good", "Okay", "Fairly bad", "Very bad"),
    },
    {
        "key": "pas_workload",
        "prompt": "I feel my coursework is too much to handle.",
        "options": ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"),
    },
    {
        "key": "pas_catchup",
        "prompt": "When I fall behind on my work, I find it hard to catch up.",
        "options": ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"),
    },
    {
        "key": "fin_stress",
        "prompt": "How stressed do you feel about your personal finances in general?",
        "options": (
            "No stress at all",
            "A little stress",
            "Moderate stress",
            "High stress",
            "Overwhelming stress",
        ),
    },
    {
        "key": "mspss_friends",
        "prompt": "I can count on my friends when things go wrong.",
        "options": ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"),
        "reverse": True,
    },
    {
        "key": "mspss_family",
        "prompt": "I get the emotional help and support I need from my family.",
        "options": ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"),
        "reverse": True,
    },
)

# ---------------------------------------------------------------------------
# Allow-lists. Tip copy lives in io_manager.format_tips — return IDs only.
# Every tip ID here is also in io_manager.TIPS_ALLOWLIST.
# emotional_distress and feelings_check_in are omitted: they need the
# reflection, and that text is never sent to Gemini.
# ---------------------------------------------------------------------------

SOFT_LABEL_OK = "You're doing ok"
SOFT_LABEL_CHECK_IN = "Worth a check-in"
SOFT_LABEL_REACH_OUT = "Please reach out"
SOFT_LABEL_OK_CURLY = "You’re doing ok"

SOFT_LABELS = (
    SOFT_LABEL_OK,
    SOFT_LABEL_CHECK_IN,
    SOFT_LABEL_REACH_OUT,
)
ALLOWED_SOFT_LABELS = frozenset(SOFT_LABELS)
_SOFT_LABEL_ALIASES = {
    SOFT_LABEL_OK: SOFT_LABEL_OK,
    SOFT_LABEL_OK_CURLY: SOFT_LABEL_OK,
    SOFT_LABEL_CHECK_IN: SOFT_LABEL_CHECK_IN,
    SOFT_LABEL_REACH_OUT: SOFT_LABEL_REACH_OUT,
}

SPEAK_PROMINENCE = ("low", "medium", "high")
ALLOWED_SPEAK_PROMINENCE = frozenset(SPEAK_PROMINENCE)
ALLOWED_RISK_CATEGORIES = frozenset({"Low", "Moderate", "High"})

ALLOWED_PRIMARY_STRESSORS = StressorsValidator.ALLOWED

# IDs only — same keys as io_manager.TIPS_ALLOWLIST. Do not invent tip text.
ALLOWED_TIP_IDS = TipsValidator.ALLOWED

# Allowed range for stress_score in Gemini's reply (a schema range check).
# The tier cut-offs are logic_manager's, not this module's.
STRESS_SCORE_MIN = ScoreValidator.MINIMUM
STRESS_SCORE_MAX = ScoreValidator.MAXIMUM

# What Gemini must return. risk_score and recommended_support are added by logic_manager.
_REQUIRED_FIELDS = (
    "scored_items",
    "stress_score",
    "risk_category",
    "primary_stressors",
    "confidence",
    "reasoning",
    "soft_label",
    "tips",
    "speak_prominence",
)

# Named models only. gemini-3.6-flash first, then older fixed names.
# No moving alias such as gemini-flash-latest.
MODEL_CHAIN = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_SEC = 0.5
# One check-in must not spend more than this many Gemini calls (daily quota).
MAX_TOTAL_CALLS = 4
# Extra try on the same model, and only for a timeout or bad JSON.
_SAME_MODEL_RETRIES = 1

# ---------------------------------------------------------------------------
# Structured AI failures — codes MUST match io_manager.format_ai_error
# ---------------------------------------------------------------------------

AI_SOURCE = "gemini"

ERROR_CODE_MISSING_API_KEY = "missing_api_key"
ERROR_CODE_TIMEOUT = "timeout"
ERROR_CODE_INVALID_RESPONSE = "invalid_response"
ERROR_CODE_RETRIES_EXHAUSTED = "retries_exhausted"
ERROR_CODE_RATE_LIMITED = "rate_limited"
ERROR_CODE_UNAVAILABLE = "unavailable"

AI_ERROR_CODES = (
    ERROR_CODE_MISSING_API_KEY,
    ERROR_CODE_TIMEOUT,
    ERROR_CODE_INVALID_RESPONSE,
    ERROR_CODE_RETRIES_EXHAUSTED,
    ERROR_CODE_RATE_LIMITED,
    ERROR_CODE_UNAVAILABLE,
)


def _structured_ai_error(error_code: str, detail: str) -> dict[str, str]:
    """Build the failure dict Flask can pass to io_manager.format_ai_error."""
    return AIFailure(error_code, str(detail)).as_dict()


def _final_ai_error(
    last_kind: str,
    last_detail: str,
    *,
    attempts: int,
    last_was_api_exception: bool,
    failure_kinds: list[str] | None = None,
) -> dict[str, str]:
    """
    Choose the structured code after the model chain ends.

    - every model rate-limited → rate_limited
    - timeout / DeadlineExceeded stay timeout (even after retries)
    - schema / malformed JSON stay invalid_response
    - other API exceptions after more than one attempt → retries_exhausted
    - other one-shot API / network failures → unavailable
    """
    if failure_kinds and all(kind == ERROR_CODE_RATE_LIMITED for kind in failure_kinds):
        return _structured_ai_error(ERROR_CODE_RATE_LIMITED, last_detail)
    if last_kind == ERROR_CODE_TIMEOUT:
        code = ERROR_CODE_TIMEOUT
    elif last_kind == ERROR_CODE_INVALID_RESPONSE:
        code = ERROR_CODE_INVALID_RESPONSE
    elif last_was_api_exception and attempts > 1:
        code = ERROR_CODE_RETRIES_EXHAUSTED
    elif last_kind in AI_ERROR_CODES:
        code = last_kind
    else:
        code = ERROR_CODE_UNAVAILABLE
    return _structured_ai_error(code, last_detail)


def _backoff_seconds(attempt: int, base_delay_sec: float) -> float:
    """Wait after a failed attempt. attempt is 1-based: 0.5s, 1s, 2s, ..."""
    base = max(0.0, float(base_delay_sec))
    return base * (2 ** max(0, attempt - 1))


def _tries_on_one_model(max_attempts: int) -> int:
    """First call, plus at most one repeat. Never more than max_attempts."""
    requested = max(1, int(max_attempts))
    return min(requested, 1 + _SAME_MODEL_RETRIES)


def _log_model_fallback(failed_model: str, next_model: str, reason: str) -> None:
    """Record a model switch. The reason is an error code, never an API key."""
    logger.warning(
        "Gemini model %s failed (%s); trying %s",
        failed_model,
        reason,
        next_model,
    )


def _mark_gemini_success(ai_fields: dict[str, Any]) -> dict[str, Any]:
    """Attach the success marker Data/Flask can trust. Never used on failure."""
    marked = dict(ai_fields)
    marked["source"] = AI_SOURCE
    return marked


def _format_question(question: Mapping[str, Any]) -> str:
    """One question line: key, wording, and answer labels. No scores."""
    key = str(question.get("key", "")).strip()
    wording = str(question.get("prompt", "")).strip()
    options = question.get("options")
    positive = bool(question.get("reverse"))
    tone = " Positively worded: a high answer means less stress." if positive else ""
    if options:
        labels = ", ".join(
            f"{number} = {label}" for number, label in enumerate(options, start=1)
        )
        return f"- {key}: {wording}{tone} Answer labels: {labels}."
    minimum = question.get("min", 0)
    maximum = question.get("max", 14)
    step = question.get("step", 0.5)
    low = question.get("low", "0 hours")
    high = question.get("high", "14 hours")
    return (
        f"- {key}: {wording}{tone} "
        f"Answer: {minimum} to {maximum} hours in steps of {step} "
        f"({low} to {high}), not a 1 to 5 rating."
    )


def _run_check(
    verify_fn: Callable[[dict[str, Any], Mapping[str, Any]], str | None],
    ai_fields: dict[str, Any],
    answers: Mapping[str, Any],
) -> str | None:
    """Run the caller's domain check on a schema-valid reply. Never raises.

    Returns the problem as text, or None when the reply passes.
    """
    try:
        problem = verify_fn(ai_fields, answers)
    except Exception as exc:  # noqa: BLE001 — a broken check must not crash the pipeline
        return f"Score check could not run: {exc}"
    return str(problem) if problem else None


# Gemini structured-output schema, built from the ResponseValidator list.
# risk_score is not included; Logic adds it after validation.
_DEFAULT_CONTRACT = default_response_contract()
RESPONSE_SCHEMA: dict[str, Any] = _DEFAULT_CONTRACT.schema()


class AIManager(BaseManager):
    """OOP service for prompt creation, AI calls, validation and recovery.

    The provider contract isolates vendor-specific calls (Open/Closed and
    Liskov). Reply fields are checked by a composed ResponseContract, so
    AIManager does not branch on each field. The optional ScoreAuditor
    belongs to the Logic layer.

    Neither the student identifier nor reflection is sent to the provider.
    No assessment is finalised or fabricated on an AI failure.
    """

    def __init__(
        self,
        provider: AIProvider,
        *,
        auditor: ScoreAuditor | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        retry_delay_sec: float = _DEFAULT_RETRY_DELAY_SEC,
        questions: Sequence[Mapping[str, Any]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        contract: ResponseContract | None = None,
        classifier: ErrorClassifier | None = None,
    ) -> None:
        super().__init__("ai_manager")
        if not isinstance(provider, AIProvider):
            raise TypeError("provider must implement AIProvider")
        if auditor is not None and not isinstance(auditor, ScoreAuditor):
            raise TypeError("auditor must implement ScoreAuditor")
        if contract is not None and not isinstance(contract, ResponseContract):
            raise TypeError("contract must be a ResponseContract")
        if classifier is not None and not isinstance(classifier, ErrorClassifier):
            raise TypeError("classifier must be an ErrorClassifier")
        self.__provider = provider
        self.__auditor = auditor
        self.__max_attempts = max_attempts
        self.__retry_delay_sec = retry_delay_sec
        self.__questions = questions
        self.__sleep = sleep_fn if sleep_fn is not None else time.sleep
        # Composition: the default contract is created here and is not shared
        # with another manager. Callers may pass an existing contract instead.
        self.__contract = contract if contract is not None else _DEFAULT_CONTRACT
        self.__classifier = classifier if classifier is not None else ErrorClassifier()

    def describe(self) -> JSONObject:
        """Non-secret service configuration for dependency and health checks."""
        return {"provider": self.__provider.name,
                "models": list(self.__provider.models),
                "ready": self.__provider.is_ready(),
                "max_attempts": self.__max_attempts,
                "auditor": type(self.__auditor).__name__ if self.__auditor else None}

    @property
    def provider(self) -> AIProvider:
        """The injected provider, exposed through its abstract contract."""
        return self.__provider

    @property
    def contract(self) -> ResponseContract:
        """The reply validators this manager runs, in order."""
        return self.__contract

    def build_prompt(
        self,
        student_dict: dict[str, Any],
        *,
        questions: Sequence[Mapping[str, Any]] | None = None,
        instructions: str | None = None,
    ) -> str:
        """Build Gemini's privacy-filtered prompt with Logic's optional rules."""
        if questions is None:
            questions = self.__questions
        if instructions is None and self.__auditor is not None:
            instructions = self.__auditor.instructions()
        catalog = DEFAULT_QUESTIONS if questions is None else questions
        payload = {key: student_dict.get(key) for key in STUDENT_PROMPT_FIELDS}
        record_json = json.dumps(payload, ensure_ascii=False, indent=2)
        wording = "\n".join(_format_question(item) for item in catalog)
        stressor_list = ", ".join(sorted(ALLOWED_PRIMARY_STRESSORS))
        tip_id_list = ", ".join(sorted(ALLOWED_TIP_IDS))
        soft_label_list = ", ".join(f'"{label}"' for label in SOFT_LABELS)
        scoring_rules = instructions or ""

        return (
            "You are a supportive student-wellbeing assistant for DoNotStress, "
            "a local check-in used by students.\n"
            "Calculate stress_score from the 11 answers with the formula below. "
            "You are not given a score, a tier, or a written reflection. "
            "Do not diagnose, and do not invent contacts.\n"
            f"{scoring_rules}"
            f"Question wording and answer labels:\n{wording}\n"
            "Speak to the student with warmth. Return ONLY a single JSON object "
            "(no markdown fences, no commentary) with exactly these keys:\n"
            '- "scored_items": object of the 11 converted values. Keys are '
            "pss_1, pss_2, pss_3, pss_4, sleep_hours_avg, sleep_quality, "
            "pas_workload, pas_catchup, fin_stress, mspss_friends, mspss_family. "
            "Each value is an integer from 1 to 5.\n"
            f'- "stress_score": the mean above, from {STRESS_SCORE_MIN:g} to '
            f"{STRESS_SCORE_MAX:g}, rounded to 2 decimal places\n"
            '- "risk_category": "Low", "Moderate", or "High", matching stress_score\n'
            '- "primary_stressors": list of snake_case strings chosen ONLY from: '
            f"{stressor_list}\n"
            '- "confidence": number from 0.0 to 1.0\n'
            '- "reasoning": 2 or 3 warm, plain sentences written to the student. '
            "No numbers, no jargon, no diagnosis, and no contacts\n"
            f'- "soft_label": exactly one of {soft_label_list}, matching risk_category. '
            "Use a straight apostrophe in You're doing ok\n"
            '- "tips": list of 1 to 3 tip IDs only, each one of: '
            f"{tip_id_list}. Do not invent tip text\n"
            '- "speak_prominence": "low", "medium", or "high", matching risk_category. '
            "This is how strongly the UI highlights Talk to someone. "
            "SIT Counselling stays available and is shown more prominently when stress is higher\n"
            "Do not add other keys. Never invent phone numbers, emails, or offices. "
            "SIT Counselling contacts are shown by the UI, not by you.\n"
            "\n"
            "Student check-in:\n"
            f"{record_json}\n"
        )

    def validate_response(self, payload: Any) -> tuple[bool, Any]:
        """Validate and normalise required Gemini fields; leave domain rules to Logic.

        Field checks are ResponseValidator objects. This method only unwraps
        the raw text, then asks the contract to run them.
        """
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return False, "Empty AI response string."
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                return False, f"AI response is not valid JSON: {exc}"

        if not isinstance(payload, dict):
            return False, "AI response must be a JSON object."
        return self.__contract.validate(payload)

    def call_api(
        self,
        prompt: str,
        *,
        answers: Mapping[str, Any] | None = None,
        verify_fn: Callable[[dict[str, Any], Mapping[str, Any]], str | None] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, dict[str, str] | None]:
        """Try provider models, validate every reply, and cap total API calls.

        A timeout or bad JSON can retry once on the same model. Rate limits
        and other provider errors move immediately to the next model. Gemini
        replies remain mandatory, even when a Logic audit is available.
        """
        if not self.__provider.is_ready():
            detail = "GEMINI_API_KEY is not set; cannot call Gemini."
            logger.error(detail)
            return False, None, _structured_ai_error(ERROR_CODE_MISSING_API_KEY, detail)

        audit = verify_fn
        if audit is None and self.__auditor is not None:
            audit = self.__auditor.verify

        last_detail = "Unknown AI failure."
        last_kind = ERROR_CODE_UNAVAILABLE
        last_was_api_exception = False
        tries_allowed = _tries_on_one_model(self.__max_attempts)
        models = tuple(self.__provider.models)
        calls_made = 0
        finished_attempt = 1
        failure_kinds: list[str] = []

        if not models:
            return False, None, _structured_ai_error(
                ERROR_CODE_UNAVAILABLE, "AI provider has no configured models."
            )

        for model_index, active_model in enumerate(models):
            tries_here = 0
            move_on = False
            while tries_here < tries_allowed and calls_made < MAX_TOTAL_CALLS:
                calls_made += 1
                tries_here += 1
                finished_attempt = tries_here
                try:
                    raw_text = self.__provider.generate(
                        prompt, active_model, schema=self.__contract.schema()
                    )
                except Exception as exc:  # noqa: BLE001 - never crash the pipeline
                    last_was_api_exception = True
                    last_kind = self.__classifier.classify(exc)
                    failure_kinds.append(last_kind)
                    # Never copy credentials from vendor exception text to logs.
                    safe_exc = self.__provider.redact(str(exc))
                    last_detail = (
                        f"Gemini API failure (attempt {tries_here}/{tries_allowed}) "
                        f"model={active_model}: {safe_exc}"
                    )
                    logger.warning(last_detail)
                    if last_kind == ERROR_CODE_MISSING_API_KEY:
                        return False, None, _structured_ai_error(
                            ERROR_CODE_MISSING_API_KEY, last_detail
                        )
                    repeat = (
                        last_kind == ERROR_CODE_TIMEOUT
                        and tries_here < tries_allowed
                        and calls_made < MAX_TOTAL_CALLS
                    )
                    if repeat:
                        self.__sleep(_backoff_seconds(tries_here, self.__retry_delay_sec))
                        continue
                    move_on = True
                    break

                ok, result = self.validate_response(raw_text)
                if ok and audit is not None and answers is not None:
                    problem = _run_check(audit, result, answers)
                    if problem:
                        ok, result = False, problem
                if ok:
                    return True, _mark_gemini_success(result), None

                last_was_api_exception = False
                last_kind = ERROR_CODE_INVALID_RESPONSE
                failure_kinds.append(last_kind)
                last_detail = (
                    f"Schema validation failed (attempt {tries_here}/{tries_allowed}) "
                    f"model={active_model}: {result}"
                )
                logger.warning(last_detail)
                if tries_here < tries_allowed and calls_made < MAX_TOTAL_CALLS:
                    self.__sleep(_backoff_seconds(tries_here, self.__retry_delay_sec))
                    continue
                move_on = True
                break

            if not move_on or calls_made >= MAX_TOTAL_CALLS or model_index >= len(models) - 1:
                break
            _log_model_fallback(active_model, models[model_index + 1], last_kind)

        return False, None, _final_ai_error(
            last_kind,
            last_detail,
            attempts=finished_attempt,
            last_was_api_exception=last_was_api_exception,
            failure_kinds=failure_kinds,
        )

    def analyse_student(
        self,
        student_dict: dict[str, Any],
        *,
        questions: Sequence[Mapping[str, Any]] | None = None,
        instructions: str | None = None,
        verify_fn: Callable[[dict[str, Any], Mapping[str, Any]], str | None] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, dict[str, str] | None]:
        """Return a Gemini-enriched record or a structured failure, never both."""
        if not isinstance(student_dict, dict):
            return False, None, _structured_ai_error(
                ERROR_CODE_INVALID_RESPONSE,
                "student_dict must be a dict from io_manager.",
            )
        prompt = self.build_prompt(
            student_dict, questions=questions, instructions=instructions
        )
        ok, ai_fields, error = self.call_api(
            prompt, answers=student_dict, verify_fn=verify_fn
        )
        if not ok or ai_fields is None:
            return False, None, error
        enriched = dict(student_dict)
        enriched.update(ai_fields)
        enriched["source"] = AI_SOURCE
        return True, enriched, None

