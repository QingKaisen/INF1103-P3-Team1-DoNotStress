"""Flask application factory. Every request is handled by objects."""
from __future__ import annotations

import os
import secrets

from flask import Flask
from dotenv import load_dotenv

from checkin_service import CheckinService
from data_manager import DataManager
from io_manager import IOManager
from logic_manager import LogicManager
from web_controller import WebController

load_dotenv()


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
