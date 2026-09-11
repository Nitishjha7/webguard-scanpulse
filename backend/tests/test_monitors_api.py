"""Monitor CRUD: validation, quotas and the incident/ping read endpoints."""
import pytest


@pytest.fixture
def headers(auth_headers):
    h, _ = auth_headers()
    return h


def _create(client, headers, **overrides):
    payload = {"name": "Test", "url": "https://target.example.com", **overrides}
    return client.post("/api/v1/monitors", headers=headers, json=payload)


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url", ["ftp://example.com", "javascript:alert(1)", "example.com", "https://", ""]
    )
    def test_bad_urls_are_rejected(self, client, headers, url):
        assert _create(client, headers, url=url).status_code == 422

    def test_https_url_is_accepted(self, client, headers):
        assert _create(client, headers).status_code == 201


class TestDuplicates:
    def test_creating_a_duplicate_url_is_a_conflict(self, client, headers):
        _create(client, headers, url="https://dup.example.com")

        resp = _create(client, headers, name="Second", url="https://dup.example.com")

        assert resp.status_code == 409

    def test_patching_onto_an_existing_url_is_a_conflict_not_a_500(self, client, headers):
        """Regression: create checked for duplicates, PATCH did not.

        The unique constraint surfaced as an unhandled IntegrityError and the
        API returned 500.
        """
        _create(client, headers, name="First", url="https://a.example.com")
        second = _create(client, headers, name="Second", url="https://b.example.com").get_json()

        resp = client.patch(
            f"/api/v1/monitors/{second['monitor']['id']}",
            headers=headers,
            json={"url": "https://a.example.com"},
        )

        assert resp.status_code == 409

    def test_patching_a_monitor_to_its_own_url_is_allowed(self, client, headers):
        monitor = _create(client, headers, url="https://same.example.com").get_json()["monitor"]

        resp = client.patch(
            f"/api/v1/monitors/{monitor['id']}",
            headers=headers,
            json={"url": "https://same.example.com", "name": "Renamed"},
        )

        assert resp.status_code == 200
        assert resp.get_json()["monitor"]["name"] == "Renamed"

    def test_two_tenants_may_monitor_the_same_url(self, client, auth_headers):
        a, _ = auth_headers("a@example.com")
        b, _ = auth_headers("b@example.com")

        assert _create(client, a, url="https://shared.example.com").status_code == 201
        assert _create(client, b, url="https://shared.example.com").status_code == 201


class TestIntervalValidation:
    def test_interval_below_the_floor_is_rejected(self, client, headers):
        assert _create(client, headers, interval_seconds=5).status_code == 422

    def test_absurd_interval_is_rejected(self, client, headers):
        assert _create(client, headers, interval_seconds=999_999).status_code == 422

    def test_non_numeric_interval_is_rejected(self, client, headers):
        assert _create(client, headers, interval_seconds="soon").status_code == 422

    def test_timeout_must_be_positive(self, client, headers):
        assert _create(client, headers, timeout_seconds=0).status_code == 422


class TestQuorumSettings:
    def test_failure_threshold_cannot_be_lowered_to_one(self, client, headers):
        """The anti-flapping floor is not configurable away."""
        assert _create(client, headers, failure_threshold=1).status_code == 422

    def test_failure_threshold_defaults_to_two(self, client, headers):
        body = _create(client, headers).get_json()
        assert body["monitor"]["failure_threshold"] == 2

    def test_degraded_threshold_round_trips(self, client, headers):
        body = _create(client, headers, degraded_latency_ms=1500).get_json()
        assert body["monitor"]["degraded_latency_ms"] == 1500

    def test_degraded_threshold_can_be_cleared_with_null(self, client, headers):
        monitor = _create(client, headers, degraded_latency_ms=1500).get_json()["monitor"]

        resp = client.patch(
            f"/api/v1/monitors/{monitor['id']}", headers=headers, json={"degraded_latency_ms": None}
        )

        assert resp.get_json()["monitor"]["degraded_latency_ms"] is None

    @pytest.mark.parametrize("value", [0, -5, "fast"])
    def test_invalid_degraded_threshold_is_rejected(self, client, headers, value):
        assert _create(client, headers, degraded_latency_ms=value).status_code == 422


