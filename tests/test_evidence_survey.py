"""Boundary, direction, privacy and integration tests for the revised survey."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_manager
import io_manager
import logic_manager
import survey
import test_frontend as frontend
from test_admin_access import login_admin
from plain_tests import function_suite

fake_generate = frontend.fake_generate


def raw(**changes):
    return dict(student_id='2605581', pss_1=1, pss_2=5, pss_3=5, pss_4=1,
                sleep_hours_avg=8, sleep_quality=1, pas_workload=1, pas_catchup=1, fin_stress=1,
                mspss_friends=5, mspss_family=5, **changes)

MAX_STRESS = dict(pss_1=5, pss_2=1, pss_3=1, pss_4=5, sleep_hours_avg=4, sleep_quality=5,
                  pas_workload=5, pas_catchup=5, fin_stress=5, mspss_friends=1, mspss_family=1)


def record(**changes):
    values=raw()
    values.update(changes)
    ok, result=io_manager.validate_student_form(values)
    assert ok, result
    return result


def with_score(**changes):
    """io record plus logic_manager's deterministic score of its answers."""
    r=record(**changes)
    return {**r, **logic_manager.score_answers(r)}


def finalised(**changes):
    """io record run through a matching fake Gemini reply, then Logic."""
    r=record(**changes)
    fields=ai_manager.validate_ai_response(fake_generate(json.dumps(r),None,None))[1]
    return logic_manager.apply_logic({**r,**fields})


# --- Scoring ---------------------------------------------------------------

def test_reverse_coding_extremes():
    assert with_score()['stress_score'] == 1.0
    assert with_score(**MAX_STRESS)['stress_score'] == 5.0
    assert with_score(pss_2=1)['stress_score'] == round(15/11,2)  # reversed: 6-1 = 5
    assert with_score(pss_1=5)['stress_score'] == round(15/11,2)  # not reversed
    assert survey.REVERSED_KEYS == ('pss_2','pss_3','mspss_friends','mspss_family')


def test_average_and_band_boundaries():
    # 11 integer scores cannot land on 2.5 or 3.5 exactly. 27/11 is just below 2.5; 28/11 just above.
    low=dict(pss_1=5,pss_4=5,sleep_hours_avg=4.5,sleep_quality=5)  # sum 27
    moderate=dict(low,pas_workload=2)  # sum 28
    assert (with_score(**low)['stress_score'],with_score(**low)['risk_category']) == (round(27/11,2),'Low')
    crossed=with_score(**moderate)
    assert (crossed['stress_score'],crossed['risk_category']) == (round(28/11,2),'Moderate')
    assert crossed['soft_label'] == 'Worth a check-in'
    high=dict(pss_1=5,pss_4=5,sleep_hours_avg=4.5,sleep_quality=5,pas_workload=5,pas_catchup=5)
    for fin, total, band in [(4,38,'Moderate'),(5,39,'High')]:
        r=with_score(**high,fin_stress=fin)
        assert (r['stress_score'],r['risk_category']) == (round(total/11,2),band)
        assert r['soft_label'] == {'Moderate':'Worth a check-in','High':'Please reach out'}[band]
    assert with_score()['soft_label'] == "You're doing ok"


def test_every_single_answer_on_every_question():
    for q in survey.QUESTIONS:
        if q['key']=='sleep_hours_avg':
            continue
        for value in range(1,6):
            r=with_score(**{q['key']:value})
            scored=6-value if q.get('reverse') else value
            assert r['stress_score'] == round((10+scored)/11,2), q['key']
            assert r['scored_item_count'] == 11


