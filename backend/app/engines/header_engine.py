"""HTTP security header audit with a weighted 100-point rubric.

Presence alone is not enough for the two headers where a weak value is common
and meaningless, so HSTS and CSP are scored on quality: a short max-age or a
policy containing ``unsafe-inline`` earns partial credit, not full.
"""
import logging
import re

import requests

from app.utils.ssrf import SSRFError, assert_safe_url

logger = logging.getLogger(__name__)

USER_AGENT = "WebGuard-ScanPulse/1.0 (+header-audit)"

HEADER_WEIGHTS = {
    "Strict-Transport-Security": 25,
    "Content-Security-Policy": 25,
    "X-Frame-Options": 20,
    "X-Content-Type-Options": 15,
    "Referrer-Policy": 15,
}

#: Headers that leak stack details. Each costs a point off the final score.
LEAKY_HEADERS = ("server", "x-powered-by", "x-aspnet-version", "x-generator")

GRADE_THRESHOLDS = ((90, "A+"), (80, "A"), (65, "B"), (50, "C"), (30, "D"))

_MAX_AGE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)
_HSTS_MIN_MAX_AGE = 15_552_000  # 180 days, the widely used baseline.


def _score_hsts(value: str, weight: int) -> tuple[int, str | None]:
    match = _MAX_AGE.search(value)
    if not match:
        return weight // 3, "max-age directive missing"
    if int(match.group(1)) < _HSTS_MIN_MAX_AGE:
        return int(weight * 0.6), "max-age below the 180-day baseline"
    if "includesubdomains" not in value.lower():
        return int(weight * 0.8), "includeSubDomains not set"
    return weight, None


def _score_csp(value: str, weight: int) -> tuple[int, str | None]:
    lowered = value.lower()
    if "unsafe-inline" in lowered or "unsafe-eval" in lowered:
        return int(weight * 0.5), "policy allows unsafe-inline/unsafe-eval"
    if "default-src" not in lowered and "script-src" not in lowered:
        return int(weight * 0.6), "no default-src or script-src directive"
    return weight, None


_QUALITY_CHECKS = {
    "Strict-Transport-Security": _score_hsts,
    "Content-Security-Policy": _score_csp,
}


def _grade(score: int) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


def audit_security_headers(target_url: str, timeout: int = 10) -> dict:
    result = {"ok": False, "score": 0, "grade": "F", "error": None}

    try:
        assert_safe_url(target_url)
    except SSRFError as exc:
        result["error"] = f"blocked: {exc}"
        return result

    try:
        resp = requests.get(
            target_url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    headers = {k.lower(): v for k, v in resp.headers.items()}

    score = 0
    present, missing, warnings = {}, [], []

    for header, weight in HEADER_WEIGHTS.items():
        value = headers.get(header.lower())
        if value is None:
            missing.append(header)
            continue
        present[header] = value
        checker = _QUALITY_CHECKS.get(header)
        if checker:
            earned, warning = checker(value, weight)
            if warning:
                warnings.append(f"{header}: {warning}")
        else:
            earned = weight
        score += earned

    leaked = {h: headers[h] for h in LEAKY_HEADERS if h in headers}
    score = max(0, score - len(leaked))

    result.update(
        {
            "ok": True,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "score": score,
            "grade": _grade(score),
            "present": present,
            "missing": missing,
            "warnings": warnings,
            "information_disclosure": leaked,
        }
    )
    return result
