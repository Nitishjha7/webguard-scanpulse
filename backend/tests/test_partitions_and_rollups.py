"""Partition management and downsampling.

These run against the real partitioned `ping_logs`, which is the only way to
test any of it — declarative partitioning has no SQLite equivalent.
"""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import PingLog, PingRollupDaily, PingRollupHourly
from app.services import partitions, rollups


def _at(hours_ago: float = 0, days_ago: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago, days=days_ago)


@pytest.fixture
def write_pings(db):
    def _write(monitor, samples):
        """samples: list of (checked_at, is_up, latency_ms)."""
        for checked_at, is_up, latency in samples:
            db.session.add(
                PingLog(
                    monitor_id=monitor.id,
                    org_id=monitor.org_id,
                    is_up=is_up,
                    latency_ms=latency,
                    status_code=200 if is_up else 500,
                    checked_at=checked_at,
                )
            )
        db.session.commit()

    return _write


class TestPartitioning:
    def test_ping_logs_is_a_partitioned_parent(self, db):
        assert partitions.is_partitioned() is True

    def test_current_and_future_months_exist(self, db):
        names = partitions.existing_partitions()
        month = datetime.now(timezone.utc).date().replace(day=1)

        for _ in range(partitions.MONTHS_AHEAD + 1):
            assert partitions.partition_name(month) in names
            month = partitions._next_month(month)

    def test_rows_route_to_the_partition_for_their_month(self, db, make_org, make_monitor, write_pings):
        monitor = make_monitor(make_org())
        now = datetime.now(timezone.utc)
        write_pings(monitor, [(now, True, 100.0)])

        landed = db.session.execute(
            sa.text(
                "SELECT tableoid::regclass::text FROM ping_logs "
                "WHERE monitor_id = :mid ORDER BY checked_at DESC LIMIT 1"
            ),
            {"mid": monitor.id},
        ).scalar()

        assert landed == partitions.partition_name(now.date())

    def test_ensure_partitions_is_idempotent(self, db):
        before = set(partitions.existing_partitions())

        partitions.ensure_partitions()
        db.session.commit()

        assert set(partitions.existing_partitions()) == before

    def test_a_row_far_in_the_past_still_lands_somewhere(
        self, db, make_org, make_monitor, write_pings
    ):
        """The default partition is a safety net: losing a probe is worse than
        an untidy table."""
        monitor = make_monitor(make_org())
        write_pings(monitor, [(_at(days_ago=900), True, 50.0)])

        assert db.session.query(PingLog).filter(PingLog.monitor_id == monitor.id).count() == 1
        assert partitions.default_partition_rows() >= 1

    def test_dropping_old_partitions_leaves_recent_ones(self, db):
        """Retention is a DROP TABLE, not a DELETE walking millions of rows."""
        future = datetime.now(timezone.utc).date().replace(day=1)
        dropped = partitions.drop_partitions_before(future.replace(year=future.year - 5))

        assert dropped == []
        assert partitions.partition_name(future) in partitions.existing_partitions()


class TestHourlyRollup:
    def test_probes_condense_into_one_bucket_per_hour(
        self, db, make_org, make_monitor, write_pings
    ):
        monitor = make_monitor(make_org())
        base = _at(hours_ago=1).replace(minute=0, second=0, microsecond=0)
        write_pings(
            monitor,
            [
                (base + timedelta(minutes=5), True, 100.0),
                (base + timedelta(minutes=20), True, 200.0),
                (base + timedelta(minutes=40), False, None),
                (base + timedelta(minutes=55), True, 300.0),
            ],
        )

        written = rollups.refresh_hourly(since=base, until=base + timedelta(hours=1))

        assert written == 1
        bucket = db.session.query(PingRollupHourly).one()
        assert bucket.checks == 4
        assert bucket.up_checks == 3
        assert bucket.uptime_percent == 75.0
        assert bucket.avg_latency_ms == 200.0
        assert bucket.min_latency_ms == 100.0
        assert bucket.max_latency_ms == 300.0

    def test_percentiles_are_stored_not_just_the_mean(
        self, db, make_org, make_monitor, write_pings
    ):
        """An average hides the slow tail, and it cannot be recovered later
        once the raw rows are gone."""
        monitor = make_monitor(make_org())
        base = _at(hours_ago=1).replace(minute=0, second=0, microsecond=0)
        write_pings(
            monitor,
            [(base + timedelta(minutes=i), True, float(10 * (i + 1))) for i in range(20)],
        )

        rollups.refresh_hourly(since=base, until=base + timedelta(hours=1))

        bucket = db.session.query(PingRollupHourly).one()
        assert bucket.p95_latency_ms > bucket.avg_latency_ms
        assert bucket.p99_latency_ms >= bucket.p95_latency_ms

    def test_refresh_is_idempotent(self, db, make_org, make_monitor, write_pings):
        """Re-running over a processed window must not double-count — it is the
        property that makes the maintenance task safe to retry."""
        monitor = make_monitor(make_org())
        base = _at(hours_ago=1).replace(minute=0, second=0, microsecond=0)
        write_pings(monitor, [(base + timedelta(minutes=5), True, 100.0)])
        window = {"since": base, "until": base + timedelta(hours=1)}

        rollups.refresh_hourly(**window)
        rollups.refresh_hourly(**window)
        rollups.refresh_hourly(**window)

        assert db.session.query(PingRollupHourly).count() == 1
        assert db.session.query(PingRollupHourly).one().checks == 1

    def test_a_late_probe_updates_the_existing_bucket(
        self, db, make_org, make_monitor, write_pings
    ):
        monitor = make_monitor(make_org())
        base = _at(hours_ago=1).replace(minute=0, second=0, microsecond=0)
        window = {"since": base, "until": base + timedelta(hours=1)}

        write_pings(monitor, [(base + timedelta(minutes=5), True, 100.0)])
        rollups.refresh_hourly(**window)

        write_pings(monitor, [(base + timedelta(minutes=50), False, None)])
        rollups.refresh_hourly(**window)

        bucket = db.session.query(PingRollupHourly).one()
        assert bucket.checks == 2
        assert bucket.uptime_percent == 50.0

    def test_monitors_get_separate_buckets(self, db, make_org, make_monitor, write_pings):
        org = make_org()
        first, second = make_monitor(org), make_monitor(org)
        base = _at(hours_ago=1).replace(minute=0, second=0, microsecond=0)
        write_pings(first, [(base + timedelta(minutes=5), True, 100.0)])
        write_pings(second, [(base + timedelta(minutes=5), False, None)])

        rollups.refresh_hourly(since=base, until=base + timedelta(hours=1))

        assert db.session.query(PingRollupHourly).count() == 2

    def test_an_empty_window_writes_nothing(self, db, make_org, make_monitor):
        make_monitor(make_org())

        assert rollups.refresh_hourly(since=_at(hours_ago=2), until=_at(hours_ago=1)) == 0


