"""
DoNotStress — Logic Manager

Post-AI validator. Gemini is mandatory for every record. Logic recomputes
the deterministic stress score from the 11 raw answers as an audit and
never substitutes its own score. It does not re-validate Gemini's schema;
the AI manager owns that.

The formula lives only in a Scorer. StressScorer is the implementation
used by default. The outcome is
built by running BusinessRule objects in order, so a new rule is a new
subclass rather than a change to LogicManager.evaluate(). main.py still
calls the module-level names below; they are the default manager's methods.

Refinement
----------
This module used to be functions only, and it repeated the scoring formula
already written in StressScorer. A finished check-in still comes out the
same. The structure is what changed:

- LogicManager is a class, and it inherits BaseManager, like the other
  manager layers are meant to.
- apply_logic no longer contains a copy of each rule. It builds one
  Decision and calls BusinessRule.run() on the list it was given.
- The scorer field is a Scorer, not a hard-wired StressScorer. The
  default is still a StressScorer. A caller can pass another Scorer
  (dependency inversion).
- main.py did not change. apply_logic, verify_ai_scores,
  scoring_instructions, evaluate, score and route are methods of one
  default LogicManager.

Relationships
-------------
Composition  — LogicManager creates the Decision for one check-in and
               discards it when evaluate() returns. The default StressScorer
               is created here and is not shared.
Aggregation  — Business rules are whole–part, but each rule can be built
               and tested on its own. Pass a list in to use those objects;
               this manager does not own their lifecycle.
Association  — TrendAnalyser reads a StressHistory. The history and its
               records live in storage and are not owned here.
Polymorphism — evaluate() calls BusinessRule.run() only. It does not branch
               on which concrete rule it was given.

Rules (logic_rule "v3")
-----------------------
1. Score — mean of the 11 answers on the 1–5 stress scale, equal weight.
   pss_2, pss_3, mspss_friends and mspss_family are positively worded and
   scored as 6 - answer. sleep_hours_avg is converted: 8+ h -> 1,
   7 to <8 -> 2, 6 to <7 -> 3, 5 to <6 -> 4, under 5 -> 5.
2. Cross-check — if Gemini's stress_score is within 0.01 of the logic
   score, Gemini's score, tier, tips, stressors and reasoning are kept.
   Otherwise Logic returns a "score_mismatch" error dict.
3. Context flags — sleep, workload, finances, support. Each flag makes sure
   its matching tip is included if there is room (max 4 tips). Flags never
   replace Gemini's tips and never change the tier.
4. Support priority — when Gemini rates the check-in High AND lists
   low_social_support, talk_to_someone is shown first and
   keep_social_contact is added if there is room.
5. Safety — if the reflection safety check fired (safety_flag True), the
   tier is forced to High / "Please reach out" / prominence high and
   talk_to_someone is shown first. The stress score is not changed.

Tiers: Low below 2.5, Moderate 2.5 to 3.5 inclusive, High above 3.5.
These bands are team heuristics, not clinical cut-offs or a diagnosis.

Pipeline position:
  User → io_manager → ai_manager (must succeed) → logic_manager → data_manager
"""

from __future__ import annotations

from base_manager import BaseManager
from json_types import JSONObject
from logic.business_rule import BusinessRule
from logic.context_flag_rule import ContextFlagRule
from logic.decision import Decision
from logic.safety_override_rule import SafetyOverrideRule
from logic.scorer import Scorer
from logic.stress_scorer import StressScorer
from logic.support_priority_rule import SupportPriorityRule
from logic.tip_cap_rule import TipCapRule
from logic.trend import Trend
from logic.trend_analyser import TrendAnalyser
from records.stress_history import StressHistory


# ---------------------------------------------------------------------------
# Locked allow-lists. The numbers come from StressScorer, the one formula.
# ---------------------------------------------------------------------------

_SCORER = StressScorer

SOFT_LABEL_OK = _SCORER.CATEGORY_LABELS["Low"]
SOFT_LABEL_CHECK_IN = _SCORER.CATEGORY_LABELS["Moderate"]
SOFT_LABEL_REACH_OUT = _SCORER.CATEGORY_LABELS["High"]