def test_sleep_hours_stay_hours_and_score_as_stress():
    bands=[(14,1),(8.5,1),(8,1),(7.5,2),(7,2),(6.5,3),(6,3),(5.5,4),(5,4),(4.5,5),(0,5)]
    for hours, scored in bands:
        r=with_score(sleep_hours_avg=hours)
        assert r['sleep_hours_avg'] == hours
        assert r['stress_score'] == round((10+scored)/11,2), hours
    assert not io_manager.validate_student_form({**raw(),'sleep_hours_avg':6.75})[0]
    q=survey.QUESTION_MAP['sleep_hours_avg']
    assert (q['kind'],q['min'],q['max'],q['step']) == ('slider',0,14,0.5)
    fin=survey.QUESTION_MAP['fin_stress']
    assert (fin['kind'],fin['min'],fin['max'],fin['step'],fin['low'],fin['high']) == ('slider',1,5,1,'No stress at all','Overwhelming stress')
    assert q['prompt'] == 'On average, how many hours of sleep have you gotten each night this week?'
    js=(Path(__file__).resolve().parents[1]/'static'/'campus.js').read_text(encoding='utf-8')
    assert 'sleep_exact' not in js
    assert 'Or enter hours directly' not in js
    assert 'Use half-hour steps' not in js
    assert 'Skip / clear this answer' not in js
    assert 'ds-optional' not in js
    assert 'ds-q-title' in js
    assert 'data-confirm' not in js
    assert 'Answer selected' not in js
    assert 'Move the slider or confirm the displayed position.' not in js
    assert 'Higher numbers mean more financial stress.' in js
    assert "Only SIT wellbeing staff can see your ID, and it's never sent to the AI." in js
    assert 'Your student ID stays private.' not in js
    assert "This space is just for you." in js
    assert "This check-in isn't a diagnosis" in js
    assert '>Your check-in<' in js
    assert 'stressBadge' not in js
    assert 'not sent to the AI provider' not in js
    assert 'Not sent to Gemini' not in js
    assert 'project guidance' not in js
    assert 'project heuristics' not in js
    assert 'documented project rules' not in js
    assert 'not a validated combined screening instrument' not in js
    assert 'Legacy /10' not in js
    assert 'Risk category' not in js
    assert 'AI Status' not in js
    assert 'Stress score (/5)' in js
    assert "You're doing ok" in js
    assert 'SIT Counselling 24-hour helpline' in js
    assert 'href="tel:65922030"' in js
    assert 'href="tel:1767"' in js
    assert 'href="tel:1771"' in js


def test_prompts_and_pss_sentences():
    prompts={
        'pss_1':"In the past month, how often have you felt you couldn't control the important things in your life?",
        'pss_2':'In the past month, how often have you felt confident handling your personal problems?',
        'pss_3':'In the past month, how often have you felt things were going well for you?',
        'pss_4':'In the past month, how often have you felt problems were piling up too much to handle?',
    }
    assert survey.QUESTION_MAP['pss_1']['label'] == 'Losing control'
    assert survey.SECTIONS[0]['heading'] == 'Start with the bigger picture.'
    assert all('source' not in section for section in survey.SECTIONS)
    assert all('card' not in item for item in survey.QUESTIONS)
    page=(Path(__file__).resolve().parents[1]/'static'/'campus.js').read_text(encoding='utf-8')
    assert 'Grounded in validated instruments' in page
    assert 'class="ds-source"' not in page
    assert 'ds-sources' in page
    assert 'cardText' not in page
    assert 'team wording' not in page
    for key, prompt in prompts.items():
        assert survey.QUESTION_MAP[key]['prompt'] == prompt
    assert survey.QUESTION_MAP['pas_workload']['prompt'] == 'I feel my coursework is too much to handle.'
    assert survey.QUESTION_MAP['pas_catchup']['prompt'] == 'When I fall behind on my work, I find it hard to catch up.'
    assert survey.QUESTION_MAP['mspss_family']['prompt'] == 'I get the emotional help and support I need from my family.'
    for item in survey.QUESTIONS:
        if item['key'].startswith('pss_') or item['key'] in ('sleep_hours_avg','sleep_quality','fin_stress'):
            assert item['prompt'].endswith('?'), item['key']
        else:
            assert item['prompt'].endswith('.'), item['key']


