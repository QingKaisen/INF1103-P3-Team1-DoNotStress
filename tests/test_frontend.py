"""Test the real I/O → AI schema → Logic → save pipeline without network calls."""
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from main import create_app
from plain_tests import case, function_suite


def fake_generate(prompt, api_key, model_name):
    # Replay the formula on the answers in the prompt so the stub matches
    # whatever the form submitted. No network call.
    import logic_manager
    if isinstance(prompt, str) and '{' in prompt:
        answers = json.loads(prompt[prompt.rfind('{'):])
    else:
        answers = {key: 3 for key in logic_manager.ANSWER_KEYS}
        answers['sleep_hours_avg'] = 6.5
    scored = logic_manager.scored_items(answers)
    stress = logic_manager.score_answers(answers)['stress_score']
    category = logic_manager.category_for_score(stress)
    label = logic_manager._CATEGORY_TO_LABEL[category]
    prominence = logic_manager._CATEGORY_TO_PROMINENCE[category]
    return json.dumps({
        'scored_items': scored, 'stress_score': stress, 'risk_category': category,
        'primary_stressors': ['academic_overload'],
        'confidence': 0.8,
        'reasoning': 'Test fixture, not a real assessment.',
        'soft_label': label, 'tips': ['workload_chunks', 'short_breaks'],
        'speak_prominence': prominence,
    })


# --- Campus ----------------------------------------------------------------

@contextmanager
def campus_app():
    """A fresh app with the fake Gemini and its own temp records file (replaces setUp)."""
    ctx = SimpleNamespace()
    with ExitStack() as stack:
        ctx.temp = tempfile.TemporaryDirectory()
        stack.callback(ctx.temp.cleanup)
        ctx.path = Path(ctx.temp.name) / 'records.json'
        ctx.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only',
                               'AI_GENERATE_FN': fake_generate, 'AI_MAX_ATTEMPTS': 1,
                               'AI_RETRY_DELAY_SEC': 0, 'DATA_PATH': str(ctx.path)})
        ctx.client = ctx.app.test_client()
        ctx.client.get('/')
        with ctx.client.session_transaction() as session:
            ctx.token = session['csrf_token']
        ctx.values = dict(student_id='2605581', pss_1=3, pss_2=3, pss_3=3, pss_4=3,
                           sleep_hours_avg=6.5, sleep_quality=3, pas_workload=3, pas_catchup=3,
                           fin_stress=3, mspss_friends=3, mspss_family=3, feelings_text='Example concern')
        yield ctx


def post_checkin(ctx, values=None, url='/'):
    """Submit a check-in as JSON with the session's CSRF token."""
    return ctx.client.post(url, json=ctx.values if values is None else values,
                            headers={'X-CSRF-Token': ctx.token})


def test_student_result_and_records_routes():
    with campus_app() as ctx:
        denied = ctx.client.get('/records')
        assert denied.status_code == 302
        assert denied.location.endswith('/admin/login')
        assert ctx.client.get('/result').status_code == 302
        assert post_checkin(ctx).status_code == 200
        result = ctx.client.get('/result')
        assert result.status_code == 200
        body = result.get_data(as_text=True)
        assert '"page": "result"' in body
        assert '"page": "records"' not in body
        assert '"resultUrl": "/result"' in body
        assert '"recordsPageUrl": "/records"' in body
        still_denied = ctx.client.get('/records')
        assert still_denied.status_code == 302
        assert still_denied.location.endswith('/admin/login')
        from test_admin_access import login_admin
        login_admin(ctx.client)
        records = ctx.client.get('/records')
        assert records.status_code == 200
        records_body = records.get_data(as_text=True)
        assert '"page": "records"' in records_body
        assert '"page": "result"' not in records_body
        js = Path(__file__).resolve().parents[1].joinpath('static', 'campus.js').read_text(encoding='utf-8')
        assert 'boot.resultUrl' in js
        assert 'boot.recordsPageUrl' in js
        assert 'history.pushState' in js
        assert "page='result'" in js


def test_retired_page_shows_home():
    with campus_app() as ctx:
        response = ctx.client.get('/garden')
        assert response.status_code == 302
        assert response.location.endswith('/')
        page = ctx.client.get('/garden', follow_redirects=True)
        text = page.get_data(as_text=True)
        assert '>Home<' in text
        assert 'Student view' in text
        assert 'My garden' not in text
        assert 'data-nav="garden"' not in text
        login = ctx.client.get('/admin/login').get_data(as_text=True)
        assert 'My garden' not in login


def test_success_uses_backend_result_and_keeps_answers_out_of_cookie():
    with campus_app() as ctx:
        response = post_checkin(ctx)
        assert response.status_code == 200
        assert response.json['result']['soft']['heading'] == 'Worth a check-in'
        with ctx.client.session_transaction() as session:
            assert set(session) == {'csrf_token', 'checkin_token'}
        record = next(iter(ctx.app.extensions['pending_checkins'].values()))['record']
        assert record['source'] == 'ai_logic'
        stored = json.loads(ctx.path.read_text(encoding='utf-8'))
        assert len(stored) == 1
        assert 'feelings_text' not in stored[0]
        assert ctx.client.get('/result').status_code == 200
        assert len(json.loads(ctx.path.read_text(encoding='utf-8'))) == 1


def test_invalid_fields_prevent_ai_call():
    with campus_app() as ctx:
        for field, value in [('student_id','1200000'), ('sleep_hours_avg','6.75'),
                             ('pss_1','6'), ('pas_workload','0'),
                             ('fin_stress','yes'), ('mspss_friends','1.5')]:
            with case(field=field), patch('ai_manager.AIManager.analyse_student') as ai:
                response = post_checkin(ctx, {**ctx.values, field:value})
                assert response.status_code == 400
                assert field in response.json['errors']
                ai.assert_not_called()