class TestDailyRollup:
    def _seed_hours(self, db, monitor, day, hours):
        """hours: list of (checks, up_checks, avg_latency)."""
        for index, (checks, up, latency) in enumerate(hours):
            db.session.add(
                PingRollupHourly(
                    monitor_id=monitor.id,
                    org_id=monitor.org_id,
                    bucket=day + timedelta(hours=index),
                    checks=checks,
                    up_checks=up,
                    uptime_percent=round(up / checks * 100, 4) if checks else 0.0,
                    avg_latency_ms=latency,
                    min_latency_ms=latency,
                    max_latency_ms=latency,
                    p95_latency_ms=latency,
                    p99_latency_ms=latency,
                )
            )
        db.session.commit()

    def test_hours_condense_into_one_day(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        day = _at(days_ago=1).replace(hour=0, minute=0, second=0, microsecond=0)
        self._seed_hours(db, monitor, day, [(60, 60, 100.0), (60, 30, 200.0)])

        written = rollups.refresh_daily(since=day, until=day + timedelta(days=1))

        assert written == 1
        bucket = db.session.query(PingRollupDaily).one()
        assert bucket.checks == 120
        assert bucket.up_checks == 90
        assert bucket.uptime_percent == 75.0

    def test_latency_is_weighted_by_check_count(self, db, make_org, make_monitor):
        """A quiet hour must not count as much as a busy one."""
        monitor = make_monitor(make_org())
        day = _at(days_ago=1).replace(hour=0, minute=0, second=0, microsecond=0)
        # 90 checks at 100ms, 10 at 1000ms -> weighted mean 190, not 550.
        self._seed_hours(db, monitor, day, [(90, 90, 100.0), (10, 10, 1000.0)])

        rollups.refresh_daily(since=day, until=day + timedelta(days=1))

        assert db.session.query(PingRollupDaily).one().avg_latency_ms == 190.0

    def test_daily_p95_is_the_worst_hour(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        day = _at(days_ago=1).replace(hour=0, minute=0, second=0, microsecond=0)
        self._seed_hours(db, monitor, day, [(60, 60, 100.0), (60, 60, 900.0)])

        rollups.refresh_daily(since=day, until=day + timedelta(days=1))

        assert db.session.query(PingRollupDaily).one().p95_latency_ms == 900.0

    def test_daily_refresh_is_idempotent(self, db, make_org, make_monitor):
        monitor = make_monitor(make_org())
        day = _at(days_ago=1).replace(hour=0, minute=0, second=0, microsecond=0)
        self._seed_hours(db, monitor, day, [(60, 60, 100.0)])
        window = {"since": day, "until": day + timedelta(days=1)}

        rollups.refresh_daily(**window)
        rollups.refresh_daily(**window)

        assert db.session.query(PingRollupDaily).count() == 1
        assert db.session.query(PingRollupDaily).one().checks == 60


class TestRetention:
    def test_hourly_buckets_older_than_the_window_are_pruned(
        self, db, make_org, make_monitor
    ):
        monitor = make_monitor(make_org())
        for days_ago in (1, 30, 120, 200):
            db.session.add(
                PingRollupHourly(
                    monitor_id=monitor.id,
                    org_id=monitor.org_id,
                    bucket=_at(days_ago=days_ago),
                    checks=60,
                    up_checks=60,
                    uptime_percent=100.0,
                )
            )
        db.session.commit()

        deleted = rollups.prune_hourly_rollups(retention_days=90)

        assert deleted == 2
        assert db.session.query(PingRollupHourly).count() == 2

    def test_backfill_covers_the_raw_window(self, db, make_org, make_monitor, write_pings):
        monitor = make_monitor(make_org())
        write_pings(
            monitor,
            [
                (_at(days_ago=1), True, 100.0),
                (_at(days_ago=3), True, 150.0),
                (_at(days_ago=6), False, None),
            ],
        )

        written = rollups.backfill_hourly(days=7)

        assert written == 3
        assert db.session.query(PingRollupHourly).count() == 3

    def test_retention_condenses_before_it_deletes(self, app, db, make_org, make_monitor, write_pings):
        """Ordering is load-bearing: history lost to a retention job is gone."""
        from app.tasks.maintenance import run_retention

        monitor = make_monitor(make_org())
        write_pings(monitor, [(_at(days_ago=2), True, 120.0)])

        result = run_retention()

        assert result["hourly_backfilled"] >= 1
        assert db.session.query(PingRollupHourly).count() >= 1
