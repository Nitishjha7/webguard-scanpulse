"""Synthetic check execution and scheduling.

Browser runs are slow and memory-hungry compared with an HTTP probe, so they
get their own queue: a backlog of journeys must never delay uptime checks.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from flask import current_app

from app.extensions import db
from app.models import AlertEvent, RunStatus, SyntheticCheck, SyntheticRun
from app.notifications import broadcast

logger = logging.getLogger(__name__)

MAX_DISPATCH_PER_TICK = 100

#: Keep enough history to see a pattern without letting screenshots grow forever.
RUN_RETENTION_DAYS = 30


def run_check(check_id: str) -> dict:
    """Execute one synthetic check and record the run."""
    from app.engines.synthetic import run_journey

    check = db.session.get(SyntheticCheck, uuid.UUID(str(check_id)))
    if check is None:
        logger.warning("synthetic check %s no longer exists", check_id)
        return {"skipped": "check not found"}

    try:
        outcome = run_journey(
            check.steps,
            timeout_seconds=check.timeout_seconds,
            viewport=(check.viewport_width, check.viewport_height),
            artifacts_dir=current_app.config["ARTIFACTS_DIR"],
        )
    except SoftTimeLimitExceeded:
        now = datetime.now(timezone.utc)
        outcome = {
            "status": "ERROR",
            "started_at": now,
            "finished_at": now,
            "duration_ms": None,
            "failed_step": None,
            "error": "journey exceeded the worker soft time limit",
            "screenshot": None,
            "final_url": None,
            "step_results": [],
        }

    status = RunStatus(outcome["status"])
    run = SyntheticRun(
        check_id=check.id,
        org_id=check.org_id,
        status=status,
        duration_ms=outcome.get("duration_ms"),
        failed_step=outcome.get("failed_step"),
        error=outcome.get("error"),
        step_results=outcome.get("step_results"),
        screenshot=outcome.get("screenshot"),
        final_url=outcome.get("final_url"),
        started_at=outcome["started_at"],
        finished_at=outcome.get("finished_at"),
    )
    db.session.add(run)

    check.last_run_at = datetime.now(timezone.utc)
    transition = _fold_failure_state(check, status)
    db.session.commit()

    if transition:
        notify_synthetic.delay(str(run.id), transition)

    logger.info(
        "synthetic %s -> %s in %sms%s",
        check.name,
        status.value,
        outcome.get("duration_ms"),
        f" (step {outcome['failed_step']})" if outcome.get("failed_step") is not None else "",
    )
    return {"run_id": str(run.id), "status": status.value, "transition": transition}


def _fold_failure_state(check: SyntheticCheck, status: RunStatus) -> str | None:
    """Apply the same anti-flapping rule uptime monitors use.

    A single failed journey is usually a slow page or a one-off timeout. Only a
    run of them means the checkout is actually broken.

    ERROR counts as a failure for alerting but is reported honestly: it means
    the browser could not complete the run, which is still a loss of coverage.
    """
    if status is RunStatus.PASSED:
        was_failing = check.consecutive_failures >= check.failure_threshold
        check.consecutive_failures = 0
        return "recovered" if was_failing else None

    check.consecutive_failures += 1
    if check.consecutive_failures == check.failure_threshold:
        return "failing"
    return None


@shared_task(name="webguard.run_synthetic_check", max_retries=0)
def run_synthetic_check(check_id: str) -> dict:
    return run_check(check_id)


@shared_task(name="webguard.notify_synthetic", max_retries=3, default_retry_delay=60)
def notify_synthetic(run_id: str, transition: str) -> dict:
    """Alert on a journey breaking or recovering."""
    run = db.session.get(SyntheticRun, uuid.UUID(str(run_id)))
    if run is None:
        return {"skipped": "run not found"}

    check = db.session.get(SyntheticCheck, run.check_id)
    if check is None:
        return {"skipped": "check not found"}

    event = (
        AlertEvent.SYNTHETIC_RECOVERED
        if transition == "recovered"
        else AlertEvent.SYNTHETIC_FAILED
    )
    results = broadcast(check.org_id, event, _message(check, run, event))
    return {"event": event.value, "delivered": sum(1 for r in results if r["ok"])}


def _message(check: SyntheticCheck, run: SyntheticRun, event: AlertEvent) -> dict:
    failing = event is AlertEvent.SYNTHETIC_FAILED
    step_label = "—"
    if run.failed_step is not None and run.step_results:
        failed = next(
            (s for s in run.step_results if s.get("index") == run.failed_step), None
        )
        action = failed.get("action") if failed else "?"
        step_label = f"#{run.failed_step} ({action})"

    return {
        "event": event.value,
        "severity": "DOWN" if failing else "RESOLVED",
        "title": (
            f"Journey '{check.name}' is failing" if failing else f"Journey '{check.name}' recovered"
        ),
        "summary": run.error or "All steps passed",
        "url": run.final_url or "",
        "fields": [
            ("Journey", check.name),
            ("Status", run.status.value),
            ("Failed step", step_label),
            ("Duration", f"{run.duration_ms:.0f}ms" if run.duration_ms else "—"),
            ("Consecutive failures", str(check.consecutive_failures)),
            ("Final URL", run.final_url or "—"),
        ],
    }


@shared_task(name="webguard.dispatch_due_synthetic_checks")
def dispatch_due_synthetic_checks() -> dict:
    """Enqueue every check whose interval has elapsed."""
    now = datetime.now(timezone.utc)
    deadline = now - sa.func.make_interval(0, 0, 0, 0, 0, 0, SyntheticCheck.interval_seconds)

    checks = (
        db.session.query(SyntheticCheck)
        .filter(SyntheticCheck.is_active.is_(True))
        .filter(
            sa.or_(
                SyntheticCheck.last_run_at.is_(None),
                SyntheticCheck.last_run_at <= deadline,
            )
        )
        .order_by(sa.nullsfirst(SyntheticCheck.last_run_at.asc()))
        .limit(MAX_DISPATCH_PER_TICK)
        .all()
    )

    for check in checks:
        run_synthetic_check.delay(str(check.id))

    if checks:
        logger.info("dispatched %d synthetic checks", len(checks))
    return {"dispatched": len(checks)}


@shared_task(name="webguard.prune_synthetic_runs")
def prune_synthetic_runs() -> dict:
    """Delete old runs and the screenshots they own.

    Screenshots live on a volume, not in the database, so the rows have to be
    read before deletion — a bulk DELETE would orphan every file.
    """
    import os

    cutoff = datetime.now(timezone.utc) - timedelta(days=RUN_RETENTION_DAYS)
    stale = (
        db.session.query(SyntheticRun).filter(SyntheticRun.started_at < cutoff).limit(1000).all()
    )

    artifacts = current_app.config["ARTIFACTS_DIR"]
    removed_files = 0
    for run in stale:
        if run.screenshot:
            try:
                os.remove(os.path.join(artifacts, run.screenshot))
                removed_files += 1
            except OSError:
                pass  # Already gone, or the volume is read-only; the row still goes.
        db.session.delete(run)

    db.session.commit()
    if stale:
        logger.info("pruned %d synthetic runs (%d screenshots)", len(stale), removed_files)
    return {"pruned": len(stale), "screenshots_removed": removed_files}
