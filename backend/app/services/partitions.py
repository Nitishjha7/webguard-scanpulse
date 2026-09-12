"""Monthly range partitions for ``ping_logs``.

``ping_logs`` is the only table in the system that grows without bound — one
row per monitor per interval, forever. Partitioning by month means retention is
a ``DROP TABLE`` rather than a ``DELETE`` that has to walk and vacuum hundreds
of millions of rows, and dashboard queries over "the last 24 hours" touch one
partition instead of the whole history.

Partitions are created *ahead* of time by a daily beat task, never lazily on
insert: a probe that fails because tomorrow's partition does not exist yet
would look like a site outage.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from app.extensions import db

logger = logging.getLogger(__name__)

PARENT_TABLE = "ping_logs"

#: Safety net for any row whose timestamp falls outside every real partition.
#: Losing a probe is worse than an untidy table.
DEFAULT_PARTITION = f"{PARENT_TABLE}_default"

#: How far ahead to keep partitions ready. Three months of headroom means the
#: maintenance task can miss a run — or a few — without inserts ever failing.
MONTHS_AHEAD = 3

_PARTITION_NAME = re.compile(rf"^{PARENT_TABLE}_(\d{{4}})_(\d{{2}})$")


def _month_start(when: date) -> date:
    return when.replace(day=1)


def _next_month(when: date) -> date:
    return (when.replace(day=1) + timedelta(days=32)).replace(day=1)


def partition_name(when: date) -> str:
    return f"{PARENT_TABLE}_{when.year:04d}_{when.month:02d}"


def is_partitioned() -> bool:
    """True when ``ping_logs`` is a partitioned parent rather than a plain table."""
    return bool(
        db.session.execute(
            sa.text(
                "SELECT 1 FROM pg_class WHERE relname = :name AND relkind = 'p'"
            ),
            {"name": PARENT_TABLE},
        ).scalar()
    )


def existing_partitions() -> list[str]:
    rows = db.session.execute(
        sa.text(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = :parent
            ORDER BY c.relname
            """
        ),
        {"parent": PARENT_TABLE},
    )
    return [row[0] for row in rows]


def create_partition(month: date, *, connection=None) -> str | None:
    """Create the partition covering ``month``. Returns its name, or None if present.

    ``IF NOT EXISTS`` plus advisory-lock-free idempotency: two workers racing
    here both succeed, which matters because this runs from beat *and* from the
    migration.
    """
    executor = connection if connection is not None else db.session
    start = _month_start(month)
    end = _next_month(start)
    name = partition_name(start)

    executor.execute(
        sa.text(
            f'CREATE TABLE IF NOT EXISTS "{name}" PARTITION OF "{PARENT_TABLE}" '
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )
    )
    return name


def ensure_partitions(months_ahead: int = MONTHS_AHEAD, *, connection=None) -> list[str]:
    """Make sure this month and the next ``months_ahead`` months exist."""
    created = []
    month = _month_start(datetime.now(timezone.utc).date())
    for _ in range(months_ahead + 1):
        created.append(create_partition(month, connection=connection))
        month = _next_month(month)
    return created


def ensure_default_partition(*, connection=None) -> None:
    executor = connection if connection is not None else db.session
    executor.execute(
        sa.text(
            f'CREATE TABLE IF NOT EXISTS "{DEFAULT_PARTITION}" '
            f'PARTITION OF "{PARENT_TABLE}" DEFAULT'
        )
    )


def default_partition_rows() -> int:
    """Rows that landed in the catch-all partition.

    Any row here is a problem worth surfacing: Postgres refuses to create a
    partition whose range overlaps rows already sitting in the default, so a
    non-zero count means future partition creation will start failing.
    """
    if DEFAULT_PARTITION not in existing_partitions():
        return 0
    return db.session.execute(
        sa.text(f'SELECT count(*) FROM "{DEFAULT_PARTITION}"')
    ).scalar()


def drop_partitions_before(cutoff: date) -> list[str]:
    """Drop whole partitions older than ``cutoff``.

    This is the point of partitioning: retention becomes a metadata operation
    instead of a bulk delete plus vacuum.
    """
    dropped = []
    boundary = _month_start(cutoff)

    for name in existing_partitions():
        match = _PARTITION_NAME.match(name)
        if not match:
            continue  # the default partition, or something a human added
        month = date(int(match.group(1)), int(match.group(2)), 1)
        if month < boundary:
            db.session.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))
            dropped.append(name)
            logger.info("dropped ping_logs partition %s", name)

    if dropped:
        db.session.commit()
    return dropped
