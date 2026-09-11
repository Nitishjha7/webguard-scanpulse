"""Auth, role enforcement and tenant isolation through the HTTP API.

Isolation is checked at the boundary rather than on the query helper, because
that is where a regression would actually leak data.
"""
import pytest


class TestRegistration:
    def test_register_creates_org_and_admin(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "password123", "org_name": "Acme"},
        )

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["user"]["role"] == "Admin"
        assert body["organization"]["slug"] == "acme"
        assert body["access_token"] and body["refresh_token"]

    def test_duplicate_email_is_rejected(self, client):
        payload = {"email": "dup@example.com", "password": "password123", "org_name": "A"}
        client.post("/api/v1/auth/register", json=payload)

        resp = client.post("/api/v1/auth/register", json={**payload, "org_name": "B"})

        assert resp.status_code == 409

    def test_org_slugs_are_made_unique(self, client):
        for email in ("a@example.com", "b@example.com"):
            client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "password123", "org_name": "Same Name"},
            )

        resp = client.post(
            "/api/v1/auth/register",
            json={"email": "c@example.com", "password": "password123", "org_name": "Same Name"},
        )
        assert resp.get_json()["organization"]["slug"] == "same-name-3"

    @pytest.mark.parametrize(
        "payload,status",
        [
            ({"email": "x@example.com", "password": "short", "org_name": "A"}, 422),
            ({"email": "not-an-email", "password": "password123", "org_name": "A"}, 422),
            ({"email": "x@example.com", "password": "password123"}, 422),
            ({}, 422),
        ],
    )
    def test_invalid_payloads_are_rejected(self, client, payload, status):
        assert client.post("/api/v1/auth/register", json=payload).status_code == status


class TestLogin:
    def test_login_returns_tokens(self, client, auth_headers):
        _, body = auth_headers("login@example.com")

        resp = client.post(
            "/api/v1/auth/login", json={"email": "login@example.com", "password": "password123"}
        )

        assert resp.status_code == 200
        assert resp.get_json()["user"]["id"] == body["user"]["id"]

    def test_wrong_password_and_unknown_email_are_indistinguishable(self, client, auth_headers):
        auth_headers("real@example.com")

        wrong = client.post(
            "/api/v1/auth/login", json={"email": "real@example.com", "password": "nope"}
        )
        unknown = client.post(
            "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "password123"}
        )

        assert wrong.status_code == unknown.status_code == 401
        # Identical message, or the endpoint becomes an account enumerator.
        assert wrong.get_json()["error"]["message"] == unknown.get_json()["error"]["message"]

    def test_reserved_domain_account_can_still_log_in(self, client, db, make_org, make_user):
        """Regression: the seeded demo admin lives at ``@webguard.local``.

        Sign-up validation rejects special-use domains, and login used to apply
        the same rules — locking out an account that already existed.
        """
        from app.models import UserRole

        user = make_user(make_org(), email="admin@webguard.local", role=UserRole.ADMIN)

        resp = client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "password123"}
        )

        assert resp.status_code == 200

    def test_login_is_case_and_whitespace_insensitive(self, client, auth_headers):
        auth_headers("mixed@example.com")

        resp = client.post(
            "/api/v1/auth/login", json={"email": "  MiXeD@Example.COM  ", "password": "password123"}
        )

        assert resp.status_code == 200


class TestAuthorization:
    def test_unauthenticated_requests_are_rejected(self, client):
        for path in ("/api/v1/monitors", "/api/v1/incidents", "/api/v1/channels"):
            assert client.get(path).status_code == 401

    def test_garbage_token_is_rejected(self, client):
        resp = client.get("/api/v1/monitors", headers={"Authorization": "Bearer not-a-token"})
        assert resp.status_code == 401

    def test_viewer_can_read_but_not_write(self, client, auth_headers):
        headers, _ = auth_headers()
        client.post(
            "/api/v1/auth/users",
            headers=headers,
            json={"email": "viewer@example.com", "password": "password123", "role": "Viewer"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"email": "viewer@example.com", "password": "password123"}
        ).get_json()["access_token"]
        viewer = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/monitors", headers=viewer).status_code == 200
        assert (
            client.post(
                "/api/v1/monitors", headers=viewer, json={"name": "X", "url": "https://x.example.com"}
            ).status_code
            == 403
        )

    def test_engineer_cannot_delete_a_monitor(self, client, auth_headers):
        headers, _ = auth_headers()
        monitor_id = client.post(
            "/api/v1/monitors", headers=headers, json={"name": "M", "url": "https://m.example.com"}
        ).get_json()["monitor"]["id"]

        client.post(
            "/api/v1/auth/users",
            headers=headers,
            json={"email": "eng@example.com", "password": "password123", "role": "Engineer"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"email": "eng@example.com", "password": "password123"}
        ).get_json()["access_token"]
        engineer = {"Authorization": f"Bearer {token}"}

        assert client.patch(
            f"/api/v1/monitors/{monitor_id}", headers=engineer, json={"name": "renamed"}
        ).status_code == 200
        assert client.delete(f"/api/v1/monitors/{monitor_id}", headers=engineer).status_code == 403

    def test_invited_user_lands_in_the_inviting_org(self, client, auth_headers):
        headers, body = auth_headers()

        resp = client.post(
            "/api/v1/auth/users",
            headers=headers,
            json={"email": "member@example.com", "password": "password123", "role": "Engineer"},
        )

        assert resp.get_json()["user"]["org_id"] == body["organization"]["id"]


class TestTenantIsolation:
    @pytest.fixture
    def two_tenants(self, client, auth_headers):
        owner, _ = auth_headers("owner-a@example.com", org_name="Tenant A")
        intruder, _ = auth_headers("owner-b@example.com", org_name="Tenant B")
        monitor_id = client.post(
            "/api/v1/monitors",
            headers=owner,
            json={"name": "Private", "url": "https://private.example.com"},
        ).get_json()["monitor"]["id"]
        return owner, intruder, monitor_id

    def test_listing_never_crosses_tenants(self, client, two_tenants):
        _, intruder, _ = two_tenants
        assert client.get("/api/v1/monitors", headers=intruder).get_json()["pagination"]["total"] == 0

    @pytest.mark.parametrize(
        "method,suffix",
        [
            ("get", ""),
            ("get", "/pings"),
            ("get", "/ssl"),
            ("get", "/security"),
            ("get", "/incidents"),
            ("patch", ""),
            ("delete", ""),
            ("post", "/scan"),
        ],
    )
    def test_foreign_monitor_is_not_found(self, client, two_tenants, method, suffix):
        _, intruder, monitor_id = two_tenants

        resp = getattr(client, method)(
            f"/api/v1/monitors/{monitor_id}{suffix}", headers=intruder, json={}
        )

        # 404, not 403 — a 403 would confirm the id exists.
        assert resp.status_code == 404

    def test_foreign_channel_is_not_found(self, client, auth_headers):
        owner, _ = auth_headers("chan-a@example.com")
        intruder, _ = auth_headers("chan-b@example.com")
        channel_id = client.post(
            "/api/v1/channels",
            headers=owner,
            json={"name": "Ops", "type": "webhook", "target": "https://example.com/hook"},
        ).get_json()["channel"]["id"]

        assert client.delete(f"/api/v1/channels/{channel_id}", headers=intruder).status_code == 404
        assert (
            client.post(f"/api/v1/channels/{channel_id}/test", headers=intruder).status_code == 404
        )

    def test_malformed_id_is_a_400_not_a_500(self, client, auth_headers):
        headers, _ = auth_headers()
        assert client.get("/api/v1/monitors/not-a-uuid", headers=headers).status_code == 400
