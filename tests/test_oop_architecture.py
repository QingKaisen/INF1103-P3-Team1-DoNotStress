"""Phase 2 OOP design regression: class contracts, LSP, privacy and summaries.

No Flask, network or live Gemini API is needed. See test_admin_summary.py for
additional HTTP integration tests when the full requirements are installed.
"""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from ai_manager import AIManager
from base_manager import BaseManager
from checkin_service import CheckinService
from data_manager import DataManager
from io_manager import IOManager
from survey_definition import SurveyDefinition
import survey
from isolated_memory_storage import IsolatedMemoryStorage
from logic_manager import LogicManager
from logic.stress_scorer import StressScorer
from fake_provider import FakeProvider
from plain_tests import function_suite
from reports.cohort_report import CohortReport

PROJECT = Path(__file__).resolve().parents[1]
ANSWERS = dict(student_id='2605581', survey_version='evidence-v3', safety_flag=False,
               pss_1=3, pss_2=3, pss_3=3, pss_4=3,
               sleep_hours_avg=6.5, sleep_quality=3, pas_workload=3,
               pas_catchup=3, fin_stress=3, mspss_friends=3, mspss_family=3,
               feelings_text='PRIVATE, do not transmit or save')


def fake_ai_reply() -> str:
    scorer = StressScorer()
    return json.dumps({
        'scored_items': scorer.scored_items(ANSWERS),
        'stress_score': scorer.score(ANSWERS).stress_score,
        'risk_category': 'Moderate',
        'primary_stressors': ['academic_overload'],
        'confidence': 0.8,
        'reasoning': 'Schoolwork can be demanding. Make room for short breaks.',
        'soft_label': 'Worth a check-in',
        'tips': ['short_breaks'],
        'speak_prominence': 'medium',
    })


def test_all_four_managers_implement_abstract_parent():
    storage = IsolatedMemoryStorage()
    services = (IOManager(), AIManager(FakeProvider('{}')),
                LogicManager(), DataManager(backend=storage))
    assert all(isinstance(service, BaseManager) for service in services)
    assert [service.name for service in services] == [
        'io_manager', 'ai_manager', 'logic_manager', 'data_manager']
    for service in services:
        summary = service.health()
        assert summary['name'] == service.name
        assert 'PRIVATE' not in str(summary)


def test_survey_policy_is_a_real_object_shared_with_io_manager():
    definition = SurveyDefinition(version=survey.VERSION, questions=survey.QUESTIONS,
                                  sections=survey.SECTIONS,
                                  safety_patterns=survey.SAFETY_PATTERNS)
    io = IOManager(definition=definition)
    assert io.survey is definition
    assert definition.public_config() == survey.DEFAULT_SURVEY.public_config()
    assert definition.safety_check('I want to die') is True
    assert definition.safety_check('Learning is tiring') is False
    assert definition.validate_question('3', survey.QUESTION_MAP['pss_1']) == (
        survey.DEFAULT_SURVEY.validate_question('3', survey.QUESTION_MAP['pss_1']))


def test_io_manager_is_real_instance_with_configurable_cli_io():
    lines = []
    answers = iter(['2605581'])
    io = IOManager(input_fn=lambda prompt: next(answers), output_fn=lines.append)
    assert io.get_student_id() == '2605581'
    io.display_record({'student_id': '2605581'})
    assert any('2605581' in line for line in lines)
    assert io.validate_student_form(ANSWERS)[0]
    assert io.format_ai_error('timeout')['error_code'] == 'timeout'


def test_ai_logic_data_pipeline_uses_mocked_provider_and_storage_contract():
    store = IsolatedMemoryStorage()
    service = CheckinService(IOManager(), LogicManager(), DataManager(backend=store))
    provider = FakeProvider(fake_ai_reply(), models=('test-provider',))
    ai = AIManager(provider, retry_delay_sec=0, max_attempts=1)
    record = dict(ANSWERS)
    ok, evaluated = service.process_checkin(record, ai=ai)
    assert ok and evaluated['ai_ok'] is True
    assert evaluated['source'] == 'ai_logic'
    assert len(provider.calls) == 1
    assert 'PRIVATE' not in provider.calls[0][0]
    assert '2605581' not in provider.calls[0][0]
    assert 'PRIVATE' not in json.dumps(service.result_view(evaluated))
    data = service.data
    saved = data.save_record(evaluated, opt_in=True)
    assert saved['ok'] and len(store.load()) == 1
    assert 'feelings_text' not in store.load()[0]
    assert len(data.query(lambda row: row['risk_category']=='Moderate')) == 1
    assert service.summarize_records(store.load())['evaluated'] == 1


