"""Validator for scored_items."""

from __future__ import annotations

from ai.validators.response_validator import ResponseValidator
from json_types import JSONObject, JSONValue


class ScoredItemsValidator(ResponseValidator):
    """The answers converted onto the 1–5 stress scale, one int per key."""

    def __init__(self, keys: tuple[str, ...], description: str = (
            "The answers converted onto the 1-5 stress scale.")) -> None:
        super().__init__("scored_items")
        self.__keys: tuple[str, ...] = keys
        self.__description: str = description

    def validate(self, value: object) -> tuple[bool, JSONValue]:
        if not isinstance(value, dict):
            return False, ("scored_items must be an object of the "
                           "converted answers.")
        if set(value) != set(self.__keys):
            return False, (f"scored_items must contain exactly the "
                           f"{len(self.__keys)} answer keys.")
        cleaned: JSONObject = {}
        for key in self.__keys:
            item = value[key]
            if (isinstance(item, bool) or not isinstance(item, int)
                    or not 1 <= item <= 5):
                return False, f"scored_items.{key} must be an integer 1 to 5."
            cleaned[key] = item
        return True, cleaned

    def schema(self) -> JSONObject:
        return {
            "type": "OBJECT",
            "description": self.__description,
            "propertyOrdering": list(self.__keys),
            "required": list(self.__keys),
            "properties": {
                key: {"type": "INTEGER", "minimum": 1, "maximum": 5}
                for key in self.__keys
            },
        }
