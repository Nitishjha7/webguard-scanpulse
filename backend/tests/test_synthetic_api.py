"""Synthetic check API and failure-state folding."""
import uuid

import pytest

from app.models import RunStatus, SyntheticCheck, SyntheticRun
from app.models.synthetic import REDACTED
from app.tasks.synthetic import _fold_failure_state

GOTO = {"action": "goto", "url": "https://example.com/login"}
LOGIN_STEPS = [
    GOTO,
    {"action": "fill", "selector": "#email", "value": "bot@example.com"},
    {"action": "fill", "selector": "#password", "value": "hunter2", "secret": True},
    {"action": "click", "selector": "button[type=submit]"},
    {"action": "expect_text", "selector": "h1", "value": "Dashboard"},
]


@pytest.fixture(autouse=True)
def fast_dns():
    """Answer name lookups locally.

    Every step validation resolves its goto URL through the SSRF guard, which
    made this file take minutes on real DNS. IP literals still resolve to
    themselves, so the private-address test below exercises the real guard.
    """
    import ipaddress
    import socket
    from unittest.mock import patch

    real = socket.getaddrinfo

    def _resolve(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if str(host).endswith("example.com") or host == "example.com":
                return [(2, 1, 6, "", ("93.184.216.34", port))]
            return real(host, port, *args, **kwargs)
        return real(host, port, *args, **kwargs)

    with patch("socket.getaddrinfo", _resolve):
        yield


@pytest.fixture
def headers(auth_headers):
    h, _ = auth_headers()
    return h


def _create(client, headers, **overrides):
    payload = {"name": "Checkout journey", "steps": LOGIN_STEPS, **overrides}
    return client.post("/api/v1/synthetic", headers=headers, json=payload)


class TestCreation:
    def test_a_valid_journey_is_accepted(self, client, headers):
        resp = _create(client, headers)

        assert resp.status_code == 201
        body = resp.get_json()["check"]
        assert body["step_count"] == 5
        assert body["interval_seconds"] == 900
        assert body["failure_threshold"] == 2

    def test_malformed_steps_are_rejected_with_the_allowed_actions(self, client, headers):
        resp = _create(client, headers, steps=[{"action": "evaluate", "script": "x"}])

        assert resp.status_code == 422
        assert "expect_text" in resp.get_json()["error"]["details"]["allowed_actions"]

    def test_a_journey_pointed_at_a_private_address_is_refused(self, client, headers):
        resp = _create(
            client, headers, steps=[{"action": "goto", "url": "http://169.254.169.254/"}]
        )

        assert resp.status_code == 422
        assert "non-public" in resp.get_json()["error"]["message"]

    def test_duplicate_name_is_a_conflict(self, client, headers):
        _create(client, headers, name="Same")

        assert _create(client, headers, name="Same").status_code == 409

    def test_interval_below_a_minute_is_rejected(self, client, headers):
        assert _create(client, headers, interval_seconds=10).status_code == 422

    def test_a_check_cannot_attach_to_another_tenants_monitor(self, client, auth_headers):
        owner, _ = auth_headers("owner@example.com")
        other, _ = auth_headers("other@example.com")
        monitor_id = client.post(
            "/api/v1/monitors",
            headers=owner,
            json={"name": "Theirs", "url": "https://theirs.example.com"},
        ).get_json()["monitor"]["id"]

        resp = _create(client, other, monitor_id=monitor_id)

        assert resp.status_code == 404

    def test_unknown_monitor_id_shape_is_a_400(self, client, headers):
        assert _create(client, headers, monitor_id="not-a-uuid").status_code == 400


class TestSecretHandling:
    def test_reading_a_check_masks_the_password(self, client, headers):
        check_id = _create(client, headers).get_json()["check"]["id"]

        steps = client.get(f"/api/v1/synthetic/{check_id}", headers=headers).get_json()["check"][
            "steps"
        ]

        assert steps[2]["value"] == REDACTED
        assert steps[1]["value"] == "bot@example.com"

    def test_the_real_secret_is_still_stored(self, client, headers, db):
        check_id = _create(client, headers).get_json()["check"]["id"]

        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))

        assert check.steps[2]["value"] == "hunter2"

    def test_a_read_edit_write_round_trip_does_not_destroy_the_secret(
        self, client, headers, db
    ):
        """Regression risk: GET masks the value, so a client that PATCHes the
        steps back unchanged would otherwise store '********' as the password."""
        check_id = _create(client, headers).get_json()["check"]["id"]
        read_back = client.get(f"/api/v1/synthetic/{check_id}", headers=headers).get_json()[
            "check"
        ]["steps"]

        resp = client.patch(
            f"/api/v1/synthetic/{check_id}", headers=headers, json={"steps": read_back}
        )

        assert resp.status_code == 200
        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))
        assert check.steps[2]["value"] == "hunter2"

    def test_a_deliberately_changed_secret_is_saved(self, client, headers, db):
        check_id = _create(client, headers).get_json()["check"]["id"]
        new_steps = [dict(s) for s in LOGIN_STEPS]
        new_steps[2]["value"] = "new-password"

        client.patch(f"/api/v1/synthetic/{check_id}", headers=headers, json={"steps": new_steps})

        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))
        assert check.steps[2]["value"] == "new-password"


