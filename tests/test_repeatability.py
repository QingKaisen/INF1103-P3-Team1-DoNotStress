"""Same input, same output across separate runs (brief constraint C4).

Uses the fake Gemini from test_frontend, so these tests never call the API.
The score, tier, label, tips, flags and factor cards come from fixed rules, so
they must be identical every run. Real Gemini is asked at temperature 0 so its
wording repeats as closely as the service allows.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import ai_manager
import data_manager
import logic_manager
import main
from plain_tests import function_suite
from test_frontend import fake_generate

ROOT = Path(__file__).resolve().parents[1]

ANSWERS = dict(
    student_id="2605581", survey_version="evidence-v3", safety_flag=False,
    pss_1=4, pss_2=2, pss_3=4, pss_4=3, sleep_hours_avg=5.5, sleep_quality=4,
    pas_workload=4, pas_catchup=3, fin_stress=4, mspss_friends=2, mspss_family=3,
)

# Runs one check-in in a fresh Python process and prints the outcome as JSON.
_ONE_RUN = """
import json, sys
sys.path[:0] = [{root!r}, {tests!r}]
from make_ai import process_checkin
from test_frontend import fake_generate
ok, outcome = process_checkin(json.loads({answers!r}), generate_fn=fake_generate,
                                   max_attempts=1, retry_delay_sec=0)
sys.stdout.write(json.dumps([ok, outcome], sort_keys=True))
"""


from logic_manager import LogicManager as _LogicManager
_LOGIC = _LogicManager()
from data_manager import DataManager
from make_ai import build_prompt, process_checkin

def _check_in():
    """Run the full AI -> Logic pipeline once with the fake Gemini."""
    ok, outcome = process_checkin(dict(ANSWERS), generate_fn=fake_generate,
                                       max_attempts=1, retry_delay_sec=0)
    assert ok, outcome
    return outcome


def test_same_answers_give_the_same_outcome_every_run():
    first = json.dumps(_check_in(), sort_keys=True)
    for _ in range(3):
        assert json.dumps(_check_in(), sort_keys=True) == first


def test_separate_processes_give_the_same_outcome():
    """Two fresh Python processes, same answers: identical outcome."""
    code = _ONE_RUN.format(root=str(ROOT), tests=str(ROOT / "tests"),
                           answers=json.dumps(ANSWERS))
    outputs = [
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                       text=True, timeout=60, check=True).stdout
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    ok, outcome = json.loads(outputs[0])
    assert ok
    assert outcome == json.loads(json.dumps(_check_in(), sort_keys=True))


def test_scoring_and_prompt_are_deterministic():
    first_score = _LOGIC.score_answers(ANSWERS)
    first_prompt = build_prompt(ANSWERS, instructions=_LOGIC.scoring_instructions())
    for _ in range(5):
        assert _LOGIC.score_answers(ANSWERS) == first_score
        assert build_prompt(
            ANSWERS, instructions=_LOGIC.scoring_instructions()) == first_prompt


def test_saved_records_load_the_same_every_time():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = str(Path(temp_dir) / "records.json")
        assert DataManager(path).save(_check_in(), opt_in=True)["ok"]
        before = Path(path).read_text(encoding="utf-8")
        first = DataManager(path).load()
        assert len(first) == 1
        assert DataManager(path).load() == first
        # Loading never rewrites the file.
        assert Path(path).read_text(encoding="utf-8") == before


def test_gemini_is_asked_for_repeatable_replies():
    from ai.gemini_provider import GeminiProvider
    assert GeminiProvider.TEMPERATURE == 0.0


def load_tests(loader, tests, pattern):
    """Let `python -m unittest discover -s tests` run this file's test_ functions."""
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
