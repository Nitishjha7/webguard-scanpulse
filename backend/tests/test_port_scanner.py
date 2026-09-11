"""Exposed-port scanner.

The socket layer is stubbed: these tests are about which ports we look at, how
we score what we find, and — most importantly — that the scanner cannot be
aimed at internal infrastructure.
"""
import asyncio
from unittest.mock import patch

import pytest

from app.engines import port_scanner
from app.engines.port_scanner import WATCHED_PORTS, Severity, scan_ports
from app.utils.ssrf import SSRFError

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def safe_resolution():
    with patch(
        "app.engines.port_scanner.resolve_safe_addresses", return_value=[PUBLIC_IP]
    ):
        yield


def _open_only(*open_ports: int):
    """Patch the scan so exactly ``open_ports`` answer."""

    async def _fake_scan(host, ports):
        return sorted(p for p in ports if p in open_ports)

    return patch.object(port_scanner, "_scan", _fake_scan)


class TestSsrfProtection:
    def test_private_host_is_refused_before_any_socket(self):
        with patch(
            "app.engines.port_scanner.resolve_safe_addresses",
            side_effect=SSRFError("10.0.0.5 resolves to non-public address 10.0.0.5"),
        ):
            result = scan_ports("internal.corp")

        assert result["ok"] is False
        assert "blocked" in result["error"]
        assert result["open_ports"] == []

    def test_scan_connects_to_the_resolved_address_not_the_hostname(self, safe_resolution):
        """Connecting to the checked IP closes the rebind window between the
        SSRF check and the connect."""
        seen = {}

        async def _record(host, ports):
            seen["host"] = host
            return []

        with patch.object(port_scanner, "_scan", _record):
            scan_ports("example.com")

        assert seen["host"] == PUBLIC_IP


class TestScoring:
    def test_nothing_open_scores_full_marks(self, safe_resolution):
        with _open_only():
            result = scan_ports("example.com")

        assert result["ok"] is True
        assert result["score"] == 100
        assert result["grade"] == "A+"
        assert result["findings"] == []

    def test_web_ports_alone_do_not_cost_anything(self, safe_resolution):
        """80 and 443 open on a web host is the expected state."""
        with _open_only(80, 443):
            result = scan_ports("example.com")

        assert result["score"] == 100
        assert result["grade"] == "A+"
        assert [f["severity"] for f in result["findings"]] == [Severity.INFO, Severity.INFO]

    def test_exposed_redis_is_critical(self, safe_resolution):
        with _open_only(443, 6379):
            result = scan_ports("example.com")

        assert result["critical_count"] == 1
        assert result["score"] == 60
        finding = next(f for f in result["findings"] if f["port"] == 6379)
        assert finding["service"] == "Redis"
        assert "no authentication" in finding["note"]

    def test_multiple_critical_services_compound(self, safe_resolution):
        with _open_only(6379, 27017, 3306):
            result = scan_ports("example.com")

        assert result["critical_count"] == 3
        assert result["score"] == 0
        assert result["grade"] == "F"

    def test_score_never_goes_negative(self, safe_resolution):
        with _open_only(*[p for p, (_, sev, _) in WATCHED_PORTS.items() if sev == Severity.CRITICAL]):
            result = scan_ports("example.com")

        assert result["score"] == 0

    def test_ssh_is_medium_not_critical(self, safe_resolution):
        """SSH on a public host is normal; flagging it as critical would train
        users to ignore the report."""
        with _open_only(22, 443):
            result = scan_ports("example.com")

        assert result["critical_count"] == 0
        assert result["score"] == 95
        assert result["grade"] == "A+"

    def test_ftp_is_high(self, safe_resolution):
        with _open_only(21):
            result = scan_ports("example.com")

        assert result["score"] == 80
        assert next(f for f in result["findings"] if f["port"] == 21)["severity"] == Severity.HIGH


class TestPortSelection:
    def test_default_scan_covers_every_watched_port(self, safe_resolution):
        seen = {}

        async def _record(host, ports):
            seen["ports"] = ports
            return []

        with patch.object(port_scanner, "_scan", _record):
            result = scan_ports("example.com")

        assert set(seen["ports"]) == set(WATCHED_PORTS)
        assert result["ports_scanned"] == len(WATCHED_PORTS)

    def test_explicit_port_list_is_honoured(self, safe_resolution):
        with _open_only(6379):
            result = scan_ports("example.com", ports=[80, 443])

        # 6379 is open but was not asked for, so it must not appear.
        assert result["open_ports"] == []
        assert result["ports_scanned"] == 2

    def test_every_watched_port_has_a_severity_and_a_reason(self):
        for port, (service, severity, note) in WATCHED_PORTS.items():
            assert service, port
            assert severity in vars(Severity).values(), port
            assert note, port


class TestProbeBehaviour:
    def test_refused_connection_counts_as_closed(self):
        async def _run():
            semaphore = asyncio.Semaphore(1)
            with patch(
                "asyncio.open_connection", side_effect=ConnectionRefusedError("refused")
            ):
                return await port_scanner._probe_port(PUBLIC_IP, 6379, semaphore)

        assert asyncio.run(_run()) == (6379, False)

    def test_timeout_counts_as_closed(self):
        async def _run():
            semaphore = asyncio.Semaphore(1)

            async def _hang(*args, **kwargs):
                await asyncio.sleep(10)

            with patch("asyncio.open_connection", _hang), patch.object(
                port_scanner, "CONNECT_TIMEOUT", 0.05
            ):
                return await port_scanner._probe_port(PUBLIC_IP, 6379, semaphore)

        assert asyncio.run(_run()) == (6379, False)

    def test_os_error_does_not_escape(self):
        async def _run():
            semaphore = asyncio.Semaphore(1)
            with patch("asyncio.open_connection", side_effect=OSError("network unreachable")):
                return await port_scanner._probe_port(PUBLIC_IP, 22, semaphore)

        assert asyncio.run(_run()) == (22, False)


class TestFailedScanReporting:
    def test_a_blocked_scan_reports_no_score_at_all(self):
        """A failed scan must not read as 'nothing exposed'."""
        with patch(
            "app.engines.port_scanner.resolve_safe_addresses",
            side_effect=SSRFError("blocked"),
        ):
            result = scan_ports("internal.corp")

        assert result["score"] is None
        assert result["grade"] is None

    def test_socket_layer_failure_is_reported_not_raised(self, safe_resolution):
        async def _explode(host, ports):
            raise OSError("network unreachable")

        with patch.object(port_scanner, "_scan", _explode):
            result = scan_ports("example.com")

        assert result["ok"] is False
        assert "OSError" in result["error"]
        assert result["score"] is None
