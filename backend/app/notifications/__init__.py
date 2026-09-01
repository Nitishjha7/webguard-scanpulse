"""Multi-channel alert delivery: Slack, Discord, email (SendGrid) and generic webhooks."""
from app.notifications.dispatcher import broadcast, channels_for, deliver
from app.notifications.formatters import incident_message, ssl_message

__all__ = ["broadcast", "channels_for", "deliver", "incident_message", "ssl_message"]
