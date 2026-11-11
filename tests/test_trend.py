"""Trend and TrendAnalyser on their own, without LogicManager."""
import unittest

from logic.trend import Trend
from logic.trend_analyser import TrendAnalyser
from plain_tests import case, function_suite
from records.check_in_record import CheckInRecord
from records.stress_history import StressHistory

ANALYSER = TrendAnalyser()
STUDENT = "2605581"


def history(*scores):
    """A history of finished check-ins, one per day, oldest first."""
    records = []
    for day, score in enumerate(scores, 1):
        records.append(CheckInRecord({
            "student_id": STUDENT,
            "survey_version": "evidence-v3",
            "stress_score": score,
            "risk_score": 0.5,
            "risk_category": "Moderate",
            "soft_label": "Worth a check-in",
            "primary_stressors": [],
            "recommended_support": "Internal",
            "confidence": 0.8,
            "reasoning": "test",
            "source": "ai_logic",
            "ai_ok": True,
            "saved_at": f"2026-10-{day:02d}T01:00:00+00:00",
        }))
    return StressHistory(STUDENT, records)


def test_trend_compares_the_latest_score_with_the_previous_one():
    cases = (((), Trend.NONE, None), ((3.0,), Trend.FIRST, None),
             ((3.0, 2.5), Trend.LOWER, -0.5), ((3.0, 3.25), Trend.SIMILAR,
                                               0.25),
             ((3.0, 2.8), Trend.SIMILAR, -0.2),
             ((2.0, 3.64), Trend.HIGHER, 1.64))
    for scores, comparison, change in cases:
        with case(scores=scores):
            trend = ANALYSER.analyse(history(*scores))
            assert trend.comparison == comparison
            assert trend.change == change


def test_trend_direction_needs_three_scores_and_uses_recent_ones():
    cases = (((3.0, 2.0), Trend.NOT_ENOUGH),
             ((4.0, 3.5, 3.0), Trend.EASING),
             ((2.0, 2.05, 1.95, 2.0), Trend.STEADY),
             ((1.5, 2.0, 2.6, 3.1), Trend.BUILDING),
             # Only the last five count: an old high is forgotten.
             ((5.0, 2.0, 2.0, 2.05, 1.95, 2.0), Trend.STEADY))
    for scores, direction in cases:
        with case(scores=scores):
            assert ANALYSER.analyse(history(*scores)).direction == direction
    trend = ANALYSER.analyse(history(4.0, 3.0, 2.0))
    assert (trend.latest, trend.previous, trend.average) == (2.0, 3.0, 3.0)


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's tests."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