RISK_CATEGORIES = ("Low", "Moderate", "High")

_CATEGORY_TO_LABEL = _SCORER.CATEGORY_LABELS
_CATEGORY_TO_PROMINENCE = _SCORER.CATEGORY_PROMINENCE

LOGIC_SOURCE = "ai_logic"
LOGIC_RULE = "v3"
ERROR_AI_FIELDS_REQUIRED = "ai_fields_required"
ERROR_SCORE_MISMATCH = "score_mismatch"

SCALE_MIN = _SCORER.SCALE_MIN
SCALE_MAX = _SCORER.SCALE_MAX
ANSWER_KEYS = _SCORER.ANSWER_KEYS
REVERSED_KEYS = _SCORER.REVERSED_KEYS
SUPPORT_KEYS = _SCORER.SUPPORT_KEYS
SLEEP_HOURS_MIN = _SCORER.SLEEP_HOURS_MIN
SLEEP_HOURS_MAX = _SCORER.SLEEP_HOURS_MAX
SLEEP_HOUR_BANDS = _SCORER.SLEEP_HOUR_BANDS
SLEEP_HOURS_BELOW_ALL = _SCORER.SLEEP_HOURS_BELOW_ALL
CHECK_IN_FROM = _SCORER.CHECK_IN_FROM
REACH_OUT_ABOVE = _SCORER.REACH_OUT_ABOVE
SCORE_TOLERANCE = _SCORER.TOLERANCE

FLAG_SLEEP = _SCORER.FLAG_SLEEP
FLAG_WORKLOAD = _SCORER.FLAG_WORKLOAD
FLAG_FINANCES = _SCORER.FLAG_FINANCES
FLAG_SUPPORT = _SCORER.FLAG_SUPPORT
SLEEP_FLAG_BELOW_HOURS = _SCORER.SLEEP_FLAG_BELOW_HOURS
SLEEP_QUALITY_FLAG_MIN = _SCORER.SLEEP_QUALITY_FLAG_MIN
WORKLOAD_FLAG_MIN = _SCORER.WORKLOAD_FLAG_MIN
FINANCE_FLAG_MIN = _SCORER.FINANCE_FLAG_MIN
SUPPORT_FLAG_BELOW = _SCORER.SUPPORT_FLAG_BELOW

MAX_TIPS = TipCapRule.MAX_TIPS
SUPPORT_PRIORITY_CATEGORY = SupportPriorityRule.CATEGORY
SUPPORT_PRIORITY_STRESSOR = SupportPriorityRule.STRESSOR

# Short internal note stored with each record (data_manager requires it).
# The student never sees this text; the UI shows tips instead.
RECOMMENDED_SUPPORT = {
    "Low": "Internal: low stress. The UI shows tips, not this field.",
    "Moderate": "Internal: moderate stress. The UI shows tips, not this field.",
    "High": "Internal: high stress. The UI shows tips, not this field.",
}

# Gemini fields that must already be on the record (from ai_manager).
# risk_score and recommended_support are worked out here, not by Gemini.
REQUIRED_AI_FIELDS = (
    "stress_score",
    "risk_category",
    "soft_label",
    "speak_prominence",
    "primary_stressors",
    "tips",
    "reasoning",
    "confidence",
)

# Answer labels for the factor cards (1-based).
SLEEP_QUALITY_OPTIONS = ("Very good", "Fairly good", "Okay", "Fairly bad", "Very bad")
AGREE_OPTIONS = ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree")
FIN_OPTIONS = (
    "No stress at all",
    "A little stress",
    "Moderate stress",
    "High stress",
    "Overwhelming stress",
)


def default_rules() -> list[BusinessRule]:
    """The v3 rules, in the only order that keeps today's behaviour.

    Safety can raise the tier before the other rules look at it. Support
    priority then leads with "talk to someone". Context flags only suggest
    extra tips. Tip cap must stay last: it keeps Gemini's own tips and
    stops the list at four. A new rule is added to this list, not written
    into LogicManager.
    """
    return [
        SafetyOverrideRule(),
        SupportPriorityRule(),
        ContextFlagRule(),
        TipCapRule(),
    ]


