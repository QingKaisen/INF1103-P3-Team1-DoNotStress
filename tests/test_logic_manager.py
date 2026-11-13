"""Tests for logic_manager: Logic validates Gemini's analysis, not replaces it.

No real Gemini call: the AI fields come from a fake generate_fn run through
ai_manager's own validation, or are written by hand for disagreement cases.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai_manager
import data_manager
import logic_manager as lm
from plain_tests import case, function_suite


def _answers(**changes):
    """All-3 answers with 6.5 h sleep: scored 3 each, average 3.0 (Moderate)."""
    answers = {key: 3 for key in lm.ANSWER_KEYS}
    answers["sleep_hours_avg"] = 6.5
    answers.update(changes)
    return answers


def _fake_generate_fn(**overrides):
    """A fake Gemini that replays the formula on the prompt's answers."""
    def generate(prompt, api_key, model_name):
        answers = json.loads(prompt[prompt.rfind("{"):])
        scored = lm.scored_items(answers)
        stress = lm.score_answers(answers)["stress_score"]
        category = lm.category_for_score(stress)
        reply = {
            "scored_items": scored,
            "stress_score": stress,
            "risk_category": category,
            "primary_stressors": ["academic_overload"],
            "confidence": 0.8,
            "reasoning": "Gemini reasoning for the student.",
            "soft_label": lm._CATEGORY_TO_LABEL[category],
            "tips": ["short_breaks", "rest_a_little_more"],
            "speak_prominence": lm._CATEGORY_TO_PROMINENCE[category],
        }
        reply.update(overrides)
        return json.dumps(reply)
    return generate


def _enriched(answers=None, **ai_overrides):
    """Run the real ai_manager.analyse_student with a fake generate_fn."""
    record = {"student_id": "2605581", "survey_version": "evidence-v3",
              "safety_flag": False, **(answers or _answers())}
    ok, enriched, error = ai_manager.analyse_student(
        record, generate_fn=_fake_generate_fn(**ai_overrides),
        max_attempts=1, retry_delay_sec=0,
    )
    assert ok, error
    return enriched


def _with_gemini_score(record, stress_score):
    """Replace Gemini's score with one that may disagree with the answers.

    ai_manager would reject a reply over 0.01 off, so this is set by hand to
    test Logic's own cross-check.
    """
    record = dict(record)
    record.update(stress_score=stress_score, risk_score=(stress_score - 1) / 4)
    return record


# --- Cross Check -----------------------------------------------------------

def test_gemini_match_is_kept_untouched():
    enriched = _enriched()
    result = lm.apply_logic(enriched)
    assert result["ok"]
    for key in ("stress_score", "risk_category", "soft_label",
                "speak_prominence", "tips", "primary_stressors", "reasoning"):
        assert result[key] == enriched[key], key
    assert result["risk_score"] == (result["stress_score"] - 1) / 4
    assert result["logic_stress_score"] == 3.0
    assert result["logic_clamp_notes"] == []


def test_gemini_within_0_01_is_kept_untouched():
    record = _with_gemini_score(_enriched(), 3.01)
    result = lm.apply_logic(record)
    assert result["ok"]
    assert result["stress_score"] == 3.01
    assert result["logic_stress_score"] == 3.0
    for key in ("risk_score", "risk_category", "soft_label",
                "speak_prominence", "reasoning"):
        assert result[key] == record[key], key
    assert result["logic_clamp_notes"] == []


def test_mismatch_over_0_01_returns_error():
    for gemini_score in (3.02, 2.98, 3.5, 2.1, 4.9):
        with case(gemini_score=gemini_score):
            record = _with_gemini_score(_enriched(), gemini_score)
            record["feelings_text"] = "private"
            result = lm.apply_logic(record)
            assert not result["ok"]
            assert result["error"] == lm.ERROR_SCORE_MISMATCH
            assert result["stress_score"] == gemini_score
            assert result["logic_stress_score"] == 3.0
            assert "feelings_text" not in result
            # No outcome is invented on a mismatch.
            assert "logic_rule" not in result
            assert "factor_insights" not in result


def test_mismatch_is_an_ai_failure_in_the_pipeline():
    import main
    record = _with_gemini_score(_enriched(), 4.5)
    with patch.object(main.ai_manager, "analyse_student",
                      return_value=(True, record, None)):
        ok, outcome = main.process_checkin(dict(record))
    assert not ok
    assert outcome == "unavailable"


