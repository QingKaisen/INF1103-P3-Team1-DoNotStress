"""New Phase 2 feature: administrative cohort/tier summary.

CohortReport is a pure analysis service. It accepts already-authorised saved
records and returns only numeric aggregates; no student IDs, free text or
AI reasoning leave this class. Different reports can be added without edits
to DataManager or the persistence backend (OCP).
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any

from json_types import JSONObject


class CohortReport:
    """Summarise evaluated and pending check-ins by cohort and tier."""

    def build(self, records: list[dict[str, Any]]) -> JSONObject:
        """Build statistics; exclude invalid or non-AI processed scores."""
        cohorts: dict[str, dict[str, Any]] = {}
        overall_tiers: Counter[str] = Counter()
        evaluated_scores: list[float] = []
        pending = evaluated = 0
        for row in records:
            if not isinstance(row, dict):
                continue
            student_id = str(row.get('student_id', ''))
            cohort = (student_id[:2] if len(student_id) >= 2
                      and student_id[:2].isascii() and student_id[:2].isdigit() else 'Unknown')
            info = cohorts.setdefault(cohort, {'cohort': cohort, 'total': 0,
                                               'pending': 0, 'evaluated': 0,
                                               'scores': [], 'tiers': Counter()})
            info['total'] += 1
            source = str(row.get('source', '')).strip().lower()
            is_pending = (str(row.get('status', '')).strip().lower() == 'pending'
                          or not (row.get('ai_ok') is True or source in {
                              'gemini', 'ai', 'ai_manager', 'gemini+logic', 'ai_logic'}))
            if is_pending:
                pending += 1
                info['pending'] += 1
                continue
            evaluated += 1
            info['evaluated'] += 1
            tier = row.get('risk_category')
            if isinstance(tier, str) and tier in {'Low', 'Moderate', 'High'}:
                overall_tiers[tier] += 1
                info['tiers'][tier] += 1
            try:
                number = float(row.get('stress_score'))
            except (TypeError, ValueError):
                continue
            if isfinite(number) and 1.0 <= number <= 5.0:
                evaluated_scores.append(number)
                info['scores'].append(number)
        result = []
        for cohort, item in sorted(cohorts.items(), key=lambda entry: entry[0], reverse=True):
            scores = item['scores']
            result.append({
                'cohort': cohort,
                'total': item['total'],
                'pending': item['pending'],
                'evaluated': item['evaluated'],
                'average_stress': round(sum(scores) / len(scores), 2) if scores else None,
                'tiers': {key: item['tiers'][key] for key in ('Low', 'Moderate', 'High')},
            })
        return {
            'total': pending + evaluated,
            'evaluated': evaluated,
            'pending': pending,
            'average_stress': (round(sum(evaluated_scores) / len(evaluated_scores), 2)
                               if evaluated_scores else None),
            'tiers': {key: overall_tiers[key] for key in ('Low', 'Moderate', 'High')},
            'cohorts': result,
            'note': 'Descriptive project heuristics; not clinical thresholds.',
        }

    def browser_summary(self, records: list[dict[str, Any]]) -> JSONObject:
        """Staff cards for the browser records page. No student identifiers."""
        rows = [row for row in records if isinstance(row, dict)]
        evaluated_rows = [row for row in rows if self._evaluated(row)]
        whole = len(evaluated_rows)
        tiers = []
        for category, label in _TIER_LABELS:
            count = sum(1 for row in evaluated_rows if row.get('risk_category') == category)
            tiers.append({
                'category': category,
                'label': label,
                'count': count,
                'percent': round(100.0 * count / whole, 1) if whole else 0.0,
            })
        scores: list[float] = []
        for row in evaluated_rows:
            try:
                number = float(row.get('stress_score'))
            except (TypeError, ValueError):
                continue
            if isfinite(number) and 1.0 <= number <= 5.0:
                scores.append(number)
        stressors: Counter[str] = Counter()
        for row in evaluated_rows:
            named = row.get('primary_stressors')
            if isinstance(named, (list, tuple)):
                stressors.update(item for item in named if isinstance(item, str) and item)
        ranked = sorted(stressors.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
        factors = []
        for flag, label in _FACTOR_LABELS:
            count = sum(
                1 for row in evaluated_rows
                if isinstance(row.get('context_flags'), dict)
                and row['context_flags'].get(flag) is True
            )
            factors.append({
                'id': flag,
                'label': label,
                'count': count,
                'percent': round(100.0 * count / whole, 1) if whole else 0.0,
            })
        return {
            'checkins': {
                'total': len(rows),
                'evaluated': whole,
                'pending': len(rows) - whole,
            },
            'tiers': {'items': tiers},
            'stress': {
                'average_score': round(sum(scores) / len(scores), 2) if scores else None,
                'support_priority': sum(1 for row in evaluated_rows if row.get('support_priority') is True),
                'safety_flags': sum(1 for row in evaluated_rows if row.get('safety_flag') is True),
            },
            'stressors': {
                'items': [{'id': name, 'label': _phrase(name), 'count': count}
                          for name, count in ranked],
            },
            'factors': {'items': factors},
        }

    @staticmethod
    def _evaluated(row: dict[str, Any]) -> bool:
        source = str(row.get('source', '')).strip().lower()
        processed = row.get('ai_ok') is True or source in _AI_SOURCES
        pending = str(row.get('status', '')).strip().lower() == 'pending'
        return processed and not pending


_TIER_LABELS = (
    ('Low', "You're doing ok"),
    ('Moderate', 'Worth a check-in'),
    ('High', 'Please reach out'),
)
_FACTOR_LABELS = (
    ('sleep', 'Rest & recovery'),
    ('workload', 'Study demands'),
    ('finances', 'Money pressures'),
    ('support', 'Support & connection'),
)
_AI_SOURCES = frozenset({'gemini', 'ai', 'ai_manager', 'gemini+logic', 'ai_logic'})


def _phrase(token: str) -> str:
    text = token.replace('_', ' ').strip()
    return text[:1].upper() + text[1:] if text else token