def test_data_manager_implements_storage_backend_lsp_and_guardrails():
    backend = IsolatedMemoryStorage()
    manager = DataManager(backend=backend)
    assert manager.save_pending_record({'student_id': '2605581'})['ok'] is False
    pending = manager.save_pending_record({'student_id': '2605581',
                                           'feelings_text': 'PRIVATE'}, opt_in=True)
    assert pending['ok'] is True
    assert 'feelings_text' not in backend.load()[0]
    assert backend.load()[0]['status'] == 'pending'
    assert manager.load_all_records()['ok']
    assert manager.replace_pending_record(pending['record']['record_id'],
                                          {'ai_ok': False}, opt_in=True)['ok'] is False
    assert backend.load()[0]['status'] == 'pending'


def test_invalid_json_schema_is_never_overwritten_or_lost():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'records.json'
        original = '{"this_is_not_a_checkin": "preserve me"}'
        path.write_text(original, encoding='utf-8')
        manager = DataManager(path)
        result = manager.save_pending_record({'student_id': '2605581'}, opt_in=True)
        assert not result['ok'] and 'Unsupported data file schema' in result['error']
        assert path.read_text(encoding='utf-8') == original
        backups = list(path.parent.glob('records.corrupt-*.json'))
        assert len(backups) == 1 and backups[0].read_text(encoding='utf-8') == original


def test_new_cohort_feature_excludes_pending_and_rejects_untrusted_scores():
    records = [
        dict(student_id='2600001', risk_category='Low', stress_score=2,
             ai_ok=True),
        dict(student_id='2600002', risk_category='High', stress_score=4,
             ai_ok=True),
        dict(student_id='2600003', status='pending', ai_ok=False, stress_score=5),
        dict(student_id='2700001', risk_category='High', stress_score='bad',
             ai_ok=True),
    ]
    report = CohortReport().build(records)
    assert report['total']==4 and report['pending']==1 and report['evaluated']==3
    assert report['average_stress']==3 and report['tiers']=={'Low':1, 'Moderate':0, 'High':2}
    assert report['cohorts'][1]['cohort']=='26'
    assert report['cohorts'][1]['average_stress']==3
    assert 'student_id' not in json.dumps(report)
    assert 'reasoning' not in json.dumps(report)


def test_partial_list_cannot_silently_discard_rows_on_append():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'records.json'
        original = '[{"record_id":"keep", "student_id":"2601111"}, null]'
        path.write_text(original, encoding='utf-8')
        manager = DataManager(path)
        result = manager.save_pending_record({'student_id':'2602222'},opt_in=True)
        assert not result['ok'] and 'Invalid record entries' in result['error']
        assert path.read_text(encoding='utf-8') == original
        assert len(list(path.parent.glob('records.corrupt-*.json'))) == 1


def test_cohort_summary_tolerates_unexpected_record_values():
    rows = [{'student_id':'2601111', 'ai_ok':True, 'risk_category':['Low'],
             'stress_score':2.5}, {'student_id':'', 'ai_ok':True,
             'risk_category':'Low', 'stress_score':float('nan')}]
    result = CohortReport().build(rows)
    assert result['total'] == 2 and result['evaluated'] == 2
    assert result['average_stress'] == 2.5
    assert result['tiers'] == {'Low': 1, 'Moderate': 0, 'High': 0}


def test_one_production_class_per_python_file_and_runtime_routes_use_objects():
    # Installed packages (a local .venv) are not project source.
    skip = {'.venv', 'venv', 'site-packages', '__pycache__', '.git'}
    for path in PROJECT.rglob('*.py'):
        if path.name == '__init__.py' or skip.intersection(path.parts) or 'tests' in path.parts:
            continue
        classes=[node for node in ast.parse(path.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.ClassDef)]
        assert len(classes) <= 1, (path, [node.name for node in classes])
    app_code = (PROJECT/'main.py').read_text(encoding='utf-8')
    controller_code = (PROJECT/'web_controller.py').read_text(encoding='utf-8')
    assert 'WebController(app, service)' in app_code
    assert "self._data.save_record(outcome, opt_in=True)" in controller_code
    assert 'self._io.validate_student_form(values)' in controller_code
    assert 'self._service.process_checkin(record, ai=self._ai_for_request())' in controller_code


def load_tests(loader, tests, pattern):
    return function_suite(globals())

if __name__ == '__main__':
    unittest.main()