# --- Scoring ---------------------------------------------------------------

def test_reverse_scoring_and_sleep_conversion():
    answers = dict(pss_1=4, pss_2=2, pss_3=1, pss_4=3, sleep_hours_avg=5.5,
                   sleep_quality=2, pas_workload=5, pas_catchup=1,
                   fin_stress=2, mspss_friends=5, mspss_family=4)
    # 4 + (6-2) + (6-1) + 3 + 4 (5.5 h) + 2 + 5 + 1 + 2 + (6-5) + (6-4) = 33
    assert lm.score_answers(answers)["stress_score"] == 3.0


def test_sleep_hour_bands():
    for hours, value in [(14, 1), (8, 1), (7.5, 2), (7, 2), (6.5, 3),
                         (6, 3), (5.5, 4), (5, 4), (4.5, 5), (0, 5)]:
        assert lm.sleep_hours_stress(hours) == value, hours


def test_reversed_items_only():
    assert lm.REVERSED_KEYS == ("pss_2", "pss_3", "mspss_friends", "mspss_family")
    for key in lm.ANSWER_KEYS:
        if key == "sleep_hours_avg":
            continue
        expected = 5 if key in lm.REVERSED_KEYS else 1
        assert lm.scored_value(key, 1) == expected, key


def test_tier_boundaries():
    assert lm.category_for_score(2.49) == "Low"
    assert lm.category_for_score(2.5) == "Moderate"
    assert lm.category_for_score(3.5) == "Moderate"
    assert lm.category_for_score(3.51) == "High"


# --- Flag Tip --------------------------------------------------------------

FLAG_CASES = (
    ("sleep", dict(sleep_hours_avg=5.5), "sleep_routine"),
    ("sleep", dict(sleep_quality=4), "sleep_routine"),
    ("workload", dict(pas_workload=4), "workload_chunks"),
    ("finances", dict(fin_stress=4), "money_worries"),
    ("support", dict(mspss_friends=2, mspss_family=2), "talk_to_someone"),
)


def test_each_flag_adds_its_tip():
    for flag, changes, tip in FLAG_CASES:
        with case(flag=flag, changes=changes):
            result = lm.apply_logic(_enriched(_answers(**changes)))
            assert result["context_flags"][flag]
            assert tip in result["tips"]
            assert result["tips"][:2] == ["short_breaks", "rest_a_little_more"]
            assert len(result["tips"]) <= lm.MAX_TIPS


def test_no_flag_adds_no_tip():
    result = lm.apply_logic(_enriched())
    assert not any(result["context_flags"].values())
    assert result["tips"] == ["short_breaks", "rest_a_little_more"]


def test_all_flags_never_exceed_four_or_replace_gemini_tips():
    gemini_tips = ["short_breaks", "rest_a_little_more", "keep_social_contact"]
    answers = _answers(sleep_hours_avg=4, pas_workload=5, fin_stress=5,
                       mspss_friends=1, mspss_family=1)
    result = lm.apply_logic(_enriched(answers, tips=gemini_tips))
    assert all(result["context_flags"].values())
    assert len(result["tips"]) == 4
    assert result["tips"][:3] == gemini_tips


def test_flags_never_change_the_tier():
    # Low average but workload and finances flagged.
    answers = {key: 1 for key in lm.ANSWER_KEYS}
    answers.update(sleep_hours_avg=8, pss_2=5, pss_3=5, mspss_friends=5,
                   mspss_family=5, pas_workload=5, fin_stress=5)
    result = lm.apply_logic(_enriched(answers))
    assert result["risk_category"] == "Low"
    assert "workload_chunks" in result["tips"]
    assert "money_worries" in result["tips"]


# --- Safety ----------------------------------------------------------------

def test_safety_flag_forces_reach_out():
    record = _enriched(_answers(pss_1=1, pss_4=1))
    record["safety_flag"] = True
    record["feelings_text"] = "should never survive Logic"
    result = lm.apply_logic(record)
    assert result["risk_category"] == "High"
    assert result["soft_label"] == "Please reach out"
    assert result["speak_prominence"] == "high"
    assert result["tips"][0] == "talk_to_someone"
    assert len(result["tips"]) <= lm.MAX_TIPS
    assert result["stress_score"] == record["stress_score"]
    assert any(n.startswith("safety_override") for n in result["logic_clamp_notes"])
    assert "feelings_text" not in result