class TestTenantIsolation:
    @pytest.fixture
    def foreign_check(self, client, auth_headers):
        owner, _ = auth_headers("a@example.com")
        intruder, _ = auth_headers("b@example.com")
        check_id = _create(client, owner).get_json()["check"]["id"]
        return intruder, check_id

    @pytest.mark.parametrize(
        "method,suffix",
        [("get", ""), ("patch", ""), ("delete", ""), ("post", "/run"), ("get", "/runs")],
    )
    def test_foreign_check_is_not_found(self, client, foreign_check, method, suffix):
        intruder, check_id = foreign_check

        resp = getattr(client, method)(
            f"/api/v1/synthetic/{check_id}{suffix}", headers=intruder, json={}
        )

        assert resp.status_code == 404

    def test_listing_does_not_leak(self, client, foreign_check):
        intruder, _ = foreign_check
        assert client.get("/api/v1/synthetic", headers=intruder).get_json()["checks"] == []

    def test_a_foreign_run_screenshot_is_not_served(self, client, auth_headers, db):
        from datetime import datetime, timezone

        owner, _ = auth_headers("x@example.com")
        intruder, _ = auth_headers("y@example.com")
        check_id = _create(client, owner).get_json()["check"]["id"]
        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))
        run = SyntheticRun(
            check_id=check.id,
            org_id=check.org_id,
            status=RunStatus.FAILED,
            screenshot="run-deadbeef.png",
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(run)
        db.session.commit()

        resp = client.get(f"/api/v1/synthetic/runs/{run.id}/screenshot", headers=intruder)

        assert resp.status_code == 404


class TestRoles:
    def test_viewer_cannot_create_or_trigger(self, client, auth_headers):
        headers, _ = auth_headers()
        check_id = _create(client, headers).get_json()["check"]["id"]
        client.post(
            "/api/v1/auth/users",
            headers=headers,
            json={"email": "v@example.com", "password": "password123", "role": "Viewer"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"email": "v@example.com", "password": "password123"}
        ).get_json()["access_token"]
        viewer = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/synthetic", headers=viewer).status_code == 200
        assert _create(client, viewer, name="Nope").status_code == 403
        assert client.post(f"/api/v1/synthetic/{check_id}/run", headers=viewer).status_code == 403


class TestRunHistory:
    def test_summary_of_an_empty_window(self, client, headers):
        check_id = _create(client, headers).get_json()["check"]["id"]

        body = client.get(f"/api/v1/synthetic/{check_id}/runs", headers=headers).get_json()

        assert body["summary"]["runs"] == 0
        assert body["summary"]["success_rate"] is None

    def test_success_rate_is_computed(self, client, headers, db):
        from datetime import datetime, timezone

        check_id = _create(client, headers).get_json()["check"]["id"]
        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))
        for status in (RunStatus.PASSED, RunStatus.PASSED, RunStatus.PASSED, RunStatus.FAILED):
            db.session.add(
                SyntheticRun(
                    check_id=check.id,
                    org_id=check.org_id,
                    status=status,
                    duration_ms=1000.0,
                    started_at=datetime.now(timezone.utc),
                )
            )
        db.session.commit()

        body = client.get(f"/api/v1/synthetic/{check_id}/runs", headers=headers).get_json()

        assert body["summary"]["runs"] == 4
        assert body["summary"]["success_rate"] == 75.0
        assert body["summary"]["avg_duration_ms"] == 1000.0

    def test_run_with_no_screenshot_is_a_404_not_a_crash(self, client, headers, db):
        from datetime import datetime, timezone

        check_id = _create(client, headers).get_json()["check"]["id"]
        check = db.session.get(SyntheticCheck, uuid.UUID(check_id))
        run = SyntheticRun(
            check_id=check.id,
            org_id=check.org_id,
            status=RunStatus.PASSED,
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(run)
        db.session.commit()

        resp = client.get(f"/api/v1/synthetic/runs/{run.id}/screenshot", headers=headers)

        assert resp.status_code == 404


class TestFailureStateFolding:
    """Same anti-flapping rule the uptime monitors use."""

    def _check(self, threshold=2):
        return SyntheticCheck(
            name="c", steps=[GOTO], consecutive_failures=0, failure_threshold=threshold
        )

    def test_one_failure_does_not_alert(self):
        check = self._check()

        assert _fold_failure_state(check, RunStatus.FAILED) is None
        assert check.consecutive_failures == 1

    def test_reaching_the_threshold_alerts_once(self):
        check = self._check()

        _fold_failure_state(check, RunStatus.FAILED)

        assert _fold_failure_state(check, RunStatus.FAILED) == "failing"

    def test_continued_failure_does_not_re_alert(self):
        check = self._check()
        for _ in range(2):
            _fold_failure_state(check, RunStatus.FAILED)

        assert _fold_failure_state(check, RunStatus.FAILED) is None
        assert check.consecutive_failures == 3

    def test_recovery_from_a_failing_state_alerts(self):
        check = self._check()
        for _ in range(2):
            _fold_failure_state(check, RunStatus.FAILED)

        assert _fold_failure_state(check, RunStatus.PASSED) == "recovered"
        assert check.consecutive_failures == 0

    def test_recovery_below_the_threshold_is_silent(self):
        """Nobody was told it broke, so nobody needs telling it is fine."""
        check = self._check()
        _fold_failure_state(check, RunStatus.FAILED)

        assert _fold_failure_state(check, RunStatus.PASSED) is None

    def test_error_status_counts_as_a_failure(self):
        """An ERROR means we lost coverage, which is still worth knowing."""
        check = self._check()
        _fold_failure_state(check, RunStatus.ERROR)

        assert _fold_failure_state(check, RunStatus.ERROR) == "failing"
