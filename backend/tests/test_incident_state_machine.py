"""Incident state machine.

The most safety-critical logic in the system: it decides whether somebody gets
paged at 3am. Two real bugs have already shipped here, and each has a named
regression test below.
"""
import pytest

from app.models import Incident, IncidentStatus
from app.services import incidents as sm
from tests.conftest import probe_down, probe_up


def _evaluate(db, monitor, probe):
    result = sm.evaluate(monitor, probe)
    db.session.commit()
    return result


class TestQuorum:
    """One failed probe is not an outage."""

    def test_single_failure_opens_nothing(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())

        result = _evaluate(db, monitor, probe_down())

        assert result["transition"] is None
        assert result["below_quorum"] is True
        assert monitor.consecutive_failures == 1
        assert db.session.query(Incident).count() == 0

    def test_second_failure_opens_incident(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())

        _evaluate(db, monitor, probe_down())
        result = _evaluate(db, monitor, probe_down("DNS failure"))

        assert result["transition"] == "opened"
        assert result["status"] == "DOWN"

        incident = db.session.query(Incident).one()
        assert incident.status is IncidentStatus.DOWN
        assert incident.is_open
        assert incident.failure_count == 2
        assert incident.root_cause == "DNS failure"

    def test_higher_threshold_is_honoured(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), failure_threshold=4)

        for _ in range(3):
            assert _evaluate(db, monitor, probe_down())["transition"] is None
        assert db.session.query(Incident).count() == 0

        assert _evaluate(db, monitor, probe_down())["transition"] == "opened"

    def test_success_between_failures_resets_the_count(self, db, make_org, make_monitor):
        """A blip, a recovery, then another blip must not add up to an outage."""
        monitor = make_monitor(make_org())

        _evaluate(db, monitor, probe_down())
        _evaluate(db, monitor, probe_up())
        assert monitor.consecutive_failures == 0

        _evaluate(db, monitor, probe_down())

        assert db.session.query(Incident).count() == 0

    def test_further_failures_accumulate_evidence_not_incidents(
        self, db, make_org, make_monitor
    ):
        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())

        for _ in range(3):
            result = _evaluate(db, monitor, probe_down())
            assert result["transition"] is None

        assert db.session.query(Incident).count() == 1
        assert db.session.query(Incident).one().failure_count == 5


class TestRecovery:
    """Opening needs a quorum; closing needs a single success."""

    def test_one_success_resolves(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())

        result = _evaluate(db, monitor, probe_up())

        assert result["transition"] == "resolved"
        incident = db.session.query(Incident).one()
        assert incident.status is IncidentStatus.RESOLVED
        assert not incident.is_open
        assert incident.resolved_at is not None
        assert incident.duration_seconds >= 0

    def test_failing_again_opens_a_second_incident(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())
        _evaluate(db, monitor, probe_up())

        for _ in range(2):
            _evaluate(db, monitor, probe_down())

        assert db.session.query(Incident).count() == 2
        assert db.session.query(Incident).filter(Incident.resolved_at.is_(None)).count() == 1


class TestDegraded:
    def test_slow_responses_open_a_degraded_incident(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), degraded_latency_ms=500)

        assert _evaluate(db, monitor, probe_up(latency_ms=900))["transition"] is None
        result = _evaluate(db, monitor, probe_up(latency_ms=1200))

        assert result["transition"] == "opened"
        assert result["status"] == "DEGRADED"
        assert db.session.query(Incident).one().status is IncidentStatus.DEGRADED

    def test_no_threshold_means_no_degraded_detection(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), degraded_latency_ms=None)

        for _ in range(5):
            _evaluate(db, monitor, probe_up(latency_ms=99_000))

        assert db.session.query(Incident).count() == 0

    def test_fast_response_resolves_degraded(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), degraded_latency_ms=500)
        for _ in range(2):
            _evaluate(db, monitor, probe_up(latency_ms=900))

        result = _evaluate(db, monitor, probe_up(latency_ms=100))

        assert result["transition"] == "resolved"
        assert db.session.query(Incident).one().status is IncidentStatus.RESOLVED

    def test_degraded_escalates_to_down(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org(), degraded_latency_ms=500)
        for _ in range(2):
            _evaluate(db, monitor, probe_up(latency_ms=900))

        _evaluate(db, monitor, probe_down())
        result = _evaluate(db, monitor, probe_down())

        assert result["transition"] == "escalated"
        # Escalation reuses the open incident rather than opening a second one.
        assert db.session.query(Incident).count() == 1
        assert db.session.query(Incident).one().status is IncidentStatus.DOWN


