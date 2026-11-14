"""Flask transport adapter with class-based routes and per-app state.

The WebController owns request/session/CSRF mechanics. CheckinService owns
orchestration; IOManager handles validation/presentation; DataManager owns
persistence; LogicManager owns scoring; AIManager owns provider/API calls.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from threading import RLock

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ai.function_provider import FunctionProvider
from ai.gemini_provider import GeminiProvider
from ai_manager import AIManager
from checkin_service import CheckinService
from data_manager import DataManager, TIER_TO_CATEGORY
from io_manager import IOManager
from jobs.checkin_runner import CheckinRunner
from logic_manager import LogicManager

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = "scrypt:32768:8:1$zoikUrXAJEAJCQlR$83ec3b2a32cedeb5721031e22f7fedc19ed5eea40c5995aa11d2c2ea1cc40845b393de85d68cda54706b5714293fc49ec360a477913186f00b46be7a6c31e8c1"


class WebController:
    """Convert Flask HTTP requests to OOP service calls, not domain logic."""

    def __init__(self, app: Flask, service: CheckinService):
        self._app = app
        self._service = service
        self._io: IOManager = service.io
        self._data: DataManager = service.data
        self._logic: LogicManager = service.logic
        self._pending: dict = {}
        self._submissions: dict = {}
        self._lock = RLock()
        self._jobs = CheckinRunner(workers=2)
        self._admin_sessions: dict = {}
        self._login_attempts: dict = {}
        # Introspection and test injection; no private data enters cookies.
        app.extensions['pending_checkins'] = self._pending
        app.extensions['pending_submissions'] = self._submissions
        app.extensions['admin_sessions'] = self._admin_sessions
        app.extensions['io_manager'] = self._io
        app.extensions['logic_manager'] = self._logic
        app.extensions['data_manager'] = self._data
        app.extensions['checkin_service'] = self._service
        app.extensions['checkin_runner'] = self._jobs
        app.extensions['web_controller'] = self
        self._register_routes()

    def _ai_for_request(self) -> AIManager:
        """Create a provider-backed manager from the current app configuration.

        Tests may swap AI_GENERATE_FN between requests; production always
        receives the real GeminiProvider which reads GEMINI_API_KEY.
        """
        generate_fn = self._app.config['AI_GENERATE_FN']
        provider = (FunctionProvider(generate_fn, os.getenv('GEMINI_API_KEY', ''),
                                     models=GeminiProvider.MODEL_CHAIN)
                    if generate_fn is not None else GeminiProvider())
        return AIManager(provider,
                         max_attempts=self._app.config['AI_MAX_ATTEMPTS'],
                         retry_delay_sec=self._app.config['AI_RETRY_DELAY_SEC'])

    def _register_routes(self) -> None:
        self._app.before_request(self.expire_records_and_check_csrf)
        self._app.after_request(self.prevent_answer_caching)
        self._app.register_error_handler(413, self.oversized_request)
        routes = (
            ('/', 'checkin', self.checkin, ['GET']),
            ('/garden', 'retired_page', self.retired_page, ['GET']),
            ('/admin/login', 'admin_login', self.admin_login, ['GET','POST']),
            ('/admin/logout', 'admin_logout', self.admin_logout, ['POST']),
            ('/', 'submit_checkin', self.submit_checkin, ['POST']),
            ('/records', 'records_page', self.records_page, ['GET']),
            ('/api/records', 'saved_records', self.saved_records, ['GET']),
            ('/api/summary', 'records_summary', self.records_summary, ['GET']),
            ('/result', 'result', self.result, ['GET']),
            ('/save', 'save_checkin', self.save_checkin, ['POST']),
            ('/new', 'new_checkin', self.new_checkin, ['GET']),
            ('/history', 'student_history', self.student_history, ['GET']),
            ('/api/history', 'student_history_api', self.student_history_api, ['GET']),
            ('/checkin/status/<job_id>', 'checkin_status', self.checkin_status, ['GET']),
        )
        for path, endpoint, handler, methods in routes:
            self._app.add_url_rule(path, endpoint=endpoint,
                                   view_func=handler, methods=methods)



    def is_admin(self):
        token = session.get('admin_token')
        with self._lock:
            expires = self._admin_sessions.get(token, 0)
            if expires > time.monotonic():
                return True
            self._admin_sessions.pop(token, None)
        session.pop('admin_token', None)
        return False

    def revoke_admin(self):
        with self._lock:
            self._admin_sessions.pop(session.pop('admin_token', None), None)

    def clear_pending(self):
        with self._lock:
            self._pending.pop(session.pop('checkin_token', None), None)

    def matched_pending_id(self, fingerprint):
        """Id of the pending row for this submission, if this session already saved one."""
        record_id = session.get('pending_record_id')
        if session.get('pending_fingerprint') == fingerprint and isinstance(record_id, str) and record_id.strip():
            return record_id.strip()
        claim = self._submissions.get(session.get('csrf_token'))
        if isinstance(claim, dict) and claim.get('fingerprint') == fingerprint:
            found = claim.get('record_id')
            if isinstance(found, str) and found.strip():
                return found.strip()
        return None

    def remember_submission(self, fingerprint, record_id, *, pending_save):
        """Keep the submission id. Pending saves also go in the session cookie."""
        token = session.get('csrf_token')
        if token:
            self._submissions[token] = {'fingerprint': fingerprint, 'record_id': record_id}
        if pending_save:
            session['pending_fingerprint'] = fingerprint
            session['pending_record_id'] = record_id
        else:
            session.pop('pending_fingerprint', None)
            session.pop('pending_record_id', None)

    def current_entry(self):
        with self._lock:
            return self._pending.get(session.get('checkin_token'))

    def expire_records_and_check_csrf(self):
        with self._lock:
            cutoff = time.monotonic() - self._app.config['PENDING_TTL_SECONDS']
            for key in [key for key, item in self._pending.items() if item['created'] < cutoff]:
                del self._pending[key]
            now = time.monotonic()
            for key in [key for key, expiry in self._admin_sessions.items() if expiry <= now]:
                del self._admin_sessions[key]
            for key in list(self._login_attempts):
                self._login_attempts[key] = [stamp for stamp in self._login_attempts[key] if stamp > now - self._app.config['ADMIN_LOGIN_WINDOW_SECONDS']]
                if not self._login_attempts[key]:
                    del self._login_attempts[key]
        if request.method == 'POST':
            expected = session.get('csrf_token', '')
            provided = request.headers.get('X-CSRF-Token', '')
            if not provided and (not request.is_json):
                provided = request.form.get('csrf_token', '')
            if not expected or not secrets.compare_digest(provided.encode('utf-8'), expected.encode('utf-8')):
                return (jsonify(message='This page has expired. Reload and try again.'), 403)

    def render_campus(self, page='home'):
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        entry = self.current_entry()
        return render_template('checkin.html', boot={'page': page, 'survey': self._io.survey.public_config(), 'isAdmin': self.is_admin(), 'adminLoginUrl': url_for('admin_login'), 'result': self._service.result_view(entry['record'], entry['saved']) if entry else None, 'csrfToken': session['csrf_token'], 'submitUrl': url_for('submit_checkin'), 'saveUrl': url_for('save_checkin'), 'newUrl': url_for('new_checkin'), 'homeUrl': url_for('checkin'), 'resultUrl': url_for('result'), 'historyUrl': url_for('student_history'), 'recordsPageUrl': url_for('records_page'), 'recordsUrl': url_for('saved_records'), 'summaryUrl': url_for('records_summary')})

    def checkin(self):
        return self.render_campus()

    def retired_page(self):
        return redirect(url_for('checkin'))

    def admin_login(self):
        if self.is_admin():
            return redirect(url_for('records_page'))
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        error, status = (None, 200)
        if request.method == 'POST':
            values = request.get_json(silent=True) if request.is_json else request.form
            values = values if isinstance(values, dict) or hasattr(values, 'get') else {}
            username, password = (values.get('username', ''), values.get('password', ''))
            address = request.remote_addr or 'local'
            with self._lock:
                attempts = self._login_attempts.setdefault(address, [])
                if len(attempts) >= self._app.config['ADMIN_LOGIN_MAX_ATTEMPTS']:
                    error, status = ('Too many sign-in attempts. Please try again in five minutes.', 429)
                else:
                    attempts.append(time.monotonic())
            if not error:
                valid_password = isinstance(password, str) and len(password) <= 256 and check_password_hash(ADMIN_PASSWORD_HASH, password)
                if username == ADMIN_USERNAME and valid_password:
                    self.revoke_admin()
                    token = secrets.token_urlsafe(32)
                    with self._lock:
                        self._admin_sessions[token] = time.monotonic() + self._app.config['ADMIN_SESSION_SECONDS']
                        self._login_attempts.pop(address, None)
                    session['admin_token'] = token
                    session['csrf_token'] = secrets.token_urlsafe(32)
                    return redirect(url_for('records_page'))
                error, status = ('Incorrect username or password.', 401)
        return (render_template('admin_login.html', error=error, csrf_token=session['csrf_token']), status)

    def admin_logout(self):
        self.revoke_admin()
        self.clear_pending()
        with self._lock:
            self._submissions.pop(session.get('csrf_token'), None)
        session.clear()
        return redirect(url_for('checkin'))

    def submit_checkin(self):
        values = request.get_json(silent=True) if request.is_json else request.form
        if not hasattr(values, 'get'):
            return (jsonify(message='Please submit a check-in form.'), 400)
        safety = self._io.survey.safety_check(values.get('feelings_text', ''))
        ok, record = self._io.validate_student_form(values)
        if not ok:
            return (jsonify(errors=record, safety_flag=safety, advisor=self._io.format_speak_to_advisor_panel('high') if safety else None), 400)
        self.clear_pending()
        fingerprint = self._service.fingerprint(record)
        job_id = self._jobs.submit(
            lambda record=record: self._service.process_checkin(record, ai=self._ai_for_request()))
        job = self._jobs.wait(job_id)
        if job["status"] != "done" or not isinstance(job.get("result"), tuple):
            ok, outcome = False, "unavailable"
        else:
            ok, outcome = job["result"]
        if not ok:
            try:
                with self._lock:
                    pending_id = self.matched_pending_id(fingerprint)
                    if pending_id is None:
                        record_id = uuid.uuid4().hex
                        saved = self._data.save_pending_record({**record, 'record_id': record_id}, opt_in=True)
                        if saved.get('ok') and isinstance(saved.get('record'), dict):
                            self.remember_submission(fingerprint, saved['record'].get('record_id') or record_id, pending_save=True)
                    else:
                        self.remember_submission(fingerprint, pending_id, pending_save=True)
            except Exception:
                logger.warning('Pending check-in could not be saved')
            copy = self._io.format_ai_error(outcome)
            return (jsonify(message='We could not complete your check-in right now. Your answers are still here; please try again.', error_code=outcome, safety_flag=safety, advisor=copy['speak_to_advisor']), 503)
        try:
            with self._lock:
                pending_id = self.matched_pending_id(fingerprint)
                if pending_id:
                    outcome = {**outcome, 'record_id': pending_id}
                    saved = self._data.replace_pending_record(pending_id, outcome, opt_in=True)
                else:
                    saved = self._data.save_record(outcome, opt_in=True)
                if saved.get('ok'):
                    stored_id = (saved.get('record') or {}).get('record_id')
                    if stored_id:
                        self.remember_submission(fingerprint, stored_id, pending_save=False)
        except Exception as exc:
            logger.warning('Saving the check-in failed: %s', type(exc).__name__)
            saved = {'ok': False}
        if not saved.get('ok'):
            return (jsonify(message='We could not save your check-in just now. Your answers are still here; please try again.'), 500)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._pending[token] = {'record': outcome, 'saved': True, 'created': time.monotonic()}
        session['checkin_token'] = token
        return jsonify(ok=True, jobId=job_id, result=self._service.result_view(outcome, saved=True))

    def records_page(self):
        if not self.is_admin():
            return redirect(url_for('admin_login'))
        return self.render_campus('records')

    def saved_records(self):
        """Read only explicitly saved check-ins; never expose free text or keys."""
        if not self.is_admin():
            return (jsonify(message='Admin sign-in is required to view saved records.'), 401)
        try:
            with self._lock:
                loaded = self._data.load_all_records()
        except Exception as exc:
            logger.warning('Loading saved records failed: %s', type(exc).__name__)
            loaded = {'ok': False}
        if not loaded.get('ok') or loaded.get('error'):
            return (jsonify(message='Saved records could not be loaded. Please try again.'), 500)
        records = [item for item in loaded.get('records', []) if isinstance(item, dict) and item.get('survey_version') == self._io.survey.version]
        cohorts = sorted({str(item.get('student_id', ''))[:2] for item in records if len(str(item.get('student_id', ''))) >= 2 and str(item.get('student_id', ''))[:2].isascii() and str(item.get('student_id', ''))[:2].isdigit()}, reverse=True)
        query = request.args.get('student_id', '').strip()
        tier = request.args.get('tier', '').strip()
        cohort = request.args.get('cohort_year', '').strip()
        if tier and tier not in TIER_TO_CATEGORY:
            return (jsonify(message="Choose a tier: You're doing ok, Worth a check-in, or Please reach out."), 400)
        if cohort and (len(cohort) not in (2, 4) or not cohort.isascii() or (not cohort.isdigit())):
            return (jsonify(message='Choose a valid cohort year.'), 400)
        filtered = self._data.filter_records(records, **{key: value for key, value in {'tier': tier, 'cohort_year': cohort}.items() if value})
        filtered = [item for item in filtered if query in str(item.get('student_id', ''))]
        columns = ('student_id', 'stress_score', 'risk_category', 'soft_label', 'pss_1', 'pss_2', 'pss_3', 'pss_4', 'sleep_hours_avg', 'sleep_quality', 'pas_workload', 'pas_catchup', 'fin_stress', 'mspss_friends', 'mspss_family', 'saved_at', 'survey_version')
        rows = []
        for item in filtered:
            row = {key: item.get(key) for key in columns}
            if not row.get('soft_label'):
                row['soft_label'] = {value: name for name, value in TIER_TO_CATEGORY.items()}.get(row.get('risk_category'))
            source = str(item.get('source', '')).lower()
            processed = item.get('ai_ok') is True or source in {'gemini', 'ai', 'ai_manager', 'gemini+logic', 'ai_logic'}
            pending = str(item.get('status', '')).strip().lower() == 'pending'
            row['status'] = 'Pending' if pending or not processed else 'Evaluated'
            rows.append(row)
        rows.sort(key=lambda row: str(row.get('saved_at') or ''), reverse=True)
        return jsonify(records=rows, total=len(records), matching=len(rows), cohorts=cohorts,
                       summary=self._service.staff_cards(filtered))

    def records_summary(self):
        """Admin-only, privacy-limited counts by cohort and risk tier."""
        if not self.is_admin():
            return jsonify(message='Admin sign-in is required.'), 401
        loaded = self._data.load_all_records()
        if not loaded.get('ok') or loaded.get('error'):
            return jsonify(message='Summary could not be loaded.'), 500
        current = [row for row in loaded.get('records', [])
                   if isinstance(row, dict) and row.get('survey_version') == self._io.survey.version]
        return jsonify(self._service.summarize_records(current))

    def result(self):
        if not self.current_entry():
            return redirect(url_for('checkin'))
        return self.render_campus('result')

    def save_checkin(self):
        """Retry a missed write. A result that is already saved is left as-is."""
        with self._lock:
            entry = self.current_entry()
            if not entry:
                return (jsonify(message='Your result has expired. Please start a new check-in.'), 409)
            if entry['saved']:
                return jsonify(ok=True, saved=True)
            try:
                saved = self._data.save_record(entry['record'], opt_in=True)
            except Exception as exc:
                logger.warning('Retrying the pending save failed: %s', type(exc).__name__)
                saved = {'ok': False}
            if not saved.get('ok'):
                return (jsonify(message='Your check-in could not be saved. Please try again.'), 500)
            entry['saved'] = True
        return jsonify(ok=True, saved=True)

    def new_checkin(self):
        self.clear_pending()
        return redirect(url_for('checkin'))

    def _own_student_id(self) -> str:
        """The student id of the result held for this browser. Not stored in the cookie."""
        entry = self.current_entry()
        if not entry or not isinstance(entry.get("record"), dict):
            return ""
        return str(entry["record"].get("student_id") or "").strip()

    def student_history(self):
        """This browser's own scores, while its result is still held."""
        if not self._own_student_id():
            return redirect(url_for('checkin'))
        return self.render_campus('history')

    def student_history_api(self):
        student_id = self._own_student_id()
        if not student_id:
            return jsonify(message='Complete a check-in in this browser to see your history.'), 401
        return jsonify(self._service.student_history(student_id))

    def checkin_status(self, job_id: str):
        """Poll a background check-in. The result itself stays on the result page."""
        found = self._jobs.status(job_id)
        if found is None:
            return jsonify(message='That check-in could not be found.'), 404
        return jsonify(found)

    def oversized_request(self, error):
        return (jsonify(message='Your response is too long. Please shorten it and try again.'), 413)

    def prevent_answer_caching(self, response):
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store'
        return response
