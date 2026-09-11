"""Alert transports and fan-out.

Network calls are stubbed with ``responses``: these tests are about our
behaviour on top of the HTTP result, not about Slack being reachable.
"""
import pytest
import responses

from app.models import AlertEvent, ChannelType, NotificationChannel
from app.notifications import dispatcher, transports

SLACK_URL = "https://hooks.slack.com/services/T000/B000/secret"
DISCORD_URL = "https://discord.com/api/webhooks/1/secret"


@pytest.fixture
def make_channel(db):
    def _make(org, channel_type=ChannelType.WEBHOOK, target="https://example.com/hook", **kwargs):
        channel = NotificationChannel(
            org_id=org.id,
            name=kwargs.pop("name", f"{channel_type.value} channel"),
            type=channel_type,
            target=target,
            **kwargs,
        )
        db.session.add(channel)
        db.session.commit()
        return channel

    return _make


MESSAGE = {
    "event": "incident.opened",
    "severity": "DOWN",
    "title": "Acme is DOWN",
    "summary": "Connection refused",
    "url": "https://acme.example.com",
    "fields": [("Monitor", "Acme"), ("Status", "DOWN")],
}


class TestHostAllowlist:
    @responses.activate
    def test_slack_channel_pointed_elsewhere_is_refused(self):
        """A 'Slack' channel aimed at another host is an exfiltration path."""
        # example.com resolves, so this reaches the allowlist check rather than
        # failing earlier on DNS.
        ok, detail = transports.send_slack("https://example.com/collect", MESSAGE)

        assert ok is False
        assert "not one of hooks.slack.com" in detail
        # Refused before any request left the process.
        assert len(responses.calls) == 0

    @responses.activate
    def test_discord_channel_pointed_elsewhere_is_refused(self):
        ok, detail = transports.send_discord("https://example.com/collect", MESSAGE)
        assert ok is False
        assert "not one of" in detail

    @responses.activate
    def test_slack_subdomain_of_the_allowed_host_is_accepted(self):
        responses.add(responses.POST, SLACK_URL, json={"ok": True}, status=200)
        assert transports.send_slack(SLACK_URL, MESSAGE)[0] is True


class TestSsrfOnWebhooks:
    def test_loopback_webhook_is_refused(self):
        ok, detail = transports.send_webhook("https://127.0.0.1/hook", MESSAGE)
        assert ok is False
        assert "blocked" in detail

    def test_metadata_endpoint_is_refused(self):
        ok, detail = transports.send_webhook("http://169.254.169.254/latest/meta-data/", MESSAGE)
        assert ok is False
        assert "blocked" in detail


class TestPayloadShape:
    @responses.activate
    def test_slack_payload_carries_severity_colour_and_fields(self):
        responses.add(responses.POST, SLACK_URL, json={"ok": True}, status=200)

        transports.send_slack(SLACK_URL, MESSAGE)

        sent = responses.calls[0].request.body
        import json

        payload = json.loads(sent)
        attachment = payload["attachments"][0]
        assert attachment["color"] == transports.SEVERITY_COLORS["DOWN"]
        assert attachment["title_link"] == MESSAGE["url"]
        assert len(attachment["fields"]) == len(MESSAGE["fields"])

    @responses.activate
    def test_discord_colour_is_an_integer(self):
        responses.add(responses.POST, DISCORD_URL, json={}, status=204)

        transports.send_discord(DISCORD_URL, MESSAGE)

        import json

        embed = json.loads(responses.calls[0].request.body)["embeds"][0]
        assert isinstance(embed["color"], int)


class TestErrorHandling:
    @responses.activate
    def test_http_error_is_reported_not_raised(self):
        responses.add(responses.POST, SLACK_URL, body="no_team", status=404)

        ok, detail = transports.send_slack(SLACK_URL, MESSAGE)

        assert ok is False
        assert "404" in detail

    @responses.activate
    def test_connection_error_is_reported_not_raised(self):
        import requests

        responses.add(responses.POST, SLACK_URL, body=requests.ConnectionError("boom"))

        ok, detail = transports.send_slack(SLACK_URL, MESSAGE)

        assert ok is False
        assert "ConnectionError" in detail

    def test_email_without_an_api_key_fails_cleanly(self, app):
        with app.app_context():
            app.config["SENDGRID_API_KEY"] = ""
            ok, detail = transports.send_email("ops@example.com", MESSAGE)

        assert ok is False
        assert "SENDGRID_API_KEY" in detail