class LogicManager(BaseManager):
    """Applies domain rules to one AI-enriched check-in.

    Single responsibility: decide the final tier, tips and audit fields.
    Scoring arithmetic stays in StressScorer. Each business rule stays in
    its own class (open/closed). Any BusinessRule subclass can be passed
    in and is used the same way (Liskov).
    """

    def __init__(self, scorer: Scorer | None = None,
                 rules: list[BusinessRule] | None = None,
                 analyser: TrendAnalyser | None = None) -> None:
        super().__init__("logic_manager")
        # Composition of the default formula. The field type is Scorer,
        # so this class depends on the interface (dependency inversion).
        # StressScorer is only the object used when nobody passes one in.
        self.__scorer: Scorer = scorer if scorer is not None else StressScorer()
        # Aggregation: a caller may pass rules that already exist.
        self.__rules: list[BusinessRule] = (
            list(rules) if rules is not None else default_rules())
        # Association: the analyser does not own the stored history.
        self.__analyser: TrendAnalyser = (
            analyser if analyser is not None else TrendAnalyser())

    @property
    def rules(self) -> list[BusinessRule]:
        """The rules this manager will run, in order."""
        return list(self.__rules)

    def describe(self) -> JSONObject:
        """How this manager is set up. Never includes secrets."""
        return {
            "logic_rule": LOGIC_RULE,
            "rules": [rule.name for rule in self.__rules],
            "score_tolerance": self.__scorer.TOLERANCE,
        }

    def apply_logic(self, record: dict) -> dict:
        """Validate Gemini's analysis against the student's answers.

        On success, returns a shallow copy with ok=True and the final
        outcome. On missing fields or a score more than 0.01 off, returns
        an error dict and does not invent an outcome. Does not mutate
        the original dict.
        """
        if not isinstance(record, dict):
            return self.__error_result(
                {}, missing=list(REQUIRED_AI_FIELDS + ANSWER_KEYS),
                invalid=["record"])

        missing, invalid = self.__field_problems(record)
        if missing or invalid:
            return self.__error_result(record, missing=missing, invalid=invalid)

        scores = self.score_answers(record)
        logic_score = scores["stress_score"]
        gemini_score = self.__scorer.number(record["stress_score"])
        # Audit only. A mismatch is an error dict, not a score we invented.
        if (gemini_score is None
                or not self.__scorer.within_tolerance(gemini_score, logic_score)):
            shown = gemini_score if gemini_score is not None else logic_score
            return self.__score_mismatch_result(record, shown, logic_score)

        # One Decision for this check-in (composition). The loop does not
        # ask which subclass each rule is: that is the polymorphism, and it
        # is why adding a rule does not mean editing this method (open/closed).
        decision = Decision(record, scores["context_flags"])
        for rule in self.__rules:
            rule.run(decision)

        category = decision.risk_category
        enriched = dict(record)
        enriched.pop("feelings_text", None)
        enriched.update({
            "ok": True,
            "error": None,
            "source": LOGIC_SOURCE,
            "ai_ok": True,
            "logic_rule": LOGIC_RULE,
            "stress_score": record["stress_score"],
            "logic_stress_score": logic_score,
            "risk_score": self.__scorer.risk_score_for(gemini_score),
            "recommended_support": RECOMMENDED_SUPPORT[category],
            "risk_category": category,
            "soft_label": decision.soft_label,
            "speak_prominence": decision.speak_prominence,
            "tips": decision.tips,
            "primary_stressors": list(record["primary_stressors"]),
            "reasoning": record["reasoning"],
            "logic_clamp_notes": decision.notes,
            "support_priority": decision.support_priority,
            "context_flags": scores["context_flags"],
            "support_mean": scores["support_mean"],
            "factor_insights": self.factor_insights(record, scores),
        })
        return enriched

    def evaluate(self, record: dict) -> dict:
        """Run every business rule on the AI-enriched record."""
        return self.apply_logic(record)

    def score(self, record: dict) -> float | None:
        """The verified AI stress_score, or None if Logic rejects it.

        Logic never substitutes its own number: logic_stress_score on the
        evaluate() result is an audit value only.
        """
        result = self.apply_logic(record)
        if not result.get("ok"):
            return None
        return float(result["stress_score"])

    def route(self, record: dict) -> str:
        """"Low", "Moderate", "High", or "unavailable".

        Routes on the final risk_category only when the soft_label agrees.
        Pass the dict evaluate() returned.
        """
        if not isinstance(record, dict) or not record.get("ok"):
            return "unavailable"
        category = record.get("risk_category")
        if (category in _CATEGORY_TO_LABEL
                and record.get("soft_label") == _CATEGORY_TO_LABEL[category]):
            return str(category)
        return "unavailable"

    def score_answers(self, record: dict) -> dict:
        """Deterministic score of the 11 answers. Caller must pass usable numbers."""
        card = self.__scorer.score(record)
        category = card.risk_category
        return {
            "stress_score": card.stress_score,
            "scored_item_count": card.item_count,
            "support_mean": card.support_mean,
            "support_item_count": len(self.__scorer.SUPPORT_KEYS),
            "risk_category": category,
            "soft_label": self.__scorer.label_for(category),
            "speak_prominence": self.__scorer.prominence_for(category),
            "context_flags": card.context_flags,
        }

    def scored_value(self, key: str, answer: float) -> float:
        """Stress-direction value: sleep hours converted, positive items 6 - answer."""
        return self.__scorer.scored_value(key, answer)

    def sleep_hours_stress(self, hours: float) -> int:
        """Map typical nightly hours onto the 1–5 stress scale. Scoring only."""
        return self.__scorer.sleep_hours_stress(hours)

    def category_for_score(self, stress_score: float) -> str:
        """Low below 2.5, Moderate 2.5 to 3.5 inclusive, High above 3.5."""
        return self.__scorer.category_for(stress_score)

    def needs_support_priority(self, record: dict) -> bool:
        """True when Gemini rated High AND named low social support."""
        if not isinstance(record, dict):
            return False
        return SupportPriorityRule().applies(Decision(record, {}))

    def risk_score_for(self, stress_score: float) -> float:
        """Map the 1–5 stress score onto 0–1. Kept for compatibility."""
        return self.__scorer.risk_score_for(stress_score)

    def scored_items(self, answers: dict) -> dict[str, int]:
        """The 11 answers on the 1–5 stress scale. What Gemini must return."""
        return self.__scorer.scored_items(answers)

    def verify_ai_scores(self, ai_fields: dict, answers: dict) -> str | None:
        """Check Gemini's arithmetic and tier. An error string, or None.

        main.py passes this to the AI manager, which retries on any message.
        Never raises.
        """
        return self.__scorer.verify(ai_fields, answers)

    def scoring_instructions(self) -> str:
        """The scoring formula and tier rules, written for the Gemini prompt."""
        return self.__scorer.instructions()

    def context_flags(self, record: dict, support_mean: float) -> dict[str, bool]:
        """The four context flags from the raw answers."""
        return self.__scorer.context_flags(record, support_mean)

    def factor_insights(self, record: dict, scores: dict) -> list[dict]:
        """The four factor cards shown on the result page."""
        flags = scores["context_flags"]
        sleep_quality = int(record["sleep_quality"])
        workload = int(record["pas_workload"])
        finances = int(record["fin_stress"])
        return [
            {
                "title": "Rest & recovery",
                "flagged": flags[FLAG_SLEEP],
                "value": (
                    f"{self.__scorer.number(record['sleep_hours_avg']):g} hours · "
                    f"{SLEEP_QUALITY_OPTIONS[sleep_quality - 1]} quality"
                ),
                "text": (
                    "Sleep and stress affect each other. When sleep is short or "
                    "restless, everyday tasks can feel harder, and stress can make "
                    "it harder to sleep well."
                ),
            },
            {
                "title": "Study demands",
                "flagged": flags[FLAG_WORKLOAD],
                "value": f"{workload}/5 · {AGREE_OPTIONS[workload - 1]}",
                "text": (
                    "A heavy workload can leave less time to rest and recharge. "
                    "Feeling behind or overloaded is one of the most common sources "
                    "of student stress."
                ),
            },
            {
                "title": "Money pressures",
                "flagged": flags[FLAG_FINANCES],
                "value": f"{finances}/5 · {FIN_OPTIONS[finances - 1]}",
                "text": (
                    "Financial worries can add to the pressure of studying. "
                    "Concerns about money often weigh on focus and sleep."
                ),
            },
            {
                "title": "Support & connection",
                "flagged": flags[FLAG_SUPPORT],
                "value": (
                    f"{scores['support_mean']:g}/5 agreement across "
                    f"{scores['support_item_count']} answers"
                ),
                "text": (
                    "Having people to turn to can make stressful times easier to "
                    "handle. Strong support from friends or family helps protect "
                    "against stress."
                ),
            },
        ]

    def analyse_trend(self, history: StressHistory) -> Trend:
        """How this student's verified scores are moving. Not a diagnosis."""
        return self.__analyser.analyse(history)

    def within_tolerance(self, gemini_score: float, logic_score: float) -> bool:
        """True when Gemini is at most 0.01 away from Logic's score."""
        return self.__scorer.within_tolerance(gemini_score, logic_score)

    def __error_result(self, record: dict, *, missing: list[str],
                       invalid: list[str]) -> dict:
        """Pass the original fields through. ok=False is a hard stop."""
        result = dict(record) if isinstance(record, dict) else {}
        result.pop("feelings_text", None)
        result["ok"] = False
        result["error"] = ERROR_AI_FIELDS_REQUIRED
        result["missing"] = list(missing)
        result["invalid"] = list(invalid)
        result["message"] = (
            "Logic requires a successful Gemini (AI-enriched) record and all 11 "
            "answers; it does not guess outcomes without them."
        )
        return result

    def __score_mismatch_result(self, record: dict, gemini_score: float,
                                logic_score: float) -> dict:
        """Gemini's score disagrees with the answers. No outcome is produced."""
        result = dict(record)
        result.pop("feelings_text", None)
        result.update({
            "ok": False,
            "error": ERROR_SCORE_MISMATCH,
            "stress_score": record["stress_score"],
            "logic_stress_score": logic_score,
            "message": (
                f"Gemini stress_score {gemini_score:g} does not match the answers "
                f"({logic_score:g}); no result is given."
            ),
        })
        return result

    def __field_problems(self, record: dict) -> tuple[list[str], list[str]]:
        """(missing keys, unusable keys). Schema checks stay with the AI manager."""
        missing = [key for key in REQUIRED_AI_FIELDS + ANSWER_KEYS if key not in record]
        invalid = [
            key for key in ("stress_score",) + ANSWER_KEYS
            if key in record and not self.__scorer.answer_in_range(key, record[key])
        ]
        for key in ("tips", "primary_stressors"):
            if key in record and not isinstance(record[key], list):
                invalid.append(key)
        return missing, invalid


# main.py and the older tests still call logic_manager.apply_logic(...).
# These names are methods of one shared LogicManager. They are not a second
# copy of the scoring rules.
_default = LogicManager()

apply_logic = _default.apply_logic
evaluate = _default.evaluate
score = _default.score
route = _default.route
score_answers = _default.score_answers
scored_value = _default.scored_value
sleep_hours_stress = _default.sleep_hours_stress
category_for_score = _default.category_for_score
needs_support_priority = _default.needs_support_priority
risk_score_for = _default.risk_score_for
scored_items = _default.scored_items
verify_ai_scores = _default.verify_ai_scores
scoring_instructions = _default.scoring_instructions
context_flags = _default.context_flags
factor_insights = _default.factor_insights
analyse_trend = _default.analyse_trend
_within_tolerance = _default.within_tolerance
