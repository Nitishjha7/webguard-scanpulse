"""Uptime + latency probe. The highest-frequency engine in the system."""
import logging
import time

import requests

from app.utils.ssrf import SSRFError, assert_safe_url

logger = logging.getLogger(__name__)

USER_AGENT = "WebGuard-ScanPulse/1.0 (+uptime-probe)"

#: 2xx and 3xx count as up. Everything else — including 401/403 — is down,
#: because a monitored endpoint that stops serving its content is an incident
#: regardless of which error code it chose.
UP_STATUS_CEILING = 400


def probe_http(url: str, timeout: int = 10, region: str = "default") -> dict:
    """Issue one GET and measure wall-clock latency.

    Redirects are followed: users monitor the URL they typed, and a redirect
    chain that ends in a 200 is a working site.
    """
    result = {
        "region": region,
        "status_code": None,
        "latency_ms": None,
        "is_up": False,
        "error": None,
    }

    try:
        assert_safe_url(url)
    except SSRFError as exc:
        result["error"] = f"blocked: {exc}"
        return result

    started = time.perf_counter()
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.Timeout:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result["error"] = f"timeout after {timeout}s"
        return result
    except requests.RequestException as exc:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["status_code"] = resp.status_code
    result["is_up"] = resp.status_code < UP_STATUS_CEILING
    result["final_url"] = resp.url
    if not result["is_up"]:
        result["error"] = f"HTTP {resp.status_code}"
    return result
