"""Flask application factory; all runtime behaviour is coordinated by objects.

One class per file: IOManager, AIManager, LogicManager, DataManager,
CheckinService, WebController. Legacy function entry points are retained
only for backwards compatibility with the existing Phase 1 test scripts.
"""
from __future__ import annotations

import os
import secrets

from flask import Flask
from dotenv import load_dotenv

import ai_manager
from checkin_service import CheckinService
from data_manager import DataManager
from io_manager import IOManager
from logic_manager import LogicManager
from web_controller import WebController

load_dotenv()


def _compat_service() -> CheckinService:
    """Compatibility adapter for pre-refactor direct-call tests only.

    The production application constructs its managers once per Flask app.
    Legacy test functions get a fresh, side-effect-free service here.
    """
    return CheckinService(IOManager(), LogicManager(), DataManager())


def submission_fingerprint(record):
    """Backwards-compatible facade; implementation resides in CheckinService."""
    return _compat_service().fingerprint(record)


def process_checkin(record, *, generate_fn=None, max_attempts=3, retry_delay_sec=0.5):
    """Backwards-compatible facade for original direct-call tests.

    Calls ai_manager.analyse_student so existing tests can patch that
    function. The HTTP app does not use this function; it uses CheckinService.
    """
    service = _compat_service()
    ok, enriched, error = ai_manager.analyse_student(
        record,
        generate_fn=generate_fn,
        max_attempts=max_attempts,
        retry_delay_sec=retry_delay_sec,
        questions=service.io.survey.questions,
        instructions=service.logic.scoring_instructions(),
        verify_fn=service.logic.verify_ai_scores,
    )
    if not ok or not isinstance(enriched, dict):
        code = error.get('error_code', 'unavailable') if isinstance(error, dict) else 'unavailable'
        return False, code
    finalized = service.logic.apply_logic(enriched)
    if not isinstance(finalized, dict) or not finalized.get('ok'):
        return False, 'unavailable'
    return True, finalized


def result_view(record, saved=False):
    """Backwards-compatible facade; no separate presentation pipeline."""
    return _compat_service().result_view(record, saved=saved)



def create_app(test_config=None, *, io: IOManager | None = None,
               logic: LogicManager | None = None,
               data: DataManager | None = None) -> Flask:
    """Compose dependencies per Flask application; no global mutable managers."""
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        DATA_PATH=os.environ.get('DONOTSTRESS_DATA_PATH') or None,
        AI_GENERATE_FN=None, AI_MAX_ATTEMPTS=3, AI_RETRY_DELAY_SEC=0.5,
        PENDING_TTL_SECONDS=1800, MAX_CONTENT_LENGTH=64 * 1024,
        ADMIN_SESSION_SECONDS=1800, ADMIN_LOGIN_MAX_ATTEMPTS=5,
        ADMIN_LOGIN_WINDOW_SECONDS=300,
    )
    if test_config:
        app.config.update(test_config)
    service = CheckinService(
        io or IOManager(), logic or LogicManager(),
        data or DataManager(app.config['DATA_PATH']),
    )
    WebController(app, service)
    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')),
            debug=os.environ.get('FLASK_DEBUG', '0').lower() in {'1', 'true', 'yes'})
