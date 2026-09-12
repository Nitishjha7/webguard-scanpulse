"""Public status pages.

These are the only unauthenticated pages in the system, so most of what is
tested here is what must *not* appear on them.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Incident, IncidentStatus, Monitor, PingLog, PingRollupDaily, StatusPage


@pytest.fixture
def headers(auth_headers):
    h, _ = auth_headers()
    return h


@pytest.fixture
def monitor_for(client, db):
    def _make(headers, name="API", url="https://api.example.com"):
        body = client.post(
            "/api/v1/monitors", headers=headers, json={"name": name, "url": url}
        ).get_json()["monitor"]
        return db.session.get(Monitor, uuid.UUID(body["id"]))

    return _make


def _create_page(client, headers, **overrides):
    payload = {"name": "Acme Status", "is_published": True, **overrides}
    return client.post("/api/v1/status-pages", headers=headers, json=payload)


class TestConfiguration:
    def test_a_page_gets_a_slug_and_a_public_path(self, client, headers):
        body = _create_page(client, headers).get_json()["status_page"]

        assert body["slug"] == "acme-status"
        assert body["public_path"] == "/status/acme-status"

    def test_reserved_slugs_are_refused(self, client, headers):
        """A page at /status/api would shadow our own routes."""
        resp = _create_page(client, headers, slug="api")

        assert resp.status_code == 422
        assert "reserved" in resp.get_json()["error"]["message"].lower()

    def test_slugs_are_globally_unique(self, client, auth_headers):
        a, _ = auth_headers("a@example.com")
        b, _ = auth_headers("b@example.com")
        _create_page(client, a, name="Shared", slug="shared")

        assert _create_page(client, b, name="Shared", slug="shared").status_code == 409

    @pytest.mark.parametrize(
        "domain", ["https://status.example.com", "status.example.com/path", "nodot", "a:b"]
    )
    def test_custom_domain_must_be_a_bare_hostname(self, client, headers, domain):
        assert _create_page(client, headers, custom_domain=domain).status_code == 422

    def test_a_valid_custom_domain_is_accepted(self, client, headers):
        resp = _create_page(client, headers, custom_domain="Status.Example.COM")

        assert resp.get_json()["status_page"]["custom_domain"] == "status.example.com"

    def test_monitor_ids_must_belong_to_the_caller(self, client, auth_headers, monitor_for):
        owner, _ = auth_headers("owner@example.com")
        intruder, _ = auth_headers("intruder@example.com")
        theirs = monitor_for(owner)

        resp = _create_page(client, intruder, monitor_ids=[str(theirs.id)])

        assert resp.status_code == 422

    def test_disclosure_flags_default_to_closed(self, client, headers):
        body = _create_page(client, headers).get_json()["status_page"]

        # A public page says whether a service is up, not where it lives.
        assert body["show_urls"] is False
        assert body["show_latency"] is False
        assert body["show_incidents"] is True


class TestPublicAccess:
    def test_a_published_page_renders(self, client, headers):
        _create_page(client, headers, name="Acme Status")

        resp = client.get("/status/acme-status")

        assert resp.status_code == 200
        assert b"Acme Status" in resp.data

    def test_an_unpublished_page_is_a_404(self, client, headers):
        _create_page(client, headers, is_published=False)

        assert client.get("/status/acme-status").status_code == 404

    def test_a_missing_page_and_an_unpublished_one_look_identical(self, client, headers):
        """Whether a slug exists is not something an anonymous visitor learns."""
        _create_page(client, headers, slug="hidden", is_published=False)

        hidden = client.get("/status/hidden")
        missing = client.get("/status/does-not-exist")

        assert hidden.status_code == missing.status_code == 404
        assert hidden.data == missing.data

    def test_no_authentication_is_required(self, client, headers):
        _create_page(client, headers)

        # No Authorization header at all.
        assert client.get("/status/acme-status").status_code == 200

    def test_the_json_feed_is_embeddable(self, client, headers):
        _create_page(client, headers)

        resp = client.get("/status/acme-status.json")

        assert resp.status_code == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert resp.get_json()["page"]["slug"] == "acme-status"

    def test_security_headers_are_set(self, client, headers):
        _create_page(client, headers)

        resp = client.get("/status/acme-status")

        assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"

    def test_a_custom_domain_serves_the_page_at_the_root(self, client, headers):
        _create_page(client, headers, custom_domain="status.acme.test")

        resp = client.get("/", headers={"Host": "status.acme.test"})

        assert resp.status_code == 200
        assert b"Acme Status" in resp.data

    def test_our_own_root_still_serves_the_service_banner(self, client, headers):
        _create_page(client, headers, custom_domain="status.acme.test")

        resp = client.get("/")

        assert resp.get_json()["service"] == "webguard-scanpulse"


class TestDisclosure:
    @pytest.fixture
    def page_with_monitor(self, client, db, headers, monitor_for):
        monitor = monitor_for(headers, name="Public API", url="https://internal-api.acme.test/v2")
        page_id = _create_page(
            client, headers, monitor_ids=[str(monitor.id)]
        ).get_json()["status_page"]["id"]
        return page_id, monitor

    def test_monitor_urls_are_hidden_by_default(self, client, page_with_monitor):
        resp = client.get("/status/acme-status")

        assert b"Public API" in resp.data
        assert b"internal-api.acme.test" not in resp.data

    def test_urls_appear_only_when_opted_in(self, client, headers, page_with_monitor):
        page_id, _ = page_with_monitor
        client.patch(f"/api/v1/status-pages/{page_id}", headers=headers, json={"show_urls": True})

        assert b"internal-api.acme.test" in client.get("/status/acme-status").data

    def test_incident_root_cause_is_never_published(
        self, client, db, headers, page_with_monitor
    ):
        """Root cause is written by our probes and routinely names internal
        infrastructure — resolved IPs, DNS errors."""
        _, monitor = page_with_monitor
        db.session.add(
            Incident(
                monitor_id=monitor.id,
                org_id=monitor.org_id,
                status=IncidentStatus.DOWN,
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                root_cause="blocked: DNS resolution failed for db-primary.internal 10.0.4.7",
            )
        )
        db.session.commit()

        resp = client.get("/status/acme-status")

        assert b"db-primary.internal" not in resp.data
        assert b"10.0.4.7" not in resp.data
        # The incident itself is still reported.
        assert b"Public API" in resp.data

    def test_the_json_feed_carries_no_root_cause_either(
        self, client, db, headers, page_with_monitor
    ):
        _, monitor = page_with_monitor
        db.session.add(
            Incident(
                monitor_id=monitor.id,
                org_id=monitor.org_id,
                status=IncidentStatus.DOWN,
                started_at=datetime.now(timezone.utc),
                root_cause="secret internal detail",
            )
        )
        db.session.commit()

        body = client.get("/status/acme-status.json").get_json()

        assert body["incidents"]
        assert "root_cause" not in body["incidents"][0]
        assert "secret" not in str(body)

    def test_another_tenants_monitors_never_appear(
        self, client, db, auth_headers, monitor_for
    ):
        owner, _ = auth_headers("owner2@example.com")
        other, _ = auth_headers("other2@example.com")
        monitor_for(other, name="Their Secret Service", url="https://theirs.example.com")
        _create_page(client, owner, name="Owner Status", slug="owner-status")

        resp = client.get("/status/owner-status")

        assert b"Their Secret Service" not in resp.data


class TestReportedState:
    @pytest.fixture
    def published(self, client, db, headers, monitor_for):
        monitor = monitor_for(headers)
        _create_page(client, headers, monitor_ids=[str(monitor.id)])
        return monitor

    def test_a_monitor_with_no_recent_probe_is_unknown_not_operational(
        self, client, published
    ):
        """Saying 'operational' without a recent probe is a claim we cannot
        support."""
        body = client.get("/status/acme-status.json").get_json()

        assert body["components"][0]["state"] == "unknown"
        assert body["overall"] == "unknown"

    def test_a_recently_probed_monitor_is_operational(self, client, db, published):
        published.last_checked_at = datetime.now(timezone.utc)
        db.session.commit()

        body = client.get("/status/acme-status.json").get_json()

        assert body["components"][0]["state"] == "operational"
        assert body["overall"] == "operational"

    def test_a_stale_probe_falls_back_to_unknown(self, client, db, published):
        published.last_checked_at = datetime.now(timezone.utc) - timedelta(hours=6)
        db.session.commit()

        assert client.get("/status/acme-status.json").get_json()["overall"] == "unknown"

    def test_an_open_incident_drives_the_banner(self, client, db, published):
        published.last_checked_at = datetime.now(timezone.utc)
        db.session.add(
            Incident(
                monitor_id=published.id,
                org_id=published.org_id,
                status=IncidentStatus.DOWN,
                started_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()

        body = client.get("/status/acme-status.json").get_json()

        assert body["components"][0]["state"] == "down"
        assert body["overall"] == "down"

    def test_the_worst_component_sets_the_overall_state(self, client, db, headers, monitor_for):
        healthy = monitor_for(headers, name="Healthy", url="https://ok.example.com")
        broken = monitor_for(headers, name="Broken", url="https://bad.example.com")
        now = datetime.now(timezone.utc)
        healthy.last_checked_at = broken.last_checked_at = now
        db.session.add(
            Incident(
                monitor_id=broken.id,
                org_id=broken.org_id,
                status=IncidentStatus.DEGRADED,
                started_at=now,
            )
        )
        _create_page(client, headers, monitor_ids=[str(healthy.id), str(broken.id)])
        db.session.commit()

        assert client.get("/status/acme-status.json").get_json()["overall"] == "degraded"

    def test_history_comes_from_the_daily_rollup(self, client, db, published):
        """A 90-day page must not read a quarter-million raw rows."""
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for days_ago in range(1, 6):
            db.session.add(
                PingRollupDaily(
                    monitor_id=published.id,
                    org_id=published.org_id,
                    bucket=midnight - timedelta(days=days_ago),
                    checks=288,
                    up_checks=288,
                    uptime_percent=100.0,
                    avg_latency_ms=120.0,
                )
            )
        db.session.commit()

        component = client.get("/status/acme-status.json").get_json()["components"][0]

        assert len(component["history"]) == 5
        assert component["uptime_percent"] == 100.0

    def test_today_is_taken_from_raw_probes(self, client, db, published):
        """The daily rollup only covers completed days; without this the page
        would show a gap for the day visitors care about most."""
        now = datetime.now(timezone.utc)
        published.last_checked_at = now
        for is_up in (True, True, True, False):
            db.session.add(
                PingLog(
                    monitor_id=published.id,
                    org_id=published.org_id,
                    is_up=is_up,
                    latency_ms=100.0,
                    checked_at=now,
                )
            )
        db.session.commit()

        component = client.get("/status/acme-status.json").get_json()["components"][0]

        assert component["history"][-1]["date"] == now.date().isoformat()
        assert component["history"][-1]["uptime_percent"] == 75.0

    def test_latency_is_hidden_unless_opted_in(self, client, db, headers, published):
        now = datetime.now(timezone.utc)
        published.last_checked_at = now
        db.session.add(
            PingLog(
                monitor_id=published.id,
                org_id=published.org_id,
                is_up=True,
                latency_ms=137.0,
                checked_at=now,
            )
        )
        db.session.commit()

        component = client.get("/status/acme-status.json").get_json()["components"][0]

        assert "latency_ms" not in component


class TestAdminIsolation:
    def test_a_foreign_page_is_not_found(self, client, auth_headers):
        owner, _ = auth_headers("x@example.com")
        intruder, _ = auth_headers("y@example.com")
        page_id = _create_page(client, owner).get_json()["status_page"]["id"]

        for method in ("get", "patch", "delete"):
            resp = getattr(client, method)(
                f"/api/v1/status-pages/{page_id}", headers=intruder, json={}
            )
            assert resp.status_code == 404

    def test_listing_does_not_leak(self, client, auth_headers):
        owner, _ = auth_headers("p@example.com")
        intruder, _ = auth_headers("q@example.com")
        _create_page(client, owner)

        assert client.get("/api/v1/status-pages", headers=intruder).get_json()["status_pages"] == []

    def test_preview_works_while_unpublished(self, client, headers):
        page_id = _create_page(client, headers, is_published=False).get_json()["status_page"]["id"]

        resp = client.get(f"/api/v1/status-pages/{page_id}/preview", headers=headers)

        assert resp.status_code == 200
        assert resp.get_json()["page"]["slug"] == "acme-status"

    def test_viewer_cannot_create_a_page(self, client, auth_headers):
        admin, _ = auth_headers()
        client.post(
            "/api/v1/auth/users",
            headers=admin,
            json={"email": "v2@example.com", "password": "password123", "role": "Viewer"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"email": "v2@example.com", "password": "password123"}
        ).get_json()["access_token"]
        viewer = {"Authorization": f"Bearer {token}"}

        assert _create_page(client, viewer).status_code == 403
