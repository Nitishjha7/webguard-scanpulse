"""Wire-format renderers and senders, one per channel type.

Every transport returns ``(ok, detail)`` and never raises for a network or API
error — a broken Slack webhook must not take down the alert for the other
channels on the same incident.
"""
import logging

import requests
from flask import current_app

from app.notifications.formatters import SEVERITY_COLORS, STATUS_EMOJI
from app.utils.ssrf import SSRFError, assert_safe_url

logger = logging.getLogger(__name__)

TIMEOUT = 10

#: Webhook URLs come from users, so they get the same SSRF treatment as monitor
#: targets, plus a host allowlist for the branded channel types — a "Slack"
#: channel pointing anywhere but Slack is a misconfiguration at best.
SLACK_HOSTS = ("hooks.slack.com",)
DISCORD_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")


def _check_webhook(url: str, allowed_hosts: tuple[str, ...] | None = None) -> str | None:
    """Return an error string if the URL is unusable, else None."""
    try:
        hostname, _, _ = assert_safe_url(url)
    except SSRFError as exc:
        return f"blocked: {exc}"
    if allowed_hosts and not any(
        hostname == h or hostname.endswith(f".{h}") for h in allowed_hosts
    ):
        return f"host {hostname} is not one of {', '.join(allowed_hosts)}"
    return None


def _post(url: str, json_payload: dict) -> tuple[bool, str]:
    try:
        resp = requests.post(url, json=json_payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return True, f"HTTP {resp.status_code}"


def send_slack(target: str, message: dict) -> tuple[bool, str]:
    error = _check_webhook(target, SLACK_HOSTS)
    if error:
        return False, error

    emoji = STATUS_EMOJI.get(message["severity"], "•")
    payload = {
        "text": f"{emoji} {message['title']}",
        "attachments": [
            {
                "color": SEVERITY_COLORS.get(message["severity"], "#666666"),
                "fallback": message["title"],
                "title": message["title"],
                "title_link": message["url"],
                "text": message["summary"],
                "fields": [
                    {"title": name, "value": value, "short": len(str(value)) < 40}
                    for name, value in message["fields"]
                ],
                "footer": "WebGuard ScanPulse",
            }
        ],
    }
    return _post(target, payload)


def send_discord(target: str, message: dict) -> tuple[bool, str]:
    error = _check_webhook(target, DISCORD_HOSTS)
    if error:
        return False, error

    emoji = STATUS_EMOJI.get(message["severity"], "•")
    color_hex = SEVERITY_COLORS.get(message["severity"], "#666666").lstrip("#")
    payload = {
        "username": "WebGuard ScanPulse",
        "embeds": [
            {
                "title": f"{emoji} {message['title']}",
                "url": message["url"],
                "description": message["summary"],
                "color": int(color_hex, 16),
                "fields": [
                    {"name": name, "value": str(value)[:1024], "inline": len(str(value)) < 40}
                    for name, value in message["fields"]
                ],
            }
        ],
    }
    return _post(target, payload)


def send_webhook(target: str, message: dict) -> tuple[bool, str]:
    """Generic JSON webhook — the raw message, for anything self-hosted."""
    error = _check_webhook(target)
    if error:
        return False, error
    return _post(target, {**message, "fields": dict(message["fields"])})


def send_email(target: str, message: dict) -> tuple[bool, str]:
    """SendGrid v3 Mail Send.

    Called through the REST API with ``requests`` rather than the SendGrid SDK:
    it is one POST, and the SDK would pull a dependency tree into both the API
    and worker images for no benefit.
    """
    api_key = current_app.config.get("SENDGRID_API_KEY")
    if not api_key:
        return False, "SENDGRID_API_KEY is not configured"

    rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#666'>{name}</td>"
        f"<td style='padding:4px 0'><code>{value}</code></td></tr>"
        for name, value in message["fields"]
    )
    html = (
        f"<h2 style='margin:0 0 8px'>{message['title']}</h2>"
        f"<p style='margin:0 0 16px'>{message['summary']}</p>"
        f"<table style='border-collapse:collapse;font-family:system-ui,sans-serif;font-size:14px'>{rows}</table>"
        f"<p style='margin-top:16px;color:#888;font-size:12px'>WebGuard ScanPulse</p>"
    )

    payload = {
        "personalizations": [{"to": [{"email": target}]}],
        "from": {
            "email": current_app.config["ALERT_FROM_EMAIL"],
            "name": current_app.config["ALERT_FROM_NAME"],
        },
        "subject": f"[{message['severity']}] {message['title']}",
        "content": [
            {"type": "text/plain", "value": f"{message['title']}\n\n{message['summary']}"},
            {"type": "text/html", "value": html},
        ],
    }

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"{exc.__class__.__name__}: {exc}"

    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return True, f"HTTP {resp.status_code}"


TRANSPORTS = {
    "slack": send_slack,
    "discord": send_discord,
    "email": send_email,
    "webhook": send_webhook,
}
