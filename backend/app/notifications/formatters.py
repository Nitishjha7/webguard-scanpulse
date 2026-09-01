"""Alert payload builders.

One neutral message dict is built per event and each transport renders it into
its own wire format. Adding a channel means adding a renderer, not touching the
incident code.
"""
from app.models import AlertEvent

SEVERITY_COLORS = {
    "DOWN": "#d13438",
    "DEGRADED": "#f7a501",
    "RESOLVED": "#2ea043",
    "WARNING": "#f7a501",
}

STATUS_EMOJI = {
    "DOWN": "🔴",
    "DEGRADED": "🟡",
    "RESOLVED": "🟢",
    "WARNING": "⚠️",
}


def _humanize_duration(seconds: float | None) -> str:
    if seconds is None:
        return "ongoing"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def incident_message(incident, monitor, event: AlertEvent) -> dict:
    status = incident.status.value
    opened = event is AlertEvent.INCIDENT_OPENED

    if opened:
        title = f"{monitor.name} is {status}"
        summary = incident.root_cause or "No response from target"
    else:
        title = f"{monitor.name} has recovered"
        summary = f"Down for {_humanize_duration(incident.duration_seconds)}"

    return {
        "event": event.value,
        "severity": status if opened else "RESOLVED",
        "title": title,
        "summary": summary,
        "url": monitor.url,
        "fields": [
            ("Monitor", monitor.name),
            ("URL", monitor.url),
            ("Status", status),
            ("Started", incident.started_at.isoformat()),
            ("Duration", _humanize_duration(incident.duration_seconds)),
            ("Failed probes", str(incident.failure_count)),
            ("Root cause", incident.root_cause or "—"),
        ],
    }


def ssl_message(monitor, scan, event: AlertEvent) -> dict:
    if event is AlertEvent.SSL_INVALID:
        title = f"{monitor.name}: TLS certificate is invalid"
        summary = scan.verify_error or scan.error or "Certificate failed validation"
        severity = "DOWN"
    else:
        title = f"{monitor.name}: TLS certificate expires in {scan.days_left} days"
        summary = f"Expires {scan.valid_to.isoformat() if scan.valid_to else 'unknown'}"
        severity = "WARNING"

    return {
        "event": event.value,
        "severity": severity,
        "title": title,
        "summary": summary,
        "url": monitor.url,
        "fields": [
            ("Monitor", monitor.name),
            ("URL", monitor.url),
            ("Issuer", scan.issuer or "—"),
            ("Expires", scan.valid_to.isoformat() if scan.valid_to else "—"),
            ("Days left", str(scan.days_left)),
            ("TLS version", scan.tls_version or "—"),
        ],
    }
