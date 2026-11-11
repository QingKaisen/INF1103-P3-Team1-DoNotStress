"""StressScorer, ScoreCard and ScoreAuditor on their own, without LogicManager."""
import unittest

import survey
from ai.score_auditor import ScoreAuditor
from logic.stress_scorer import StressScorer
from plain_tests import case, function_suite

SCORER = StressScorer()
KEYS = StressScorer.ANSWER_KEYS


def _answers(**changes):
    """All-3 answers with 6.5 h sleep: scored 3 each, average 3.0."""
    answers = {key: 3 for key in KEYS}
    answers["sleep_hours_avg"] = 6.5
    answers.update(changes)
    return answers


def _exact_ai_fields(answers):
    """The reply Gemini should give for these answers."""
    stress = SCORER.score(answers).stress_score
    category = SCORER.category_for(stress)
    return {
        "scored_items": SCORER.scored_items(answers),
        "stress_score": stress,
        "risk_category": category,
        "soft_label": SCORER.label_for(category),
        "speak_prominence": SCORER.prominence_for(category),
    }


def test_score_auditor_cannot_be_constructed():
    try:
        ScoreAuditor()
    except TypeError:
        return
    raise AssertionError("ScoreAuditor must be abstract")


def test_stress_scorer_is_a_score_auditor():
    assert isinstance(SCORER, ScoreAuditor)


def test_reverse_scoring_and_sleep_conversion():
    answers = dict(pss_1=4, pss_2=2, pss_3=1, pss_4=3, sleep_hours_avg=5.5,
                   sleep_quality=2, pas_workload=5, pas_catchup=1,
                   fin_stress=2, mspss_friends=5, mspss_family=4)
    # 4 + (6-2) + (6-1) + 3 + 4 (5.5 h) + 2 + 5 + 1 + 2 + (6-5) + (6-4)
    assert SCORER.score(answers).stress_score == 3.0


def test_sleep_hour_bands():
    for hours, value in [(14, 1), (8, 1), (7.5, 2), (7, 2), (6.5, 3),
                         (6, 3), (5.5, 4), (5, 4), (4.5, 5), (0, 5)]:
        assert SCORER.sleep_hours_stress(hours) == value, hours


def test_reversed_items_only():
    assert StressScorer.REVERSED_KEYS == (
        "pss_2", "pss_3", "mspss_friends", "mspss_family")
    assert StressScorer.REVERSED_KEYS == survey.REVERSED_KEYS
    for key in KEYS:
        if key == "sleep_hours_avg":
            continue
        expected = 5 if key in StressScorer.REVERSED_KEYS else 1
        assert SCORER.scored_value(key, 1) == expected, key


def test_tier_boundaries():
    assert SCORER.category_for(2.49) == "Low"
    assert SCORER.category_for(2.5) == "Moderate"
    assert SCORER.category_for(3.5) == "Moderate"
    assert SCORER.category_for(3.51) == "High"


def test_score_card_holds_the_audit_values():
    card = SCORER.score(_answers(mspss_friends=1, mspss_family=3))
    assert card.item_count == 11
    assert card.support_mean == 2
    assert card.context_flags["support"] is True
    assert card.risk_category == SCORER.category_for(card.stress_score)
    card.context_flags["support"] = False
    assert card.context_flags["support"] is True


def test_scored_items_are_what_gemini_must_return():
    lowest = _answers(pss_1=1, pss_2=5, pss_3=5, pss_4=1, sleep_hours_avg=8,
                      sleep_quality=1, pas_workload=1, pas_catchup=1,
                      fin_stress=1, mspss_friends=5, mspss_family=5)
    assert SCORER.scored_items(lowest) == {key: 1 for key in KEYS}
    assert all(isinstance(value, int)
               for value in SCORER.scored_items(lowest).values())
    assert SCORER.score(lowest).stress_score == 1.0
    for key in ("pss_2", "pss_3", "mspss_friends", "mspss_family"):
        with case(key=key):
            flipped = dict(lowest, **{key: 1})
            assert SCORER.scored_items(flipped)[key] == 5
            assert SCORER.score(flipped).stress_score == round(15 / 11, 2)
    for hours, band in ((14, 1), (8.5, 1), (8, 1), (7.5, 2), (7, 2),
                        (6.5, 3), (6, 3), (5.5, 4), (5, 4), (4.5, 5),
                        (0, 5)):
        with case(hours=hours):
            items = SCORER.scored_items(dict(lowest, sleep_hours_avg=hours))
            assert items["sleep_hours_avg"] == band