def test_safety_moves_existing_talk_tip_first_without_dropping_gemini_tips():
    gemini_tips = ["short_breaks", "rest_a_little_more", "talk_to_someone"]
    record = _enriched(tips=gemini_tips)
    record["safety_flag"] = True
    result = lm.apply_logic(record)
    assert result["tips"][0] == "talk_to_someone"
    assert set(gemini_tips) <= set(result["tips"])


def test_only_true_safety_flag_counts():
    record = _enriched(_answers(pss_1=1, pss_4=1))
    record["safety_flag"] = "yes"
    assert lm.apply_logic(record)["risk_category"] != "High"


# --- Output ----------------------------------------------------------------

def test_output_contract():
    record = _enriched()
    record["feelings_text"] = "private"
    result = lm.apply_logic(record)
    assert result["source"] == "ai_logic"
    assert result["ai_ok"] is True
    assert result["logic_rule"] == "v3"
    for key in ("stress_score", "logic_stress_score", "risk_category", "soft_label",
                "speak_prominence", "tips", "primary_stressors", "reasoning",
                "risk_score", "logic_clamp_notes", "factor_insights"):
        assert key in result
    assert [card["title"] for card in result["factor_insights"]] == (["Rest & recovery", "Study demands", "Money pressures",
                      "Support & connection"])
    assert "feelings_text" not in result


def test_input_is_not_mutated():
    record = _enriched()
    snapshot = json.dumps(record, sort_keys=True)
    lm.apply_logic(record)
    assert json.dumps(record, sort_keys=True) == snapshot


# --- Missing Field ---------------------------------------------------------

def test_missing_ai_field_gives_error_dict():
    for key in lm.REQUIRED_AI_FIELDS:
        with case(key=key):
            record = _enriched()
            del record[key]
            result = lm.apply_logic(record)
            assert not result["ok"]
            assert result["error"] == lm.ERROR_AI_FIELDS_REQUIRED
            assert key in result["missing"]


def test_missing_answer_gives_error_dict():
    for key in lm.ANSWER_KEYS:
        with case(key=key):
            record = _enriched()
            del record[key]
            result = lm.apply_logic(record)
            assert not result["ok"]
            assert key in result["missing"]


def test_unusable_numbers_give_error_dict():
    for key, bad in (("stress_score", "high"), ("pss_1", None),
                     ("sleep_quality", 9), ("sleep_hours_avg", float("nan")),
                     ("fin_stress", 10 ** 400)):
        with case(key=key, bad=bad):
            record = _enriched()
            record[key] = bad
            result = lm.apply_logic(record)
            assert not result["ok"]
            assert key in result["invalid"]


def test_non_dict_gives_error_dict():
    for bad in (None, [], "record"):
        result = lm.apply_logic(bad)
        assert not result["ok"]
        assert "record" in result["invalid"]


# --- Legacy Removed --------------------------------------------------------

LEGACY = ("stress_level", "academic_workload", "financial_stress", "social_support",
          "ANSWER_MAX", "PSS4", "_stress_to_pss4", "_rescale",
          "REQUIRED_STUDENT_FIELDS", "0.40", "0.75", "finalise")


