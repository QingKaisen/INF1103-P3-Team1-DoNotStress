"""HTTP tests for Phase 2's new cohort overview; requires Flask installation."""
import json
import tempfile
import unittest
from pathlib import Path

from main import create_app
from plain_tests import function_suite
from test_admin_access import login_admin
import survey


def test_summary_rejects_students_and_returns_only_aggregates_to_admins():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/'records.json'
        app = create_app({'TESTING': True, 'SECRET_KEY': 'local-tests',
                          'DATA_PATH': str(path)})
        client = app.test_client()
        assert client.get('/api/summary').status_code == 401
        path.write_text(json.dumps([
            {'student_id':'2600001', 'survey_version':survey.VERSION,
             'ai_ok':True,'source':'ai_logic','stress_score':2.0,
             'risk_category':'Low','feelings_text':'PRIVATE'},
            {'student_id':'2600002', 'survey_version':survey.VERSION,
             'ai_ok':True,'source':'ai_logic','stress_score':4.0,
             'risk_category':'High'},
            {'student_id':'2600003', 'survey_version':survey.VERSION,
             'status':'pending','ai_ok':False},
            {'student_id':'2900004', 'survey_version':'legacy',
             'ai_ok':True,'stress_score':5,'risk_category':'High'},
        ]),encoding='utf-8')
        login_admin(client)
        response=client.get('/api/summary')
        assert response.status_code==200
        data=response.json
        assert data['total']==3 and data['evaluated']==2 and data['pending']==1
        assert data['average_stress']==3
        assert data['tiers']=={'Low':1,'Moderate':0,'High':1}
        assert data['cohorts'][0]['cohort']=='26'
        assert '2600001' not in response.get_data(as_text=True)
        assert 'PRIVATE' not in response.get_data(as_text=True)
        page=client.get('/records')
        assert page.status_code==200
        assert '"summaryUrl": "/api/summary"' in page.get_data(as_text=True)


def test_corrupt_records_report_summary_as_error_not_zero_records():
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'records.json'
        app=create_app({'TESTING': True, 'SECRET_KEY':'local-tests',
                        'DATA_PATH':str(path)})
        client=app.test_client()
        login_admin(client)
        path.write_text('{invalid',encoding='utf-8')
        assert client.get('/api/summary').status_code==500


def load_tests(loader, tests, pattern):
    return function_suite(globals())

if __name__=='__main__':
    unittest.main()
