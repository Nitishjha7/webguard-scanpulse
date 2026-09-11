"""Synthetic end-to-end checks: scripted browser journeys and their results.

A check is a list of steps in a small JSON DSL — see
``app.engines.synthetic`` for the step vocabulary and its validator.
"""
import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.scans import JSONColumn


class RunStatus(str, enum.Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"   # A step assertion did not hold — the journey is broken.
    ERROR = "ERROR"     # The run could not complete: browser crash, bad config.


class SyntheticCheck(BaseModel):
    """A scripted user journey, run on its own schedule."""

    __tablename__ = "synthetic_checks"
    __table_args__ = (
        sa.CheckConstraint("interval_seconds >= 60", name="ck_synthetic_min_interval"),
        sa.UniqueConstraint("org_id", "name", name="uq_synthetic_org_name"),
        sa.Index("ix_synthetic_checks_active_schedule", "is_active", "last_run_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Optional link to the monitor this journey exercises, so the dashboard can
    #: show uptime and journey health side by side.
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("monitors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    steps: Mapped[list] = mapped_column(JSONColumn, nullable=False)

    interval_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=900)
    #: Whole-journey budget. A login flow that takes a minute is already broken.
    timeout_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=60)
    viewport_width: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1280)
    viewport_height: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=720)

    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    #: Same anti-flapping idea as uptime monitors: a single failed journey is
    #: usually a slow page, not a broken checkout.
    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    failure_threshold: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=2)

    organization = relationship("Organization", back_populates="synthetic_checks")
    runs = relationship(
        "SyntheticRun",
        back_populates="check",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self, *, include_steps: bool = True) -> dict:
        payload = {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "monitor_id": str(self.monitor_id) if self.monitor_id else None,
            "name": self.name,
            "description": self.description,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "is_active": self.is_active,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "step_count": len(self.steps or []),
            "created_at": self.created_at.isoformat(),
        }
        if include_steps:
            payload["steps"] = redact_steps(self.steps)
        return payload

    def __repr__(self) -> str:
        return f"<SyntheticCheck {self.name}>"


class SyntheticRun(BaseModel):
    """One execution of a check."""

    __tablename__ = "synthetic_runs"
    __table_args__ = (
        sa.Index("ix_synthetic_runs_check_started", "check_id", "started_at"),
        sa.Index("ix_synthetic_runs_org_started", "org_id", "started_at"),
    )

    check_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("synthetic_checks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[RunStatus] = mapped_column(
        sa.Enum(RunStatus, name="run_status"), nullable=False, index=True
    )
    duration_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    #: Zero-based index of the step that broke, so the UI can point straight at it.
    failed_step: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Per-step timings and outcomes.
    step_results: Mapped[list | None] = mapped_column(JSONColumn, nullable=True)
    #: Filename inside the artifacts volume, captured on failure.
    screenshot: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    final_url: Mapped[str | None] = mapped_column(sa.String(2048), nullable=True)

    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    check = relationship("SyntheticCheck", back_populates="runs")

    @property
    def passed(self) -> bool:
        return self.status is RunStatus.PASSED

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "check_id": str(self.check_id),
            "status": self.status.value,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "failed_step": self.failed_step,
            "error": self.error,
            "step_results": self.step_results,
            "has_screenshot": bool(self.screenshot),
            "final_url": self.final_url,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def __repr__(self) -> str:
        return f"<SyntheticRun {self.status.value} check={self.check_id}>"


REDACTED = "********"


def redact_steps(steps: list | None) -> list:
    """Strip step values marked ``secret`` before they leave the API.

    Login journeys carry real passwords. They have to be stored to be replayed,
    but they must never come back out of a GET — an Engineer reading a check
    definition has no reason to see the credential it types.
    """
    redacted = []
    for step in steps or []:
        if isinstance(step, dict) and step.get("secret") and "value" in step:
            step = {**step, "value": REDACTED}
        redacted.append(step)
    return redacted
