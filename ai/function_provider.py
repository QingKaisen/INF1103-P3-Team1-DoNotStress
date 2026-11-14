"""Adapter for legacy generate_fn tests; the app uses GeminiProvider."""

from __future__ import annotations

from collections.abc import Callable

from ai.ai_provider import AIProvider
from json_types import JSONObject


class FunctionProvider(AIProvider):
    """Adapts a test callback to the exact AIProvider contract.

    A callback receives (prompt, api_key, model_name), matching the Phase 1
    tests. The manager still owns retries and JSON validation. This adapter
    never makes a network request on its own.
    """

    def __init__(
        self,
        generate_fn: Callable[[str, str, str], str],
        api_key: str,
        *,
        models: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.__generate_fn = generate_fn
        self.__api_key = api_key
        self.__models = models

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def models(self) -> tuple[str, ...]:
        return self.__models

    def is_ready(self) -> bool:
        # Callbacks are only supplied explicitly for offline tests.
        return True

    def generate(
        self,
        prompt: str,
        model_name: str,
        schema: JSONObject | None = None,
    ) -> str:
        return self.__generate_fn(prompt, self.__api_key, model_name)

    def redact(self, text: str) -> str:
        if self.__api_key:
            return text.replace(self.__api_key, "[redacted]")
        return text
