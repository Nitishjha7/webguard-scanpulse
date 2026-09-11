"""HTTP probe and alert message formatting.

``probe_http`` is the highest-frequency code path in the system; the formatters
run on every alert that leaves it.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import requests
import responses

from app.engines.http_probe import probe_http
from app.models import AlertEvent, IncidentStatus
from app.notifications.formatters import _humanize_duration, incident_message, ssl_message

TARGET = "https://target.example.com/"


@pytest.fixture
def public_dns():
    """Resolve every hostname to a public address.

    ``responses`` intercepts HTTP but not DNS, and the SSRF guard resolves the
    target before connecting — without this the probe tests would depend on a
    working resolver and on these example hostnames actually existing.
    """
    with patch(
        "socket.getaddrinfo",
        lambda host, port, *a, **kw: [(2, 1, 6, "", ("93.184.216.34", port))],
    ):
        yield


class TestProbeStatusClassification:
    @responses.activate
    def test_2xx_is_up(self, public_dns):
        responses.add(responses.GET, TARGET, status=200)

        result = probe_http(TARGET)

        assert result["is_up"] is True
        assert result["status_code"] == 200
        assert result["error"] is None
        assert result["latency_ms"] >= 0

    @responses.activate
    def test_3xx_that_lands_on_200_is_up(self, public_dns):
        responses.add(responses.GET, TARGET, status=302, headers={"Location": "https://final.example.com/"})
        responses.add(responses.GET, "https://final.example.com/", status=200)

        result = probe_http(TARGET)

        assert result["is_up"] is True
        assert result["final_url"] == "https://final.example.com/"

    @responses.activate
    def test_500_is_down(self, public_dns):
        responses.add(responses.GET, TARGET, status=500)

        result = probe_http(TARGET)

        assert result["is_up"] is False
        assert result["error"] == "HTTP 500"

    @responses.activate
    def test_401_is_down(self, public_dns):
        """A monitored endpoint that stops serving its content is an incident,
        whichever error code it picks."""
        responses.add(responses.GET, TARGET, status=401)

        assert probe_http(TARGET)["is_up"] is False


class TestProbeFailureModes:
    @responses.activate
    def test_timeout_is_recorded_not_raised(self, public_dns):
        responses.add(responses.GET, TARGET, body=requests.Timeout("too slow"))

        result = probe_http(TARGET, timeout=3)

        assert result["is_up"] is False
        assert "timeout after 3s" in result["error"]
        assert result["latency_ms"] is not None

    @responses.activate
    def test_connection_error_is_recorded_not_raised(self, public_dns):
        responses.add(responses.GET, TARGET, body=requests.ConnectionError("refused"))

        result = probe_http(TARGET)

        assert result["is_up"] is False
        assert "ConnectionError" in result["error"]

    def test_private_target_is_blocked_before_the_request(self):
        result = probe_http("http://10.0.0.1/")

        assert result["is_up"] is False
        assert "blocked" in result["error"]
        assert result["status_code"] is None

    @responses.activate
    def test_region_is_carried_through(self, public_dns):
        responses.add(responses.GET, TARGET, status=200)

        assert probe_http(TARGET, region="eu-west")["region"] == "eu-west"


class TestDurationFormatting:
    def test_shapes(self):
        assert _humanize_duration(None) == "ongoing"
        assert _humanize_duration(45) == "45s"
        assert _humanize_duration(90) == "1m 30s"
        assert _humanize_duration(7320) == "2h 2m"


class _FakeIncident:
    def __init__(self, status, duration=None, resolved=False):
        self.status = status
        self.started_at = datetime.now(timezone.utc) - timedelta(seconds=duration or 0)
        self.resolved_at = self.started_at + timedelta(seconds=duration or 0) if resolved else None
        self.root_cause = "Connection refused"
        self.failure_count = 3

    @property
    def duration_seconds(self):
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.started_at).total_seconds()


class _FakeMonitor:
    name = "Acme Checkout"
    url = "https://acme.example.com"


class TestIncidentFormatting:
    def test_opened_message_states_the_status_and_cause(self):
        message = incident_message(
            _FakeIncident(IncidentStatus.DOWN), _FakeMonitor(), AlertEvent.INCIDENT_OPENED
        )

        assert message["severity"] == "DOWN"
        assert "Acme Checkout is DOWN" == message["title"]
        assert message["summary"] == "Connection refused"
        assert dict(message["fields"])["Failed probes"] == "3"

    def test_resolved_message_reports_the_outage_length(self):
        incident = _FakeIncident(IncidentStatus.RESOLVED, duration=300, resolved=True)

        message = incident_message(incident, _FakeMonitor(), AlertEvent.INCIDENT_RESOLVED)

        assert message["severity"] == "RESOLVED"
        assert "recovered" in message["title"]
        assert "5m 0s" in message["summary"]

    def test_degraded_keeps_its_own_severity(self):
        message = incident_message(
            _FakeIncident(IncidentStatus.DEGRADED), _FakeMonitor(), AlertEvent.INCIDENT_OPENED
        )

        assert message["severity"] == "DEGRADED"


class _FakeScan:
    def __init__(self, days_left, verify_error=None):
        self.days_left = days_left
        self.valid_to = datetime.now(timezone.utc) + timedelta(days=days_left)
        self.issuer = "CN=Test CA"
        self.tls_version = "TLSv1.3"
        self.verify_error = verify_error
        self.error = None


class TestSslFormatting:
    def test_expiring_message_is_a_warning_with_a_countdown(self):
        message = ssl_message(_FakeMonitor(), _FakeScan(7), AlertEvent.SSL_EXPIRING)

        assert message["severity"] == "WARNING"
        assert "expires in 7 days" in message["title"]
        assert dict(message["fields"])["Days left"] == "7"

    def test_invalid_message_carries_the_verification_failure(self):
        scan = _FakeScan(-3, verify_error="certificate has expired")

        message = ssl_message(_FakeMonitor(), scan, AlertEvent.SSL_INVALID)

        assert message["severity"] == "DOWN"
        assert message["summary"] == "certificate has expired"
