"""Background check-in jobs and the student's own history page."""
import threading
import unittest

from plain_tests import function_suite
from test_frontend import campus_app, fake_generate, post_checkin


def test_checkin_runs_on_a_worker_and_can_be_polled():
    seen = {}

    def generate(prompt, api_key, model_name):
        seen["thread"] = threading.current_thread().name
        return fake_generate(prompt, api_key, model_name)

    with campus_app() as ctx:
        ctx.app.config["AI_GENERATE_FN"] = generate
        response = post_checkin(ctx)
        assert response.status_code == 200
        body = response.get_json()
        assert body["ok"] is True
        assert seen["thread"].startswith("checkin")
        status = ctx.client.get(f"/checkin/status/{body['jobId']}")
        assert status.status_code == 200
        assert status.get_json()["status"] == "done"
        missing = ctx.client.get("/checkin/status/not-a-job")
        assert missing.status_code == 404


def test_history_is_only_for_the_browser_that_checked_in():
    with campus_app() as ctx:
        denied = ctx.client.get("/api/history")
        assert denied.status_code == 401
        page = ctx.client.get("/")
        assert 'My history' in page.get_data(as_text=True)
        assert post_checkin(ctx).status_code == 200
        history = ctx.client.get("/api/history")
        assert history.status_code == 200
        payload = history.get_json()
        assert payload["count"] >= 1
        assert payload["comparison"] in {"first", "lower", "similar", "higher"}
        assert "feelings_text" not in payload
        shown = ctx.client.get("/history")
        assert shown.status_code == 200
        assert '"page": "history"' in shown.get_data(as_text=True)


def load_tests(loader, tests, pattern):
    return function_suite(globals())


if __name__ == "__main__":
    unittest.main()