def test_one_based_labels():
    r=with_score(sleep_hours_avg=7.5,sleep_quality=5,fin_stress=1)
    shown_factors=logic_manager.factor_insights(r,r)
    factors={item['title']:item['value'] for item in shown_factors}
    assert factors['Rest & recovery'] == '7.5 hours · Very bad quality'
    assert factors['Money pressures'] == '1/5 · No stress at all'
    texts={item['title']:item['text'] for item in shown_factors}
    assert texts['Rest & recovery'] == 'Sleep and stress affect each other. When sleep is short or restless, everyday tasks can feel harder, and stress can make it harder to sleep well.'
    assert texts['Study demands'] == 'A heavy workload can leave less time to rest and recharge. Feeling behind or overloaded is one of the most common sources of student stress.'
    assert texts['Money pressures'] == 'Financial worries can add to the pressure of studying. Concerns about money often weigh on focus and sleep.'
    assert texts['Support & connection'] == 'Having people to turn to can make stressful times easier to handle. Strong support from friends or family helps protect against stress.'
    flagged={item['title']:item['flagged'] for item in shown_factors}
    assert flagged['Rest & recovery']
    assert not flagged['Money pressures']
    shown={field['key']:field['value'] for field in io_manager.format_student_record(r)['fields']}
    assert shown['sleep_hours_avg'] == '7.5'
    assert shown['sleep_quality'] == '5 · Very bad'
    assert shown['fin_stress'] == '1 · No stress at all'
    assert shown['pss_1'] == '1 · Never'


def test_sleep_boundary_and_quality():
    assert not with_score(sleep_hours_avg=6,sleep_quality=3)['context_flags']['sleep']
    assert with_score(sleep_hours_avg=5.5)['context_flags']['sleep']
    assert with_score(sleep_quality=4)['context_flags']['sleep']


def test_finance_direction_and_flags_only_steer_tips():
    assert not with_score(fin_stress=3)['context_flags']['finances']
    assert with_score(fin_stress=4)['context_flags']['finances']
    r=with_score(fin_stress=5,pas_workload=5)  # 19/11: flags no longer lift the band
    assert r['risk_category'] == 'Low'
    assert r['context_flags']['finances'] and r['context_flags']['workload']


def test_support_mean_and_required_fields():
    r=with_score(mspss_friends=1,mspss_family=3)
    assert r['support_mean'] == 2
    assert r['support_item_count'] == 2
    assert r['scored_item_count'] == 11
    assert r['context_flags']['support']
    assert not with_score(mspss_friends=3,mspss_family=2)['context_flags']['support']
    assert not with_score(pas_catchup=5)['context_flags']['workload']
    assert 'mspss_so' not in survey.QUESTION_MAP
    assert len(survey.QUESTIONS) == 11
    assert all(not q.get('optional') for q in survey.QUESTIONS)
    for key in ('pas_catchup','mspss_friends','mspss_family'):
        values=raw();values.pop(key)
        assert key in io_manager.validate_student_form(values)[1]


def test_required_and_invalid_answers():
    for q in survey.QUESTIONS:
        for bad in (True, False, [], {}, float('nan'), float('inf'), q['min']-1, q['max']+1, 1.25):
            values=raw(); values[q['key']]=bad
            assert q['key'] in io_manager.validate_student_form(values)[1]
        values=raw();values.pop(q['key'],None)
        assert not io_manager.validate_student_form(values)[0], q['key']


def test_reflection_not_scored_or_sent():
    r=record(feelings_text='PRIVATE REFLECTION: I feel sad and depressed')
    assert finalised(feelings_text='PRIVATE REFLECTION: I feel sad and depressed')['risk_category'] == 'Low'
    assert 'feelings_text' not in r
    prompt=ai_manager.build_prompt(r)
    assert 'PRIVATE REFLECTION' not in prompt
    assert '2605581' not in prompt
    for key in survey.QUESTION_MAP:assert key in prompt
    assert survey.QUESTION_MAP['pss_1']['prompt'] in prompt
    assert "In the past month, how often have you felt you couldn't control the important things in your life?" in prompt
    assert 'mspss_so' not in prompt
    assert 'special person' not in prompt.lower()