def test_verify_accepts_an_exact_reply():
    for answers in (_answers(),
                    _answers(pss_1=5, fin_stress=5, sleep_hours_avg=4.5),
                    _answers(pss_2=5, pss_3=5, mspss_friends=5,
                             mspss_family=5)):
        with case(answers=answers):
            assert SCORER.verify(_exact_ai_fields(answers), answers) is None
    within = dict(_exact_ai_fields(_answers()), stress_score=3.01)
    assert SCORER.verify(within, _answers()) is None


def test_exactly_0_01_off_is_accepted_and_more_is_rejected():
    """Reject a score MORE than 0.01 off; float error must not reject 0.01."""
    accepted = rejected = 0
    for total in range(11, 56):  # every possible sum of 11 items
        mean = round(total / 11, 2)
        for delta, should_pass in ((0.01, True), (-0.01, True),
                                   (0.02, False), (-0.02, False)):
            gemini = round(mean + delta, 2)
            if (not 1 <= gemini <= 5 or SCORER.category_for(gemini)
                    != SCORER.category_for(mean)):
                continue
            with case(mean=mean, gemini=gemini):
                assert SCORER.within_tolerance(gemini, mean) is should_pass
                accepted += should_pass
                rejected += not should_pass
    assert accepted > 50 and rejected > 50


def test_verify_rejects_wrong_tiers_items_and_scores():
    answers = _answers()
    exact = _exact_ai_fields(answers)
    cases = (
        (dict(exact, stress_score=2.0, risk_category="High",
              soft_label="Please reach out", speak_prominence="high"),
         "risk_category must be Low"),
        (dict(exact, soft_label="You're doing ok"), "soft_label must match"),
        (dict(exact, speak_prominence="high"), "speak_prominence must match"),
        (dict(exact, scored_items=dict(exact["scored_items"], pss_1=4)),
         "scored_items"),
        (dict(exact, stress_score=3.02), "stress_score must match"),
        (dict(exact, stress_score=3.4), "stress_score must match"),
    )
    for fields, expected in cases:
        with case(expected=expected):
            message = SCORER.verify(fields, answers)
            assert message is not None
            assert expected in message


def test_verify_never_raises_on_bad_input():
    exact = _exact_ai_fields(_answers())
    without_pss_1 = {k: v for k, v in _answers().items() if k != "pss_1"}
    assert SCORER.verify(None, _answers())
    assert SCORER.verify(exact, None)
    assert "pss_1" in SCORER.verify(exact, without_pss_1)
    assert "pss_1" in SCORER.verify(exact, _answers(pss_1=2.5))
    assert "pss_1" in SCORER.verify(exact, _answers(pss_1="three"))
    assert "stress_score" in SCORER.verify(
        dict(exact, stress_score="high"), _answers())


def test_scoring_instructions_state_the_rules():
    text = SCORER.instructions()
    for expected in ("pss_2, pss_3, mspss_friends, mspss_family are "
                     "positively worded",
                     "6 - answer", "8 or more hours -> 1", "5 or more -> 4",
                     "otherwise 5", "mean of those 11 converted values",
                     "rounded to 2 decimal places",
                     'below 2.5: risk_category Low, soft_label '
                     '"You\'re doing ok"',
                     "2.5 to 3.5 inclusive: risk_category Moderate",
                     'above 3.5: risk_category High, soft_label '
                     '"Please reach out"'):
        with case(expected=expected):
            assert expected in text


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's tests."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
