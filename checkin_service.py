"""Object-oriented coordinator of the four manager responsibilities.

The service never reads a Flask request, sends response HTML, or accesses a
file itself. It composes IOManager, AIManager, LogicManager and DataManager
through explicit object references (association). A successful result is
possible only after an AIProvider has generated validated JSON and the Logic
layer has verified its arithmetic and rules.
"""
from __future__ import annotations

import hashlib
import json
import logging

from ai_manager import AIManager
from data_manager import DataManager
from io_manager import IOManager
from logic_manager import LogicManager
from reports.cohort_report import CohortReport

logger = logging.getLogger(__name__)


class CheckinService:
    """Coordinate one check-in and prepare its safe student-facing result."""

    def __init__(self, io: IOManager, logic: LogicManager,
                 data: DataManager, *, reporter: CohortReport | None = None) -> None:
        self.__reporter = reporter if reporter is not None else CohortReport()
        self.__io = io
        self.__logic = logic
        self.__data = data

    @property
    def io(self) -> IOManager:
        return self.__io

    @property
    def logic(self) -> LogicManager:
        return self.__logic

    @property
    def data(self) -> DataManager:
        return self.__data

    def summarize_records(self, records: list[dict]) -> dict:
        """Generate the new administrative summary without exposing identifiers."""
        return self.__reporter.build(records)

    def staff_cards(self, records: list[dict]) -> dict:
        """Browser cohort cards for the current filter. No student identifiers."""
        return self.__reporter.browser_summary(records)

    def student_history(self, student_id: str) -> dict:
        """Scores and the trend for one student. No reflection text."""
        history = self.__data.history_for(student_id)
        trend = self.__logic.analyse_trend(history)
        return {
            "count": history.count,
            "pending_count": history.pending_count,
            "scores": history.scores,
            "comparison": trend.comparison,
            "direction": trend.direction,
            "latest": trend.latest,
            "previous": trend.previous,
            "change": trend.change,
        }

    def fingerprint(self, record: dict) -> str:
        """Stable identity for same answers; free text is deliberately excluded."""
        answers = [record.get(question['key']) for question in self.__io.survey.questions]
        payload = json.dumps(
            [record.get('student_id'), answers, record.get('survey_version')],
            separators=(',', ':'), ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def process_checkin(self, record: dict, *, ai: AIManager) -> tuple[bool, dict | str]:
        """AI → deterministic Logic. Never substitute a calculated-only result."""
        ok, enriched, error = ai.analyse_student(
            record, questions=self.__io.survey.questions,
            instructions=self.__logic.scoring_instructions(),
            verify_fn=self.__logic.verify_ai_scores,
        )
        if not ok or not isinstance(enriched, dict):
            code = error.get('error_code', 'unavailable') if isinstance(error, dict) else 'unavailable'
            logger.warning('Check-in could not complete: %s', code)
            return False, code
        finalized = self.__logic.apply_logic(enriched)
        if not isinstance(finalized, dict) or not finalized.get('ok'):
            return False, 'unavailable'
        return True, finalized

    def result_view(self, record: dict, saved: bool = False) -> dict:
        """Allow-list output for the browser; never expose raw reflection text."""
        advisor = self.__io.format_speak_to_advisor_panel(record['speak_prominence'])
        advisor.pop('cta', None)
        return {
            'soft': self.__io.format_soft_label(record['soft_label']),
            'tips': self.__io.format_tips(record['tips']),
            'advisor': advisor,
            'saved': saved,
            'insights': {
                'risk_category': record.get('risk_category', 'Unknown'),
                'explanation': str(record.get('reasoning', '')).split(' Logic adjusted:')[0],
                'stressors': record.get('primary_stressors', []),
                'survey_version': record.get('survey_version', 'legacy'),
                'stress_score': record.get('stress_score'),
                'support_mean': record.get('support_mean'),
                'context_flags': record.get('context_flags', {}),
                'factors': record.get('factor_insights', []),
                'safety_flag': record.get('safety_flag', False),
                'snapshot': {key: record.get(key) for key in (
                    'sleep_hours', 'stress_level', 'academic_workload',
                    'financial_stress', 'social_support', *self.__io.survey.question_map)},
            },
        }