class TestRegressions:
    def test_down_monitor_that_returns_slowly_does_not_stay_down_forever(
        self, db, make_org, make_monitor
    ):
        """Regression: a DOWN monitor that recovers but is slow used to stick.

        Every later slow probe hit the "incident already open" branch and
        returned without changing anything, so the incident could never clear
        while the site was in fact serving traffic.
        """
        monitor = make_monitor(make_org(), degraded_latency_ms=100)
        for _ in range(2):
            _evaluate(db, monitor, probe_down())
        assert db.session.query(Incident).one().status is IncidentStatus.DOWN

        result = _evaluate(db, monitor, probe_up(latency_ms=800))

        assert result["transition"] == "deescalated"
        assert db.session.query(Incident).one().status is IncidentStatus.DEGRADED

        # And once it is fast again the incident actually closes.
        assert _evaluate(db, monitor, probe_up(latency_ms=10))["transition"] == "resolved"
        assert db.session.query(Incident).one().resolved_at is not None

    def test_repeated_slow_probes_do_not_reopen_or_duplicate(
        self, db, make_org, make_monitor
    ):
        monitor = make_monitor(make_org(), degraded_latency_ms=100)
        for _ in range(2):
            _evaluate(db, monitor, probe_down())
        _evaluate(db, monitor, probe_up(latency_ms=800))

        for _ in range(4):
            assert _evaluate(db, monitor, probe_up(latency_ms=800))["transition"] is None

        assert db.session.query(Incident).count() == 1


class TestConcurrencyGuard:
    def test_database_refuses_two_open_incidents_for_one_monitor(
        self, db, make_org, make_monitor
    ):
        """The partial unique index is what makes the race handling safe."""
        from sqlalchemy.exc import IntegrityError

        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())

        duplicate = Incident(
            monitor_id=monitor.id,
            org_id=monitor.org_id,
            status=IncidentStatus.DOWN,
            started_at=db.session.query(Incident).one().started_at,
            root_cause="racing worker",
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_resolved_incidents_do_not_block_a_new_one(self, db, make_org, make_monitor):
        """The index is partial, so closed incidents must not occupy the slot."""
        monitor = make_monitor(make_org())
        for _ in range(3):
            for _ in range(2):
                _evaluate(db, monitor, probe_down())
            _evaluate(db, monitor, probe_up())

        assert db.session.query(Incident).count() == 3


class TestManualResolve:
    def test_manual_resolve_closes_and_clears_counters(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())
        incident = db.session.query(Incident).one()

        sm.resolve_manually(incident, note="rolled back the bad deploy")
        db.session.commit()

        assert incident.status is IncidentStatus.RESOLVED
        assert "rolled back the bad deploy" in incident.root_cause
        assert monitor.consecutive_failures == 0

    def test_manual_resolve_is_idempotent(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        for _ in range(2):
            _evaluate(db, monitor, probe_down())
        incident = db.session.query(Incident).one()

        sm.resolve_manually(incident)
        db.session.commit()
        first_resolved_at = incident.resolved_at

        sm.resolve_manually(incident, note="again")
        db.session.commit()

        assert incident.resolved_at == first_resolved_at
        assert "again" not in (incident.root_cause or "")


class TestRegionTracking:
    def test_regions_are_recorded_without_duplicates(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())

        _evaluate(db, monitor, probe_down(region="eu-west"))
        _evaluate(db, monitor, probe_down(region="us-east"))
        _evaluate(db, monitor, probe_down(region="eu-west"))

        incident = db.session.query(Incident).one()
        assert sorted(incident.regions) == ["eu-west", "us-east"]
