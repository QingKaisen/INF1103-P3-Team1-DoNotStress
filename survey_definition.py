"""Encapsulated questionnaire definitions, safe responses and local reflection checks.

Data is supplied by survey.py for backwards compatibility with the team's
Phase 1 question constants. Runtime clients use a SurveyDefinition instance.
"""
from __future__ import annotations

import math
import re
from typing import Any


class SurveyDefinition:
    """The immutable-by-convention policy for one questionnaire version."""

    def __init__(self, *, version: str, questions: list[dict[str, Any]],
                 sections: list[dict[str, Any]], safety_patterns: list[str]) -> None:
        self.__version = version
        self.__questions = tuple(questions)
        self.__question_map = {item['key']: item for item in questions}
        self.__sections = tuple(sections)
        self.__safety_patterns = tuple(safety_patterns)

    @property
    def version(self) -> str:
        return self.__version

    @property
    def questions(self) -> tuple[dict[str, Any], ...]:
        return self.__questions

    @property
    def question_map(self) -> dict[str, dict[str, Any]]:
        return dict(self.__question_map)

    def safety_check(self, text: object) -> bool:
        checked = str(text or '').lower().replace('’', "'")
        return any(re.search(pattern, checked) for pattern in self.__safety_patterns)

    def validate_question(self, raw: object, question: dict[str, Any]) -> tuple[bool, Any]:
        if raw is None or str(raw).strip() == '':
            return ((True, None) if question.get('optional') else
                    (False, 'Please answer this question.'))
        if isinstance(raw, (bool, list, dict)):
            return False, 'Choose one of the available answers.'
        try:
            value = float(raw)
        except (ValueError, TypeError, OverflowError):
            return False, 'Choose a number in the available range.'
        if not math.isfinite(value) or not question['min'] <= value <= question['max'] or not (value / question['step']).is_integer():
            return False, f"Choose {question['min']} to {question['max']} in steps of {question['step']}."
        return True, value if question['step'] == 0.5 else int(value)

    def public_config(self) -> dict[str, Any]:
        return {'version': self.__version, 'questions': list(self.__questions),
                'sections': list(self.__sections), 'safetyPatterns': list(self.__safety_patterns)}
