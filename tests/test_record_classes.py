"""Unit tests for the Record classes in records/."""

import unittest

from records.check_in_record import CheckInRecord
from records.pending_record import PendingRecord
from records.record import Record
from records.stress_history import StressHistory


def finished(saved_at: str, score: float) -> CheckInRecord:
    return CheckInRecord({
        "record_id": f"r-{saved_at}",
        "student_id": "2123456",
        "saved_at": saved_at,
        "source": "gemini",
        "stress_score": score,
        "risk_score": 0.5,
        "risk_category": "Moderate",
        "primary_stressors": ["workload"],
        "recommended_support": "talk_to_someone",
        "confidence": 0.8,
        "reasoning": "You have a lot on right now.",
        "reflection": "private text",
    })


class RecordTests(unittest.TestCase):

    def test_record_is_abstract(self):
        with self.assertRaises(TypeError):
            Record({})

    def test_reflection_never_stored(self):
        row = finished("2026-11-09T10:00:00Z", 3.0).to_row()
        self.assertNotIn("reflection", row)

    def test_valid_check_in_may_be_saved(self):
        record = finished("2026-11-09T10:00:00Z", 3.0)
        self.assertIsNone(record.problem())
        self.assertEqual(record.status, "evaluated")
        self.assertEqual(record.score, 3.0)

    def test_logic_fallback_is_refused(self):
        record = CheckInRecord({"source": "logic_fallback"})
        self.assertIn("not AI-processed", record.problem())

    def test_pending_record_has_no_score(self):
        record = PendingRecord({"student_id": "2123456"})
        row = record.to_row()
        self.assertIsNone(record.score)
        self.assertEqual(row["status"], "pending")
        self.assertFalse(row["ai_ok"])

    def test_subclasses_share_the_record_contract(self):
        for record in (finished("2026-11-09T10:00:00Z", 2.0),
                       PendingRecord({"student_id": "2123456"})):
            self.assertTrue(record.belongs_to("2123456"))
            self.assertFalse(record.belongs_to("2999999"))


class StressHistoryTests(unittest.TestCase):

    def test_history_sorted_oldest_first(self):
        history = StressHistory("2123456", [
            finished("2026-11-09T10:00:00Z", 4.0),
            finished("2026-11-02T10:00:00Z", 2.0),
        ], pending_count=1)
        self.assertEqual(history.scores, [2.0, 4.0])
        self.assertEqual(history.latest.score, 4.0)
        self.assertEqual(history.previous.score, 2.0)
        self.assertEqual(history.pending_count, 1)

    def test_records_list_is_a_copy(self):
        history = StressHistory("2123456", [finished("2026-11-09T10:00:00Z", 3.0)])
        history.records.clear()
        self.assertEqual(history.count, 1)


if __name__ == "__main__":
    unittest.main()