def test_safety_override_keeps_pss_unchanged():
    for text in ('I want to kill myself','I feel suicidal','I want to die','I might self-harm','I can’t keep myself safe'):
        r=finalised(feelings_text=text)
        assert r['safety_flag'], text
        assert r['risk_category'] == 'High'
        assert r['stress_score'] == 1.0


def test_no_client_score_or_flag_trusted():
    r=record(stress_score=5,survey_version='fake',safety_flag=True,risk_category='High')
    assert 'stress_score' not in r
    assert 'risk_category' not in r
    assert not r['safety_flag']
    assert r['survey_version'] == survey.VERSION


def test_ai_score_far_from_answers_is_rejected():
    fields=json.loads(fake_generate(None,None,None))
    fields.update(stress_score=4.5,risk_score=0.875,risk_category='High',
                  soft_label='Please reach out',speak_prominence='high')
    r=logic_manager.apply_logic({**record(),**fields})
    assert not r['ok']
    assert r['error'] == 'score_mismatch'
    assert r['logic_stress_score'] == 1.0


def test_ai_score_matching_answers_is_kept():
    r=finalised()
    assert r['ok']
    assert (r['stress_score'],r['risk_category']) == (1.0,'Low')
    assert len(r['factor_insights']) == 4


def test_nonfinite_ai_numbers_rejected():
    for field in ('stress_score','confidence'):
        values=json.loads(fake_generate(None,None,None));values[field]=float('nan')
        assert not ai_manager.validate_ai_response(values)[0]


# --- Revised Pipeline ------------------------------------------------------

def test_new_record_storage_and_history():
    with frontend.campus_app() as ctx:
        ctx.values.update(feelings_text='SENSITIVE NOTE that must not be retained',pas_catchup=4,mspss_so=5)
        assert frontend.post_checkin(ctx).status_code == 200
        assert frontend.post_checkin(ctx, {'opt_in':True},'/save').status_code == 200
        stored=json.loads(ctx.path.read_text(encoding='utf-8'))[0]
        assert 'feelings_text' not in stored
        assert 'SENSITIVE NOTE' not in ctx.path.read_text(encoding='utf-8')
        assert 'mspss_so' not in stored
        assert stored['sleep_hours_avg'] == 6.5  # hours are stored, not a 1-5 band
        assert stored['pas_catchup'] == 4
        assert stored['stress_score'] == round(34/11,2)  # 10 answers at 3, catch-up 4
        assert ctx.client.get('/api/records').status_code == 401
        login_admin(ctx.client)
        row=ctx.client.get('/api/records').json['records'][0]
        assert row['survey_version'] == survey.VERSION
        assert row['fin_stress'] == 3
        assert 'mspss_so' not in row
        assert 'stress_level' not in row
        assert row['status'] == 'Evaluated'


def test_safety_remains_visible_when_ai_fails():
    with frontend.campus_app() as ctx:
        ctx.values['feelings_text']='I want to end my life'
        ctx.app.config['AI_GENERATE_FN']=lambda *args:'{}'
        response=frontend.post_checkin(ctx)
        assert response.status_code == 503
        assert response.json['safety_flag']
        assert 'advisor' in response.json
        assert not ctx.app.extensions['pending_checkins']


def test_safety_even_with_invalid_answers():
    with frontend.campus_app() as ctx:
        response=frontend.post_checkin(ctx, {**ctx.values,'pss_1':None,'feelings_text':'I want to die'})
        assert response.status_code == 400
        assert response.json['safety_flag']


def test_old_questionnaire_cannot_be_submitted_as_new():
    with frontend.campus_app() as ctx:
        response=frontend.post_checkin(ctx, dict(student_id='2605581',sleep_hours=7,stress_level=5,
                                academic_workload=5,financial_stress=5,social_support=5))
        assert response.status_code == 400
        assert 'pss_1' in response.json['errors']


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())
