"""Build AIManager the way the tests used to call the old module functions.

The application does not import this file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ai.function_provider import FunctionProvider
from ai.gemini_provider import GeminiProvider
from ai_manager import AIManager
from checkin_service import CheckinService
from data_manager import DataManager
from io_manager import IOManager
from logic_manager import LogicManager


def _models(model_name: str) -> tuple[str, ...]:
    primary = (model_name or GeminiProvider.MODEL_CHAIN[0]).strip() or GeminiProvider.MODEL_CHAIN[0]
    ordered = [primary]
    for name in GeminiProvider.MODEL_CHAIN:
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def manager(
    *,
    api_key: str | None = None,
    model_name: str = GeminiProvider.MODEL_CHAIN[0],
    max_attempts: int = 3,
    retry_delay_sec: float = 0.5,
    generate_fn: Callable[[str, str, str], str] | None = None,
) -> AIManager:
    """An AIManager on Gemini, or on a fake generate_fn when one is given."""
    if api_key is None:
        key = os.environ.get("GEMINI_API_KEY", "")
    else:
        key = api_key
    key = key.strip()
    if generate_fn is None:
        provider = GeminiProvider(api_key=key, preferred_model=model_name)
    else:
        provider = FunctionProvider(generate_fn, key, models=_models(model_name))
    return AIManager(provider, max_attempts=max_attempts, retry_delay_sec=retry_delay_sec)


def build_prompt(
    student_dict: dict[str, Any],
    questions: Sequence[Mapping[str, Any]] | None = None,
    instructions: str | None = None,
) -> str:
    return AIManager(GeminiProvider(api_key="")).build_prompt(
        student_dict, questions=questions, instructions=instructions)


def validate_response(payload: Any) -> tuple[bool, Any]:
    return AIManager(GeminiProvider(api_key="")).validate_response(payload)


def call_gemini(
    prompt: str,
    *,
    api_key: str | None = None,
    model_name: str = GeminiProvider.MODEL_CHAIN[0],
    max_attempts: int = 3,
    retry_delay_sec: float = 0.5,
    generate_fn: Callable[[str, str, str], str] | None = None,
    answers: Mapping[str, Any] | None = None,
    verify_fn: Callable[..., str | None] | None = None,
) -> tuple[bool, dict[str, Any] | None, dict[str, str] | None]:
    return manager(
        api_key=api_key, model_name=model_name, max_attempts=max_attempts,
        retry_delay_sec=retry_delay_sec, generate_fn=generate_fn,
    ).call_api(prompt, answers=answers, verify_fn=verify_fn)


def call_api(
    prompt: str,
    *,
    generate_fn: Callable[[str, str, str], str] | None = None,
    answers: Mapping[str, Any] | None = None,
    verify_fn: Callable[..., str | None] | None = None,
) -> tuple[bool, dict[str, Any] | None, dict[str, str] | None]:
    return call_gemini(prompt, generate_fn=generate_fn, answers=answers, verify_fn=verify_fn)


def analyse_student(
    student_dict: dict[str, Any],
    *,
    api_key: str | None = None,
    model_name: str = GeminiProvider.MODEL_CHAIN[0],
    max_attempts: int = 3,
    retry_delay_sec: float = 0.5,
    generate_fn: Callable[[str, str, str], str] | None = None,
    questions: Sequence[Mapping[str, Any]] | None = None,
    instructions: str | None = None,
    verify_fn: Callable[..., str | None] | None = None,
) -> tuple[bool, dict[str, Any] | None, dict[str, str] | None]:
    return manager(
        api_key=api_key, model_name=model_name, max_attempts=max_attempts,
        retry_delay_sec=retry_delay_sec, generate_fn=generate_fn,
    ).analyse_student(
        student_dict, questions=questions, instructions=instructions, verify_fn=verify_fn)


def process_checkin(record, *, generate_fn=None, max_attempts=3, retry_delay_sec=0.5):
    """AI, then Logic. The Flask app uses CheckinService directly."""
    service = CheckinService(IOManager(), LogicManager(), DataManager())
    ok, enriched, error = analyse_student(
        record, generate_fn=generate_fn, max_attempts=max_attempts,
        retry_delay_sec=retry_delay_sec, api_key="test" if generate_fn else None,
        questions=service.io.survey.questions,
        instructions=service.logic.scoring_instructions(),
        verify_fn=service.logic.verify_ai_scores,
    )
    if not ok or not isinstance(enriched, dict):
        code = error.get("error_code", "unavailable") if isinstance(error, dict) else "unavailable"
        return False, code
    finalized = service.logic.apply_logic(enriched)
    if not isinstance(finalized, dict) or not finalized.get("ok"):
        return False, "unavailable"
    return True, finalized