def test_no_legacy_names_in_logic_or_survey():
    for name in ("logic_manager.py", "survey.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for legacy in LEGACY:
            with case(file=name, legacy=legacy):
                # Start of a name only, so the real stressor ID
                # "low_social_support" is not mistaken for "social_support".
                assert re.search(r"(?<![A-Za-z0-9_])" + re.escape(legacy), text) is None


def test_survey_only_holds_config_and_safety():
    import survey
    for gone in ("score", "finalise", "factor_insights", "explanation",
                 "scored_value", "sleep_hours_stress"):
        assert not hasattr(survey, gone), gone
    assert callable(survey.safety_check)


# --- Scoring rules handed to ai_manager ---------------------------------------
# main.py passes scoring_instructions() and verify_ai_scores() to ai_manager,
# so these rules exist only here.

def _exact_ai_fields(answers):
    """The reply Gemini should give for these answers."""
    stress = lm.score_answers(answers)["stress_score"]
    category = lm.category_for_score(stress)
    return {
        "scored_items": lm.scored_items(answers),
        "stress_score": stress,
        "risk_category": category,
        "soft_label": lm._CATEGORY_TO_LABEL[category],
        "speak_prominence": lm._CATEGORY_TO_PROMINENCE[category],
    }


def test_scored_items_are_what_gemini_must_return():
    lowest = _answers(pss_1=1, pss_2=5, pss_3=5, pss_4=1, sleep_hours_avg=8, sleep_quality=1,
                      pas_workload=1, pas_catchup=1, fin_stress=1, mspss_friends=5, mspss_family=5)
    assert lm.scored_items(lowest) == {key: 1 for key in lm.ANSWER_KEYS}
    assert all(isinstance(value, int) for value in lm.scored_items(lowest).values())
    assert lm.score_answers(lowest)["stress_score"] == 1.0
    for key in ("pss_2", "pss_3", "mspss_friends", "mspss_family"):
        with case(key=key):
            flipped = dict(lowest, **{key: 1})
            assert lm.scored_items(flipped)[key] == 5
            assert lm.score_answers(flipped)["stress_score"] == round(15 / 11, 2)
    for hours, band in ((14, 1), (8.5, 1), (8, 1), (7.5, 2), (7, 2), (6.5, 3), (6, 3),
                        (5.5, 4), (5, 4), (4.5, 5), (0, 5)):
        with case(hours=hours):
            assert lm.scored_items(dict(lowest, sleep_hours_avg=hours))["sleep_hours_avg"] == band


def test_verify_accepts_an_exact_reply():
    for answers in (_answers(), _answers(pss_1=5, fin_stress=5, sleep_hours_avg=4.5),
                    _answers(pss_2=5, pss_3=5, mspss_friends=5, mspss_family=5)):
        with case(answers=answers):
            assert lm.verify_ai_scores(_exact_ai_fields(answers), answers) is None
    within = dict(_exact_ai_fields(_answers()), stress_score=3.01)
    assert lm.verify_ai_scores(within, _answers()) is None


def test_exactly_0_01_off_is_accepted_and_more_is_rejected():
    """Brief: reject a score MORE than 0.01 off. Float error must not reject exactly 0.01."""
    accepted = rejected = 0
    for total in range(11, 56):                       # every possible sum of 11 items
        mean = round(total / 11, 2)
        for delta, should_pass in ((0.01, True), (-0.01, True), (0.02, False), (-0.02, False)):
            gemini = round(mean + delta, 2)
            if not 1 <= gemini <= 5 or lm.category_for_score(gemini) != lm.category_for_score(mean):
                continue
            with case(mean=mean, gemini=gemini):
                assert lm._within_tolerance(gemini, mean) is should_pass
                accepted += should_pass
                rejected += not should_pass
    assert accepted > 50 and rejected > 50


def test_verify_rejects_wrong_tiers_items_and_scores():
    answers = _answers()
    exact = _exact_ai_fields(answers)
    cases = (
        (dict(exact, stress_score=2.0, risk_category="High", soft_label="Please reach out",
              speak_prominence="high"), "risk_category must be Low"),
        (dict(exact, soft_label="You're doing ok"), "soft_label must match"),
        (dict(exact, speak_prominence="high"), "speak_prominence must match"),
        (dict(exact, scored_items=dict(exact["scored_items"], pss_1=4)), "scored_items"),
        (dict(exact, stress_score=3.02), "stress_score must match"),
        (dict(exact, stress_score=3.4), "stress_score must match"),
    )
    for fields, expected in cases:
        with case(expected=expected):
            message = lm.verify_ai_scores(fields, answers)
            assert message is not None
            assert expected in message


def test_verify_never_raises_on_bad_input():
    exact = _exact_ai_fields(_answers())
    assert lm.verify_ai_scores(None, _answers())
    assert lm.verify_ai_scores(exact, None)
    assert "pss_1" in lm.verify_ai_scores(exact, {k: v for k, v in _answers().items() if k != "pss_1"})
    assert "pss_1" in lm.verify_ai_scores(exact, _answers(pss_1=2.5))
    assert "pss_1" in lm.verify_ai_scores(exact, _answers(pss_1="three"))
    assert "stress_score" in lm.verify_ai_scores(dict(exact, stress_score="high"), _answers())


def test_scoring_instructions_state_the_rules():
    text = lm.scoring_instructions()
    for expected in ("pss_2, pss_3, mspss_friends, mspss_family are positively worded",
                     "6 - answer", "8 or more hours -> 1", "5 or more -> 4", "otherwise 5",
                     "mean of those 11 converted values", "rounded to 2 decimal places",
                     'below 2.5: risk_category Low, soft_label "You\'re doing ok"',
                     "2.5 to 3.5 inclusive: risk_category Moderate",
                     'above 3.5: risk_category High, soft_label "Please reach out"'):
        with case(expected=expected):
            assert expected in text


def test_pipeline_retries_and_rejects_wrong_arithmetic():
    """main.process_checkin wires verify_ai_scores into ai_manager.

    A reply with one wrong scored item is never accepted: every model in the
    chain is tried, up to the call cap, then the check-in fails.
    """
    import main
    calls = []
    good = json.loads(_fake_generate_fn()("x " + json.dumps(_answers()), "", ""))

    def generate(prompt, api_key, model_name):
        calls.append(model_name)
        return json.dumps(dict(good, scored_items=dict(good["scored_items"], pss_1=4)))

    record = {"student_id": "2605581", "survey_version": "evidence-v3",
              "safety_flag": False, **_answers()}
    ok, outcome = main.process_checkin(record, generate_fn=generate, max_attempts=3,
                                       retry_delay_sec=0)
    assert not ok
    assert outcome == "invalid_response"
    assert len(calls) == ai_manager.MAX_TOTAL_CALLS

    sent = {}

    def capture(prompt, api_key, model_name):
        sent["prompt"] = prompt
        return json.dumps(good)

    ok, _ = main.process_checkin(dict(record), generate_fn=capture, max_attempts=1,
                                 retry_delay_sec=0)
    assert ok
    assert lm.scoring_instructions() in sent["prompt"]


def test_risk_score_and_recommended_support_come_from_logic():
    result = lm.apply_logic(_enriched())
    assert result["ok"]
    assert result["risk_score"] == 0.5
    assert result["recommended_support"] == lm.RECOMMENDED_SUPPORT["Moderate"]

    record = _enriched()
    record["safety_flag"] = True
    forced = lm.apply_logic(record)
    assert forced["risk_category"] == "High"
    assert forced["recommended_support"] == lm.RECOMMENDED_SUPPORT["High"]

    # The data layer still accepts the finished record.
    with tempfile.TemporaryDirectory() as temp_dir:
        saved = data_manager.save_record(result, str(Path(temp_dir) / "records.json"), opt_in=True)
        assert saved["ok"], saved["error"]


# --- Support priority (multi-condition rule on two AI fields) -------------

_HIGH_STRESS = dict(pss_1=5, pss_2=1, pss_3=1, pss_4=5, sleep_hours_avg=4.5, sleep_quality=5,
                    pas_workload=5, pas_catchup=5, fin_stress=5, mspss_friends=1, mspss_family=1)


def test_support_priority_rule_needs_both_ai_fields():
    high = {"risk_category": "High", "primary_stressors": ["low_social_support"]}
    assert lm.needs_support_priority(high)
    assert not lm.needs_support_priority(dict(high, risk_category="Moderate"))
    assert not lm.needs_support_priority(dict(high, primary_stressors=["academic_overload"]))
    assert not lm.needs_support_priority(dict(high, primary_stressors=[]))
    assert not lm.needs_support_priority(dict(high, primary_stressors="low_social_support"))
    assert not lm.needs_support_priority({})


def test_high_tier_with_low_support_puts_talking_first():
    record = _enriched(_answers(**_HIGH_STRESS),
                       primary_stressors=["low_social_support", "academic_overload"])
    result = lm.apply_logic(record)
    assert result["ok"]
    assert result["risk_category"] == "High"
    assert result["support_priority"] is True
    assert result["tips"][0] == "talk_to_someone"
    assert "keep_social_contact" in result["tips"]
    for tip in record["tips"]:                      # Gemini's own tips are kept
        assert tip in result["tips"]
    assert len(result["tips"]) <= lm.MAX_TIPS
    assert "support_priority: High tier with low social support" in result["logic_clamp_notes"]


def test_support_priority_does_not_fire_on_one_field_alone():
    high_no_support = lm.apply_logic(
        _enriched(_answers(**_HIGH_STRESS), primary_stressors=["academic_overload"]))
    assert high_no_support["risk_category"] == "High"
    assert high_no_support["support_priority"] is False
    assert high_no_support["tips"][0] != "talk_to_someone"

    moderate_low_support = lm.apply_logic(
        _enriched(_answers(), primary_stressors=["low_social_support"]))
    assert moderate_low_support["risk_category"] == "Moderate"
    assert moderate_low_support["support_priority"] is False
    assert moderate_low_support["logic_clamp_notes"] == []


def test_support_priority_and_safety_together_do_not_repeat_tips():
    record = _enriched(_answers(**_HIGH_STRESS), primary_stressors=["low_social_support"])
    record["safety_flag"] = True
    result = lm.apply_logic(record)
    assert result["tips"][0] == "talk_to_someone"
    assert len(result["tips"]) == len(set(result["tips"]))
    assert result["support_priority"] is True


def test_manager_runs_rules_polymorphically_and_can_drop_one():
    """The refinement is in use, and the rule list can change on its own.

    1. LogicManager is a BaseManager. Its default rules are the four v3
       subclasses, and the test holds them only as BusinessRule (Liskov).
    2. Open/closed: a manager built with only TipCapRule does not add the
       sleep tip. evaluate() was not edited to switch that off; the list
       passed in was shorter.
    3. analyse_trend reads a StressHistory that storage owns. The manager
       does not own those records (association). Here the later score is
       lower, so the comparison is LOWER and the change is -0.8.
    """
    from base_manager import BaseManager
    from logic.business_rule import BusinessRule
    from logic.tip_cap_rule import TipCapRule
    from logic.trend import Trend
    from records.check_in_record import CheckInRecord
    from records.stress_history import StressHistory

    manager = lm.LogicManager()
    assert isinstance(manager, BaseManager)
    assert [rule.name for rule in manager.rules] == [
        "safety_override", "support_priority", "context_flags", "tip_cap"]
    assert all(isinstance(rule, BusinessRule) for rule in manager.rules)
    assert manager.describe()["logic_rule"] == "v3"

    sleepy = _enriched(_answers(sleep_hours_avg=5.5))
    assert "sleep_routine" in manager.apply_logic(sleepy)["tips"]
    capped_only = lm.LogicManager(rules=[TipCapRule()])
    without_flags = capped_only.apply_logic(sleepy)
    assert without_flags["ok"]
    assert "sleep_routine" not in without_flags["tips"]
    assert without_flags["tips"] == sleepy["tips"]

    history = StressHistory("2605581", [
        CheckInRecord({
            "student_id": "2605581", "stress_score": 3.2,
            "saved_at": "2026-10-01T01:00:00+00:00", "source": "ai_logic",
            "ai_ok": True,
        }),
        CheckInRecord({
            "student_id": "2605581", "stress_score": 2.4,
            "saved_at": "2026-10-02T01:00:00+00:00", "source": "ai_logic",
            "ai_ok": True,
        }),
    ])
    trend = manager.analyse_trend(history)
    assert trend.comparison == Trend.LOWER
    assert trend.change == -0.8


def test_manager_uses_the_injected_scorer():
    """DIP: apply_logic audits with the Scorer it was given, not a hidden one.

    Raising the injected audit score to 4.0 makes a genuine 3.0 Gemini
    reply fail the 0.01 check. The default manager, given no scorer,
    still accepts that same reply.
    """
    from logic.score_card import ScoreCard
    from logic.scorer import Scorer
    from logic.stress_scorer import StressScorer

    class HighAudit(StressScorer):
        """Same formula, then reports 4.0 so the cross-check can be seen."""

        def score(self, answers):
            card = super().score(answers)
            return ScoreCard(4.0, card.scored_items, card.support_mean,
                             "High", card.context_flags)

    scorer = HighAudit()
    assert isinstance(scorer, Scorer)
    record = _enriched()
    injected = lm.LogicManager(scorer=scorer).apply_logic(record)
    assert injected["ok"] is False
    assert injected["error"] == lm.ERROR_SCORE_MISMATCH
    assert injected["logic_stress_score"] == 4.0
    assert lm.LogicManager().apply_logic(record)["ok"] is True


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
