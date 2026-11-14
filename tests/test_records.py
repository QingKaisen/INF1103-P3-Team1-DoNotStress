"""Saved history and student-ID contract tests using synthetic records only."""
import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import io_manager
import survey
from checkin_service import CheckinService
from data_manager import DataManager
from io_manager import IOManager as _IOManager
from logic_manager import LogicManager as _LogicManager
from main import create_app
from test_admin_access import login_admin
from plain_tests import capture_logs, function_suite

_IO = _IOManager()
_LOGIC = _LogicManager()


def result_view(record, saved=False):
    return CheckinService(_IO, _LOGIC, DataManager()).result_view(record, saved=saved)


# --- Records ---------------------------------------------------------------

@contextmanager
def records_app():
    """A fresh app, a signed-in admin client and sample rows (replaces setUp)."""
    ctx = SimpleNamespace()
    with ExitStack() as stack:
        ctx.temp = tempfile.TemporaryDirectory()
        stack.callback(ctx.temp.cleanup)
        ctx.path = Path(ctx.temp.name) / 'records.json'
        ctx.app = create_app({'TESTING': True, 'SECRET_KEY':'tests', 'DATA_PATH':str(ctx.path)})
        ctx.client = ctx.app.test_client()
        login_admin(ctx.client)
        labels={'Low':"You're doing ok",'Moderate':'Worth a check-in','High':'Please reach out'}
        ctx.rows = [dict(student_id=sid, survey_version=survey.VERSION, stress_score=score,
                          stress_level=5, pss_1=2, pss_2=4, pss_3=3, pss_4=1,
                          sleep_hours_avg=7.5, sleep_quality=2, pas_workload=3,
                          pas_catchup=2, fin_stress=4, mspss_friends=4, mspss_family=5,
                          risk_category=risk, soft_label=labels[risk], ai_ok=True, source='ai_logic',
                          saved_at=date, feelings_text='Private response that must not appear in history',
                          reasoning='Private explanation')
                     for sid, score, risk, date in [
                         ('2603503',3.1,'Moderate','2026-10-09T01:00:00+00:00'),
                         ('2703504',4.2,'High','2026-10-09T02:00:00+00:00'),
                         ('2000123',1.4,'Low','2026-10-09T03:00:00+00:00')]]
        yield ctx


def write_rows(ctx, rows=None):
    """Write rows (default: the sample rows) straight to the records file."""
    ctx.path.write_text(json.dumps(ctx.rows if rows is None else rows), encoding='utf-8')


def test_seven_digits_starting_with_two():
    with records_app() as ctx:
        for sid in ('2000000','2200000','2703504','2999999',' 2603503 '):
            assert _IO.validate_student_id(sid)[0], sid
        for sid in ('1200000','3000000','260350','26035030','2abc503','2１２３４５６',''):
            assert not _IO.validate_student_id(sid)[0], sid


def test_corrupt_file_is_backed_up_before_save():
        with records_app() as ctx:
            import data_manager
            ctx.path.write_text('[{"student_id": "2603503"', encoding='utf-8')
            with capture_logs('data_manager', level='WARNING'):
                result = DataManager(str(ctx.path)).save_pending_record({'student_id': '2000123'}, opt_in=True)
            assert result['ok']
            backups = list(Path(ctx.temp.name).glob('records.corrupt-*.json'))
            assert len(backups) == 1
            assert backups[0].read_text(encoding='utf-8') == '[{"student_id": "2603503"'


def test_missing_file_is_empty():
    with records_app() as ctx:
        response=ctx.client.get('/api/records')
        assert response.status_code == 200
        assert response.json['records'] == []


def test_all_records_columns_and_privacy():
    with records_app() as ctx:
        write_rows(ctx)
        response=ctx.client.get('/api/records')
        assert response.status_code == 200
        assert response.json['total'] == 3
        assert response.json['records'][0]['student_id'] == '2000123'
        assert set(response.json['records'][0]) == ({'student_id','stress_score','risk_category','soft_label',
                         'pss_1','pss_2','pss_3','pss_4',
                         'sleep_hours_avg','sleep_quality','pas_workload','pas_catchup','fin_stress','mspss_friends',
                         'mspss_family','saved_at','survey_version','status'})
        assert response.json['records'][0]['pss_1'] == 2
        assert response.json['records'][0]['sleep_hours_avg'] == 7.5
        assert 'stress_level' not in response.json['records'][0]
        assert 'ai_status' not in response.json['records'][0]
        assert 'Private' not in response.get_data(as_text=True)
        assert response.headers['Cache-Control'] == 'no-store'