class TestDispatcher:
    @responses.activate
    def test_one_failing_channel_does_not_block_the_others(
        self, app, db, make_org, make_channel
    ):
        """The whole point of per-channel isolation."""
        responses.add(responses.POST, SLACK_URL, body="no_team", status=404)
        responses.add(responses.POST, "https://example.com/hook", json={}, status=200)

        org = make_org()
        make_channel(org, ChannelType.SLACK, SLACK_URL, name="Broken Slack")
        make_channel(org, ChannelType.WEBHOOK, "https://example.com/hook", name="Good hook")

        results = dispatcher.broadcast(org.id, AlertEvent.INCIDENT_OPENED, MESSAGE)

        assert len(results) == 2
        assert {r["ok"] for r in results} == {True, False}

    @responses.activate
    def test_event_filter_is_honoured(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", json={}, status=200)
        org = make_org()
        make_channel(
            org,
            ChannelType.WEBHOOK,
            "https://example.com/hook",
            events=[AlertEvent.INCIDENT_OPENED.value],
        )

        assert len(dispatcher.broadcast(org.id, AlertEvent.INCIDENT_OPENED, MESSAGE)) == 1
        assert len(dispatcher.broadcast(org.id, AlertEvent.SSL_EXPIRING, MESSAGE)) == 0

    @responses.activate
    def test_null_events_means_every_event(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", json={}, status=200)
        org = make_org()
        make_channel(org, ChannelType.WEBHOOK, "https://example.com/hook", events=None)

        for event in AlertEvent:
            assert len(dispatcher.broadcast(org.id, event, MESSAGE)) == 1

    @responses.activate
    def test_inactive_channels_are_skipped(self, db, make_org, make_channel):
        org = make_org()
        make_channel(org, ChannelType.WEBHOOK, "https://example.com/hook", is_active=False)

        assert dispatcher.broadcast(org.id, AlertEvent.INCIDENT_OPENED, MESSAGE) == []

    @responses.activate
    def test_channels_never_cross_tenants(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", json={}, status=200)
        org_a, org_b = make_org("A"), make_org("B")
        make_channel(org_a, ChannelType.WEBHOOK, "https://example.com/hook")

        assert dispatcher.broadcast(org_b.id, AlertEvent.INCIDENT_OPENED, MESSAGE) == []


class TestChannelHealth:
    @responses.activate
    def test_success_clears_a_previous_error(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", json={}, status=200)
        channel = make_channel(make_org(), failure_count=3, last_error="old failure")

        dispatcher.deliver(channel, MESSAGE)

        assert channel.failure_count == 0
        assert channel.last_error is None
        assert channel.last_used_at is not None

    @responses.activate
    def test_channel_is_disabled_after_repeated_failures(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", status=500)
        channel = make_channel(make_org())

        for _ in range(dispatcher.MAX_CHANNEL_FAILURES):
            dispatcher.deliver(channel, MESSAGE)

        assert channel.is_active is False
        assert channel.failure_count == dispatcher.MAX_CHANNEL_FAILURES

    @responses.activate
    def test_a_few_failures_do_not_disable(self, db, make_org, make_channel):
        responses.add(responses.POST, "https://example.com/hook", status=500)
        channel = make_channel(make_org())

        for _ in range(dispatcher.MAX_CHANNEL_FAILURES - 1):
            dispatcher.deliver(channel, MESSAGE)

        assert channel.is_active is True


class TestSecretMasking:
    def test_webhook_target_is_truncated_in_api_output(self, db, make_org, make_channel):
        """Webhook URLs embed their own credential."""
        channel = make_channel(make_org(), ChannelType.SLACK, SLACK_URL)

        assert "secret" not in channel.to_dict()["target"]

    def test_email_target_is_shown_in_full(self, db, make_org, make_channel):
        channel = make_channel(make_org(), ChannelType.EMAIL, "ops@example.com")

        assert channel.to_dict()["target"] == "ops@example.com"