def test_optional_text():
    with campus_app() as ctx:
        assert post_checkin(ctx, {**ctx.values, 'feelings_text':''}).status_code == 200


def test_missing_key_returns_error_and_no_logic_fallback():
    with campus_app() as ctx:
        ctx.app.config['AI_GENERATE_FN'] = None
        with patch.dict(os.environ, {'GEMINI_API_KEY':''}), patch('logic_manager.LogicManager.apply_logic') as logic:
            response = post_checkin(ctx)
        assert response.status_code == 503
        assert response.json['error_code'] == 'missing_api_key'
        assert 'result' not in response.json
        assert 'advisor' in response.json
        logic.assert_not_called()


def test_invalid_ai_response_never_becomes_result():
    with campus_app() as ctx:
        ctx.app.config['AI_GENERATE_FN'] = lambda *args: '{}'
        assert post_checkin(ctx).status_code == 503
        assert ctx.client.get('/result').status_code == 302


def test_failure_clears_previous_result():
    with campus_app() as ctx:
        post_checkin(ctx)
        ctx.app.config['AI_GENERATE_FN'] = lambda *args: '{}'
        post_checkin(ctx)
        assert not ctx.app.extensions['pending_checkins']
        assert ctx.client.get('/result').status_code == 302


def test_save_is_automatic_and_refresh_does_not_duplicate():
    with campus_app() as ctx:
        post_checkin(ctx)
        assert len(json.loads(ctx.path.read_text(encoding='utf-8'))) == 1
        assert ctx.client.get('/result').status_code == 200
        assert post_checkin(ctx, {'opt_in':True}, '/save').status_code == 200
        assert post_checkin(ctx, {}, '/save').status_code == 200
        records = json.loads(ctx.path.read_text(encoding='utf-8'))
        assert len(records) == 1


def test_submit_saves_record_for_admin_without_reflection():
    with campus_app() as ctx:
        ctx.values['feelings_text'] = 'PRIVATE REFLECTION that must not be stored'
        response = post_checkin(ctx)
        assert response.status_code == 200
        stored = json.loads(ctx.path.read_text(encoding='utf-8'))
        assert len(stored) == 1
        assert stored[0]['student_id'] == '2605581'
        assert stored[0]['pss_1'] == 3
        assert stored[0]['sleep_hours_avg'] == 6.5
        assert 'feelings_text' not in stored[0]
        assert 'PRIVATE REFLECTION' not in ctx.path.read_text(encoding='utf-8')
        assert ctx.client.get('/result').status_code == 200
        assert len(json.loads(ctx.path.read_text(encoding='utf-8'))) == 1
        assert ctx.client.get('/api/records').status_code == 401
        from test_admin_access import login_admin
        login_admin(ctx.client)
        records_page = ctx.client.get('/records')
        assert records_page.status_code == 200
        assert '"page": "records"' in records_page.get_data(as_text=True)
        payload = ctx.client.get('/api/records').json
        assert payload['matching'] == 1
        row = payload['records'][0]
        assert row['student_id'] == '2605581'
        assert row['status'] == 'Evaluated'
        assert 'feelings_text' not in row
        assert 'reflection' not in row
        assert 'PRIVATE REFLECTION' not in json.dumps(payload)


def test_ai_down_saves_pending_record_without_reflection():
    with campus_app() as ctx:
        ctx.values['feelings_text'] = 'PRIVATE REFLECTION that must not be stored'
        ctx.app.config['AI_GENERATE_FN'] = lambda *args: '{}'
        response = post_checkin(ctx)
        assert response.status_code == 503
        assert 'result' not in response.json
        assert not ctx.app.extensions['pending_checkins']
        stored = json.loads(ctx.path.read_text(encoding='utf-8'))
        assert len(stored) == 1
        assert stored[0]['status'] == 'pending'
        assert stored[0]['student_id'] == '2605581'
        assert stored[0]['sleep_hours_avg'] == 6.5
        assert stored[0]['ai_ok'] is False
        assert 'feelings_text' not in stored[0]
        assert 'PRIVATE REFLECTION' not in ctx.path.read_text(encoding='utf-8')
        from test_admin_access import login_admin
        login_admin(ctx.client)
        row = ctx.client.get('/api/records').json['records'][0]
        assert row['student_id'] == '2605581'
        assert row['status'] == 'Pending'
        assert 'feelings_text' not in row
        assert 'PRIVATE REFLECTION' not in json.dumps(row)


def test_csrf_and_malformed_requests():
    with campus_app() as ctx:
        assert ctx.client.post('/', json=ctx.values).status_code == 403
        assert post_checkin(ctx, []).status_code == 400
        assert ctx.client.post('/save', json={'opt_in':True}).status_code == 403


def test_new_and_expired_result():
    with campus_app() as ctx:
        post_checkin(ctx)
        ctx.client.get('/new')
        assert not ctx.app.extensions['pending_checkins']
        assert ctx.client.get('/result').status_code == 302
        post_checkin(ctx)
        ctx.app.config['PENDING_TTL_SECONDS'] = -1
        assert ctx.client.get('/result').status_code == 302
        assert post_checkin(ctx, {'opt_in':True}, '/save').status_code == 409


def test_save_failure_is_retryable():
    with campus_app() as ctx:
        with patch.object(ctx.app.extensions['data_manager'], 'save_record', return_value={'ok':False}):
            assert post_checkin(ctx).status_code == 500
        assert not ctx.path.exists()
        assert post_checkin(ctx).status_code == 200
        assert len(json.loads(ctx.path.read_text(encoding='utf-8'))) == 1


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())


if __name__ == '__main__':
    unittest.main()
