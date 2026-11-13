"""The scoring port LogicManager depends on.

Dependency inversion: LogicManager calls this interface. StressScorer is
one implementation. A test, or a later formula, can be passed in instead.
The AI layer still sees only ScoreAuditor, which this extends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai.score_auditor import ScoreAuditor
from json_types import JSONObject, JSONValue
from logic.score_card import ScoreCard


class Scorer(ScoreAuditor, ABC):
    """What Logic needs from whoever owns the formula.

    ScoreAuditor (instructions, verify) is included, so a Scorer can also
    be handed to the AI manager. The attributes TOLERANCE and SUPPORT_KEYS
    are part of the same contract.
    """

    @abstractmethod
    def score(self, answers: JSONObject) -> ScoreCard:
        """Deterministic reading of the answers."""

    @abstractmethod
    def scored_items(self, answers: JSONObject) -> dict[str, int]:
        """The answers on the 1–5 stress scale."""

    @abstractmethod
    def scored_value(self, key: str, answer: float) -> float:
        """One answer converted onto the stress scale."""

    @abstractmethod
    def sleep_hours_stress(self, hours: float) -> int:
        """Typical nightly hours as a 1–5 stress value."""

    @abstractmethod
    def category_for(self, stress_score: float) -> str:
        """Low, Moderate, or High for this score."""

    @abstractmethod
    def label_for(self, category: str) -> str:
        """The student-facing label for a tier."""

    @abstractmethod
    def prominence_for(self, category: str) -> str:
        """How strongly the support panel is shown for a tier."""

    @abstractmethod
    def risk_score_for(self, stress_score: float) -> float:
        """The 1–5 score mapped onto 0–1."""

    @abstractmethod
    def within_tolerance(self, gemini_score: float, logic_score: float) -> bool:
        """True when Gemini is close enough to the audit score."""

    @abstractmethod
    def context_flags(self, answers: JSONObject,
                      support_mean: float) -> dict[str, bool]:
        """The four context flags from the raw answers."""

    @abstractmethod
    def answer_in_range(self, key: str, value: JSONValue) -> bool:
        """True when value is a number Logic can score for this key."""

    @abstractmethod
    def number(self, value: JSONValue) -> float | None:
        """value as a finite float, or None when it is not usable."""