class TestQuota:
    def test_quota_is_enforced(self, client, headers, app):
        original = app.config["MAX_MONITORS_PER_ORG"]
        app.config["MAX_MONITORS_PER_ORG"] = 2
        try:
            for n in range(2):
                assert _create(client, headers, url=f"https://q{n}.example.com").status_code == 201

            resp = _create(client, headers, url="https://over.example.com")

            assert resp.status_code == 409
            assert resp.get_json()["error"]["details"]["limit"] == 2
        finally:
            app.config["MAX_MONITORS_PER_ORG"] = original


class TestReadEndpoints:
    def test_pings_endpoint_summarises_an_empty_window(self, client, headers):
        monitor = _create(client, headers).get_json()["monitor"]

        body = client.get(f"/api/v1/monitors/{monitor['id']}/pings", headers=headers).get_json()

        assert body["summary"]["checks"] == 0
        # No divide-by-zero on a monitor that has never been probed.
        assert body["summary"]["uptime_percent"] is None

    def test_uptime_percentage_is_computed(self, client, headers, db, make_org):
        from app.models import Monitor, PingLog

        monitor = _create(client, headers).get_json()["monitor"]
        row = db.session.get(Monitor, __import__("uuid").UUID(monitor["id"]))
        for is_up in (True, True, True, False):
            db.session.add(
                PingLog(
                    monitor_id=row.id,
                    org_id=row.org_id,
                    is_up=is_up,
                    latency_ms=100.0,
                    status_code=200 if is_up else 500,
                )
            )
        db.session.commit()

        body = client.get(f"/api/v1/monitors/{monitor['id']}/pings", headers=headers).get_json()

        assert body["summary"]["checks"] == 4
        assert body["summary"]["uptime_percent"] == 75.0
        assert body["summary"]["avg_latency_ms"] == 100.0

    def test_ssl_and_security_are_404_before_any_scan(self, client, headers):
        monitor = _create(client, headers).get_json()["monitor"]

        for suffix in ("ssl", "security"):
            resp = client.get(f"/api/v1/monitors/{monitor['id']}/{suffix}", headers=headers)
            assert resp.status_code == 404

    def test_incident_feed_is_empty_for_a_new_monitor(self, client, headers):
        monitor = _create(client, headers).get_json()["monitor"]

        body = client.get(
            f"/api/v1/monitors/{monitor['id']}/incidents", headers=headers
        ).get_json()

        assert body["incidents"] == []
        assert body["summary"] == {}


class TestIncidentFeed:
    def test_summary_counts_open_incidents(self, client, headers, db):
        from app.models import Monitor
        from app.services import incidents as sm
        from tests.conftest import probe_down

        monitor_json = _create(client, headers).get_json()["monitor"]
        monitor = db.session.get(Monitor, __import__("uuid").UUID(monitor_json["id"]))
        for _ in range(2):
            sm.evaluate(monitor, probe_down())
        db.session.commit()

        body = client.get("/api/v1/incidents/summary", headers=headers).get_json()

        assert body["open"] == 1
        assert body["by_status"]["DOWN"] == 1

    def test_state_filter_rejects_unknown_values(self, client, headers):
        assert client.get("/api/v1/incidents?state=weird", headers=headers).status_code == 422

    def test_resolving_an_already_resolved_incident_is_a_conflict(self, client, headers, db):
        from app.models import Incident, Monitor
        from app.services import incidents as sm
        from tests.conftest import probe_down, probe_up

        monitor_json = _create(client, headers).get_json()["monitor"]
        monitor = db.session.get(Monitor, __import__("uuid").UUID(monitor_json["id"]))
        for _ in range(2):
            sm.evaluate(monitor, probe_down())
        sm.evaluate(monitor, probe_up())
        db.session.commit()
        incident = db.session.query(Incident).one()

        resp = client.post(f"/api/v1/incidents/{incident.id}/resolve", headers=headers, json={})

        assert resp.status_code == 409
