"""Scoring engines.

The scoring rubrics are where opinions live — a header that is present but
weak, a DMARC policy that reports instead of blocking. These tests pin those
opinions down so a refactor cannot quietly turn an F into an A.
"""
import responses

from app.engines.dns_engine import _audit_dmarc, _audit_spf, _grade
from app.engines.header_engine import HEADER_WEIGHTS, audit_security_headers

TARGET = "https://example.com/"

STRONG_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _stub(headers: dict, status: int = 200):
    responses.add(responses.GET, TARGET, headers=headers, status=status, body="ok")


class TestHeaderScoring:
    @responses.activate
    def test_all_headers_strong_scores_top_grade(self):
        _stub(STRONG_HEADERS)

        result = audit_security_headers(TARGET)

        assert result["ok"] is True
        assert result["score"] == sum(HEADER_WEIGHTS.values())
        assert result["grade"] == "A+"
        assert result["missing"] == []

    @responses.activate
    def test_no_headers_scores_zero(self):
        _stub({})

        result = audit_security_headers(TARGET)

        assert result["score"] == 0
        assert result["grade"] == "F"
        assert set(result["missing"]) == set(HEADER_WEIGHTS)

    @responses.activate
    def test_short_hsts_max_age_earns_partial_credit(self):
        """A max-age of a few minutes looks compliant and protects nothing."""
        _stub({**STRONG_HEADERS, "Strict-Transport-Security": "max-age=300"})

        result = audit_security_headers(TARGET)

        weight = HEADER_WEIGHTS["Strict-Transport-Security"]
        assert result["score"] < sum(HEADER_WEIGHTS.values())
        assert result["score"] >= sum(HEADER_WEIGHTS.values()) - weight
        assert any("max-age below" in w for w in result["warnings"])

    @responses.activate
    def test_hsts_without_max_age_scores_lowest(self):
        _stub({**STRONG_HEADERS, "Strict-Transport-Security": "includeSubDomains"})

        result = audit_security_headers(TARGET)

        assert any("max-age directive missing" in w for w in result["warnings"])

    @responses.activate
    def test_csp_with_unsafe_inline_earns_half_credit(self):
        _stub({**STRONG_HEADERS, "Content-Security-Policy": "default-src 'self' 'unsafe-inline'"})

        result = audit_security_headers(TARGET)

        # Half credit: the header is there, the policy is not worth much.
        weight = HEADER_WEIGHTS["Content-Security-Policy"]
        assert result["score"] == sum(HEADER_WEIGHTS.values()) - weight + int(weight * 0.5)
        assert any("unsafe-inline" in w for w in result["warnings"])

    @responses.activate
    def test_server_header_costs_a_point(self):
        _stub({**STRONG_HEADERS, "Server": "nginx/1.2.3", "X-Powered-By": "PHP/8"})

        result = audit_security_headers(TARGET)

        assert result["score"] == sum(HEADER_WEIGHTS.values()) - 2
        assert set(result["information_disclosure"]) == {"server", "x-powered-by"}

    @responses.activate
    def test_header_matching_is_case_insensitive(self):
        _stub({k.lower(): v for k, v in STRONG_HEADERS.items()})

        assert audit_security_headers(TARGET)["grade"] == "A+"

    def test_private_target_is_blocked_before_any_request(self):
        result = audit_security_headers("http://127.0.0.1/")

        assert result["ok"] is False
        assert "blocked" in result["error"]

    @responses.activate
    def test_network_failure_is_reported_not_raised(self):
        import requests

        responses.add(responses.GET, TARGET, body=requests.ConnectTimeout("timed out"))

        result = audit_security_headers(TARGET)

        assert result["ok"] is False
        assert result["grade"] == "F"


class TestSpfScoring:
    def test_missing_spf_scores_zero(self):
        assert _audit_spf([])["score"] == 0

    def test_enforcing_all_scores_highest(self):
        strict = _audit_spf(["v=spf1 include:_spf.example.com -all"])
        soft = _audit_spf(["v=spf1 include:_spf.example.com ~all"])

        assert strict["score"] > soft["score"]
        assert strict["issue"] is None
        assert "softfail" in soft["issue"]

    def test_plus_all_scores_zero_and_is_called_out(self):
        """'+all' permits any sender — worse than publishing nothing."""
        result = _audit_spf(["v=spf1 +all"])

        assert result["score"] == 0
        assert "any sender" in result["issue"]

    def test_multiple_spf_records_are_a_permerror(self):
        result = _audit_spf(["v=spf1 -all", "v=spf1 ~all"])

        assert result["score"] <= 5
        assert "Multiple SPF" in result["issue"]

    def test_unrelated_txt_records_are_ignored(self):
        assert _audit_spf(["google-site-verification=abc", "some-other-txt"])["present"] is False


class TestDmarcScoring:
    def test_reject_beats_quarantine_beats_none(self):
        reject = _audit_dmarc(["v=DMARC1; p=reject; rua=mailto:a@example.com"])
        quarantine = _audit_dmarc(["v=DMARC1; p=quarantine; rua=mailto:a@example.com"])
        none = _audit_dmarc(["v=DMARC1; p=none; rua=mailto:a@example.com"])

        assert reject["score"] > quarantine["score"] > none["score"]

    def test_p_none_is_flagged_as_monitoring_only(self):
        result = _audit_dmarc(["v=DMARC1; p=none"])

        assert "does not block" in result["issue"]

    def test_missing_rua_is_flagged(self):
        result = _audit_dmarc(["v=DMARC1; p=reject"])

        assert "rua=" in result["issue"]

    def test_tags_are_parsed(self):
        result = _audit_dmarc(["v=DMARC1; p=reject; pct=100; rua=mailto:a@example.com"])

        assert result["tags"]["pct"] == "100"
        assert result["policy"] == "reject"

    def test_score_is_capped(self):
        assert _audit_dmarc(["v=DMARC1; p=reject; rua=mailto:a@example.com"])["score"] <= 40


class TestGradeBoundaries:
    def test_thresholds(self):
        assert _grade(100) == "A+"
        assert _grade(90) == "A+"
        assert _grade(89) == "A"
        assert _grade(80) == "A"
        assert _grade(65) == "B"
        assert _grade(50) == "C"
        assert _grade(30) == "D"
        assert _grade(29) == "F"
        assert _grade(0) == "F"