def test_partial_id_and_combined_filters():
    with records_app() as ctx:
        write_rows(ctx)
        assert ctx.client.get('/api/records?student_id=035').json['matching'] == 2
        result=ctx.client.get('/api/records?student_id=035&tier=Please reach out&cohort_year=2027').json
        assert result['matching'] == 1
        assert result['records'][0]['student_id'] == '2703504'
        assert result['cohorts'] == ['27','26','20']
        assert ctx.client.get("/api/records?tier=You're doing ok&cohort_year=26").json['records'] == []
        assert ctx.client.get('/api/records').json['matching'] == 3


def test_tier_filters():
    with records_app() as ctx:
        write_rows(ctx)
        for risk, tier in (('Low',"You're doing ok"),('Moderate','Worth a check-in'),('High','Please reach out')):
            rows=ctx.client.get('/api/records',query_string={'tier':tier}).json['records']
            assert len(rows) == 1
            assert rows[0]['risk_category'] == risk
            assert rows[0]['soft_label'] == tier
            assert rows[0]['status'] == 'Evaluated'


def test_errors_are_not_empty_history():
    with records_app() as ctx:
        ctx.path.write_text('invalid json',encoding='utf-8')
        assert ctx.client.get('/api/records').status_code == 500
        write_rows(ctx)
        assert ctx.client.get('/api/records?tier=Critical').status_code == 400
        assert ctx.client.get('/api/records?cohort_year=abc').status_code == 400


def test_legacy_records_are_ignored():
    with records_app() as ctx:
        write_rows(ctx, [
            {'student_id':'2600000','survey_version':'legacy','risk_category':'High'},
            {'student_id':'2700000','survey_version':'evidence-v2','ai_ok':False,'risk_category':'Low'},
            {'student_id':'2800000','survey_version':survey.VERSION,'status':'pending','risk_category':'Low'},
            {'student_id':'2900000','survey_version':survey.VERSION,'ai_ok':True,'source':'ai_logic','risk_category':'Moderate'},
            'not-a-record',
        ])
        payload=ctx.client.get('/api/records').json
        assert payload['total'] == 2
        assert payload['cohorts'] == ['29','28']
        statuses={row['student_id']:row['status'] for row in payload['records']}
        assert statuses == {'2800000':'Pending','2900000':'Evaluated'}
        pending=next(row for row in payload['records'] if row['student_id']=='2800000')
        assert pending['soft_label'] == "You're doing ok"


def test_insights_use_assessment_and_inputs():
    with records_app() as ctx:
        view=result_view({**ctx.rows[0], 'soft_label':'Worth a check-in',
                          'tips':['short_breaks'], 'speak_prominence':'medium',
                          'primary_stressors':['academic_overload'],
                          'reasoning':'Workload contributes. Logic adjusted: internal_rule.'})
        assert view['insights']['risk_category'] == 'Moderate'
        assert view['insights']['explanation'] == 'Workload contributes.'
        assert view['insights']['snapshot']['stress_level'] == 5


def test_records_page():
    with records_app() as ctx:
        response=ctx.client.get('/records')
        assert response.status_code == 200
        assert '"page": "records"' in response.get_data(as_text=True)
        js=(Path(__file__).resolve().parents[1]/'static'/'campus.js').read_text(encoding='utf-8')
        css=(Path(__file__).resolve().parents[1]/'static'/'style.css').read_text(encoding='utf-8')
        for header in ('Control','Coping','Going well','Piling up','Sleep (hours)','Catch-up','Date saved'):
            assert header in js
        for gone in ('Control (pss_1)','Coping (pss_2)','Going well (pss_3)','Piling up (pss_4)',
                     'Answers: 1 = lowest, 5 = highest · Sleep in hours',
                     'Cohort year uses the first two ID digits'):
            assert gone not in js
        assert "title=\"${escape(key+': '+questions[key].prompt)}\"" in js
        assert 'Showing ${data.matching} of ${data.total} check-ins' in js
        assert 'Answers are on a 1–5 scale, where 1 is the lowest. Sleep is shown in hours. All times are in Singapore time (SGT).' in js
        assert 'role="status"' in js
        assert 'title="${escape(title)}"' in js
        assert 'overflow-x:auto' in css
        assert 'max-width:75rem' in css
        assert 'text-align:center' in css
        assert 'white-space:nowrap' in css


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())


if __name__=='__main__':
    unittest.main()
