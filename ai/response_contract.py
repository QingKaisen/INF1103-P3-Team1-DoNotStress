"""The Gemini reply contract: one validator per field, run as a list."""

from __future__ import annotations

from ai.validators.choice_validator import ChoiceValidator
from ai.validators.confidence_validator import ConfidenceValidator
from ai.validators.reasoning_validator import ReasoningValidator
from ai.validators.response_validator import ResponseValidator
from ai.validators.score_validator import ScoreValidator
from ai.validators.scored_items_validator import ScoredItemsValidator
from ai.validators.soft_label_validator import SoftLabelValidator
from ai.validators.stressors_validator import StressorsValidator
from ai.validators.tips_validator import TipsValidator
from json_types import JSONObject, JSONValue

# Schema key order sent to Gemini. Validation order is separate so the
# first rejected field stays the historical one.
SCHEMA_ORDER: tuple[str, ...] = (
    "scored_items",
    "stress_score",
    "risk_category",
    "soft_label",
    "speak_prominence",
    "primary_stressors",
    "tips",
    "reasoning",
    "confidence",
)

# Order used when naming every missing key in one message.
MISSING_ORDER: tuple[str, ...] = (
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

ANSWER_KEYS: tuple[str, ...] = (
    "pss_1",
    "pss_2",
    "pss_3",
    "pss_4",
    "sleep_hours_avg",
    "sleep_quality",
    "pas_workload",
    "pas_catchup",
    "fin_stress",
    "mspss_friends",
    "mspss_family",
)


class ResponseContract:
    """Owns the reply validators and applies them without type-specific branches.

    AIManager composes one of these. A new reply field is a new
    ResponseValidator passed in here, not a new branch inside AIManager.
    """

    def __init__(self, validators: tuple[ResponseValidator, ...] | list[ResponseValidator]) -> None:
        self.__validators: tuple[ResponseValidator, ...] = tuple(validators)
        self.__schema: JSONObject | None = None

    @property
    def validators(self) -> tuple[ResponseValidator, ...]:
        return self.__validators

    def schema(self) -> JSONObject:
        """Gemini structured-output schema. Cached so callers can use `is`."""
        if self.__schema is None:
            by_field = {validator.field: validator for validator in self.__validators}
            ordered = [by_field[name] for name in SCHEMA_ORDER if name in by_field]
            seen = {validator.field for validator in ordered}
            ordered.extend(
                validator for validator in self.__validators
                if validator.field not in seen
            )
            fields = [validator.field for validator in ordered]
            self.__schema = {
                "type": "OBJECT",
                "propertyOrdering": fields,
                "required": list(fields),
                "properties": {
                    validator.field: validator.schema() for validator in ordered
                },
            }
        return self.__schema

    def validate(self, payload: dict) -> tuple[bool, JSONValue]:
        """Normalise every field, or return the first problem. Never raises."""
        fields = [validator.field for validator in self.__validators]
        missing_order = (
            MISSING_ORDER if set(fields) == set(MISSING_ORDER) else tuple(fields)
        )
        missing = [name for name in missing_order if name not in payload]
        if missing:
            return False, (
                "AI response missing required fields: " + ", ".join(missing) + "."
            )
        normalised: dict[str, JSONValue] = {}
        for validator in self.__validators:
            ok, value = validator.validate(payload[validator.field])
            if not ok:
                return False, value
            normalised[validator.field] = value
        return True, normalised


def default_response_contract() -> ResponseContract:
    """The nine fields Gemini must return, checked in the original order."""
    return ResponseContract((
        ScoreValidator(),
        ScoredItemsValidator(
            ANSWER_KEYS,
            "The 11 answers converted onto the 1-5 stress scale.",
        ),
        ChoiceValidator(
            "risk_category",
            ("Low", "Moderate", "High"),
            "The tier for stress_score, following the rules in the prompt.",
        ),
        StressorsValidator(),
        ConfidenceValidator(),
        ReasoningValidator(),
        SoftLabelValidator(),
        TipsValidator(),
        ChoiceValidator(
            "speak_prominence",
            ("low", "medium", "high"),
            "Must match risk_category: low, medium, or high.",
        ),
    ))
