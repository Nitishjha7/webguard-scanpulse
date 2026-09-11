"""Beat dispatch and SSL expiry alerting.

Both are time-dependent logic where an off-by-one is invisible in production
until it either floods a queue or never fires at all.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models import SslScan
from app.tasks.probes import _raise_ssl_alerts
from app.tasks.scheduler import dispatch_due_pings, dispatch_due_security_scans


def _ago(**kwargs) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


class TestPingDispatch:
    def test_never_checked_monitor_is_due(self, db, make_org, make_monitor):
        make_monitor(make_org(), interval_seconds=300)

        with patch("app.tasks.probes.probe_monitor.delay") as delay:
            assert dispatch_due_pings()["dispatched"] == 1
        assert delay.call_count == 1

    def test_monitor_inside_its_interval_is_not_due(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), interval_seconds=300)
        monitor.last_checked_at = _ago(seconds=60)
        db.session.commit()

        with patch("app.tasks.probes.probe_monitor.delay"):
            assert dispatch_due_pings()["dispatched"] == 0

    def test_monitor_past_its_interval_is_due(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), interval_seconds=300)
        monitor.last_checked_at = _ago(seconds=301)
        db.session.commit()

        with patch("app.tasks.probes.probe_monitor.delay"):
            assert dispatch_due_pings()["dispatched"] == 1

    def test_each_monitor_uses_its_own_interval(self, db, make_org, make_monitor):
        """One SQL pass, per-row deadlines — not a single global cutoff."""
        org = make_org()
        fast = make_monitor(org, interval_seconds=60)
        slow = make_monitor(org, interval_seconds=3600)
        for monitor in (fast, slow):
            monitor.last_checked_at = _ago(seconds=120)
        db.session.commit()

        with patch("app.tasks.probes.probe_monitor.delay") as delay:
            assert dispatch_due_pings()["dispatched"] == 1
        assert delay.call_args[0][0] == str(fast.id)

    def test_inactive_monitors_are_never_dispatched(self, db, make_org, make_monitor):
        make_monitor(make_org(), interval_seconds=60, is_active=False)

        with patch("app.tasks.probes.probe_monitor.delay"):
            assert dispatch_due_pings()["dispatched"] == 0

    def test_dispatch_is_capped_per_tick(self, db, make_org, make_monitor):
        """A large tenant must not be able to starve the queue in one tick."""
        from app.tasks import scheduler

        org = make_org()
        for _ in range(5):
            make_monitor(org, interval_seconds=60)

        with patch.object(scheduler, "MAX_DISPATCH_PER_TICK", 3):
            with patch("app.tasks.probes.probe_monitor.delay"):
                assert dispatch_due_pings()["dispatched"] == 3

    def test_oldest_monitors_go_first(self, db, make_org, make_monitor):
        from app.tasks import scheduler

        org = make_org()
        recent = make_monitor(org, interval_seconds=60)
        stale = make_monitor(org, interval_seconds=60)
        recent.last_checked_at = _ago(seconds=120)
        stale.last_checked_at = _ago(hours=5)
        db.session.commit()

        with patch.object(scheduler, "MAX_DISPATCH_PER_TICK", 1):
            with patch("app.tasks.probes.probe_monitor.delay") as delay:
                dispatch_due_pings()
        assert delay.call_args[0][0] == str(stale.id)


class TestSecurityScanDispatch:
    def test_freshly_scanned_monitor_is_skipped(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        monitor.last_scanned_at = _ago(hours=1)
        db.session.commit()

        with patch("app.tasks.probes.scan_monitor_ssl.delay"), patch(
            "app.tasks.probes.scan_monitor_security.delay"
        ):
            assert dispatch_due_security_scans()["dispatched"] == 0

    def test_day_old_scan_is_due_and_queues_both_scans(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        monitor.last_scanned_at = _ago(hours=25)
        db.session.commit()

        with patch("app.tasks.probes.scan_monitor_ssl.delay") as ssl_delay, patch(
            "app.tasks.probes.scan_monitor_security.delay"
        ) as sec_delay:
            assert dispatch_due_security_scans()["dispatched"] == 1

        assert ssl_delay.call_count == 1
        assert sec_delay.call_count == 1


@pytest.fixture
def make_scan(db):
    def _make(monitor, days_left, **kwargs):
        scan = SslScan(
            monitor_id=monitor.id,
            org_id=monitor.org_id,
            days_left=days_left,
            valid_to=datetime.now(timezone.utc) + timedelta(days=days_left or 0),
            is_valid=kwargs.pop("is_valid", True),
            **kwargs,
        )
        db.session.add(scan)
        db.session.commit()
        return scan

    return _make


class TestSslExpiryThresholds:
    """Thresholds default to 30, 14, 7, 3, 1 days."""

    def test_crossing_a_mark_alerts_once(self, app, db, make_org, make_monitor, make_scan):
        monitor = make_monitor(make_org())
        previous = make_scan(monitor, days_left=31)
        current = make_scan(monitor, days_left=29)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, previous)

        assert notify.call_count == 1
        assert notify.call_args[0][1] == "ssl.expiring"

    def test_staying_between_marks_does_not_re_alert(
        self, app, db, make_org, make_monitor, make_scan
    ):
        """Regression risk: crossing 30 must not then alert daily until 14."""
        monitor = make_monitor(make_org())
        previous = make_scan(monitor, days_left=29)
        current = make_scan(monitor, days_left=28)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, previous)

        assert notify.call_count == 0

    def test_a_healthy_certificate_never_alerts(
        self, app, db, make_org, make_monitor, make_scan
    ):
        monitor = make_monitor(make_org())
        previous = make_scan(monitor, days_left=90)
        current = make_scan(monitor, days_left=89)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, previous)

        assert notify.call_count == 0

    def test_first_ever_scan_of_a_soon_expiring_cert_alerts(
        self, app, db, make_org, make_monitor, make_scan
    ):
        """A monitor added with a cert expiring in 5 days must alert immediately."""
        monitor = make_monitor(make_org())
        current = make_scan(monitor, days_left=5)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, None)

        assert notify.call_count == 1

    def test_first_ever_scan_of_a_healthy_cert_stays_quiet(
        self, app, db, make_org, make_monitor, make_scan
    ):
        monitor = make_monitor(make_org())
        current = make_scan(monitor, days_left=200)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, None)

        assert notify.call_count == 0

    def test_skipping_several_marks_still_alerts_once(
        self, app, db, make_org, make_monitor, make_scan
    ):
        """A gap in scanning must not swallow the alert entirely."""
        monitor = make_monitor(make_org())
        previous = make_scan(monitor, days_left=31)
        current = make_scan(monitor, days_left=2)

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(current, previous)

        assert notify.call_count == 1


class TestSslInvalidAlerts:
    def test_verify_error_raises_an_invalid_alert_not_a_countdown(
        self, app, db, make_org, make_monitor, make_scan
    ):
        monitor = make_monitor(make_org())
        scan = make_scan(
            monitor, days_left=-10, is_valid=False, verify_error="certificate has expired"
        )

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(scan, None)

        assert notify.call_count == 1
        assert notify.call_args[0][1] == "ssl.invalid"

    def test_unreachable_host_raises_no_ssl_alert(
        self, app, db, make_org, make_monitor, make_scan
    ):
        """The uptime probe owns 'cannot connect' — an SSL alert would be noise."""
        monitor = make_monitor(make_org())
        scan = make_scan(monitor, days_left=None, is_valid=False, error="ConnectionRefused")

        with patch("app.tasks.probes.notify_ssl.delay") as notify:
            _raise_ssl_alerts(scan, None)

        assert notify.call_count == 0
