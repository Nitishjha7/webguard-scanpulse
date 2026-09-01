"""Fan one alert out to every channel a tenant has configured for that event."""
import logging
from datetime import datetime, timezone

from app.extensions import db
from app.models import AlertEvent, ChannelType, NotificationChannel
from app.notifications.transports import TRANSPORTS

logger = logging.getLogger(__name__)

#: A channel that keeps failing is disabled rather than retried forever — a dead
#: webhook otherwise burns a worker slot on every single alert.
MAX_CHANNEL_FAILURES = 10


def channels_for(org_id, event: AlertEvent) -> list[NotificationChannel]:
    rows = (
        db.session.query(NotificationChannel)
        .filter(
            NotificationChannel.org_id == org_id,
            NotificationChannel.is_active.is_(True),
        )
        .all()
    )
    return [c for c in rows if c.wants(event)]


def deliver(channel: NotificationChannel, message: dict) -> tuple[bool, str]:
    """Send one message to one channel and record the outcome on the row."""
    transport = TRANSPORTS.get(
        channel.type.value if isinstance(channel.type, ChannelType) else str(channel.type)
    )
    if transport is None:
        return False, f"No transport for channel type {channel.type}"

    ok, detail = transport(channel.target, message)

    channel.last_used_at = datetime.now(timezone.utc)
    if ok:
        channel.last_error = None
        channel.failure_count = 0
    else:
        channel.last_error = detail
        channel.failure_count += 1
        if channel.failure_count >= MAX_CHANNEL_FAILURES:
            channel.is_active = False
            logger.error(
                "channel %s disabled after %d consecutive failures: %s",
                channel.name,
                channel.failure_count,
                detail,
            )
    return ok, detail


def broadcast(org_id, event: AlertEvent, message: dict) -> list[dict]:
    """Deliver to every matching channel. One failure never blocks the others."""
    results = []
    for channel in channels_for(org_id, event):
        ok, detail = deliver(channel, message)
        results.append(
            {
                "channel_id": str(channel.id),
                "channel": channel.name,
                "type": channel.type.value,
                "ok": ok,
                "detail": detail,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info(
            "alert %s -> %s (%s): %s", event.value, channel.name, channel.type.value, detail
        )

    if not results:
        logger.info("alert %s for org %s: no channels configured", event.value, org_id)
    return results
