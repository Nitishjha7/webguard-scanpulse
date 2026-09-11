"""Synthetic end-to-end journeys, driven by headless Chromium.

A check is a list of steps in a small JSON DSL::

    [
      {"action": "goto",              "url": "https://shop.example.com/login"},
      {"action": "fill",              "selector": "#email",    "value": "bot@example.com"},
      {"action": "fill",              "selector": "#password", "value": "…", "secret": true},
      {"action": "click",             "selector": "button[type=submit]"},
      {"action": "wait_for_selector", "selector": ".dashboard"},
      {"action": "expect_text",       "selector": "h1", "value": "Dashboard"},
      {"action": "expect_url",        "value": "/dashboard"}
    ]

A closed vocabulary rather than user-supplied JavaScript: `page.evaluate` with
a tenant-authored string would be arbitrary code execution inside the worker.
Everything here maps onto a specific, bounded Playwright call.

Playwright is imported lazily so the API image — which has no browser — can
still import this module to validate step definitions.
"""
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.utils.ssrf import SSRFError, assert_safe_url

logger = logging.getLogger(__name__)

#: Per-step budget. The whole-journey limit is enforced by the caller.
DEFAULT_STEP_TIMEOUT_MS = 15_000

USER_AGENT = "WebGuard-ScanPulse/1.0 (+synthetic-monitor)"

MAX_STEPS = 40
MAX_SELECTOR_LENGTH = 500
MAX_VALUE_LENGTH = 2_000


class StepError(Exception):
    """A step's assertion did not hold — the journey is broken."""

    def __init__(self, message: str, index: int):
        super().__init__(message)
        self.message = message
        self.index = index


class ValidationError(ValueError):
    """The step definition itself is malformed."""


# --- Step vocabulary -------------------------------------------------------
#
# action -> required keys
STEP_SCHEMA: dict[str, tuple[str, ...]] = {
    "goto": ("url",),
    "click": ("selector",),
    "fill": ("selector", "value"),
    "select": ("selector", "value"),
    "press": ("key",),
    "wait_for_selector": ("selector",),
    "wait_for_timeout": ("ms",),
    "expect_text": ("selector", "value"),
    "expect_not_text": ("selector", "value"),
    "expect_url": ("value",),
    "expect_selector_count": ("selector", "count"),
    "screenshot": (),
}

MAX_WAIT_MS = 30_000


def validate_steps(steps) -> list[dict]:
    """Validate a step list, raising :class:`ValidationError` on anything wrong.

    Runs in the API process at write time so a broken journey is rejected while
    the user is looking at it, rather than failing silently on a worker at 4am.
    """
    if not isinstance(steps, list) or not steps:
        raise ValidationError("steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise ValidationError(f"a check may have at most {MAX_STEPS} steps")

    if not isinstance(steps[0], dict) or steps[0].get("action") != "goto":
        raise ValidationError("the first step must be a 'goto'")

    cleaned = []
    for index, step in enumerate(steps):
        cleaned.append(_validate_step(step, index))
    return cleaned


def _validate_step(step, index: int) -> dict:
    where = f"step {index}"
    if not isinstance(step, dict):
        raise ValidationError(f"{where}: must be an object")

    action = step.get("action")
    if action not in STEP_SCHEMA:
        raise ValidationError(
            f"{where}: unknown action {action!r}; allowed: {', '.join(sorted(STEP_SCHEMA))}"
        )

    missing = [key for key in STEP_SCHEMA[action] if step.get(key) in (None, "")]
    if missing:
        raise ValidationError(f"{where} ({action}): missing {', '.join(missing)}")

    if action == "goto":
        _validate_url(step["url"], where)

    for key in ("selector", "value", "key"):
        value = step.get(key)
        if value is not None and not isinstance(value, str):
            raise ValidationError(f"{where}: '{key}' must be a string")

    if len(str(step.get("selector", ""))) > MAX_SELECTOR_LENGTH:
        raise ValidationError(f"{where}: selector is too long")
    if len(str(step.get("value", ""))) > MAX_VALUE_LENGTH:
        raise ValidationError(f"{where}: value is too long")

    if action == "wait_for_timeout":
        ms = step.get("ms")
        if not isinstance(ms, int) or not 0 < ms <= MAX_WAIT_MS:
            raise ValidationError(f"{where}: 'ms' must be an integer 1-{MAX_WAIT_MS}")

    if action == "expect_selector_count":
        count = step.get("count")
        if not isinstance(count, int) or count < 0:
            raise ValidationError(f"{where}: 'count' must be a non-negative integer")

    return step


def _validate_url(url, where: str) -> None:
    if not isinstance(url, str):
        raise ValidationError(f"{where}: 'url' must be a string")
    try:
        assert_safe_url(url)
    except SSRFError as exc:
        # A journey that navigates to 169.254.169.254 would read cloud
        # credentials with a real browser. Refuse it at definition time.
        raise ValidationError(f"{where}: {exc}") from None


# --- Execution -------------------------------------------------------------


def _is_safe_navigation(url: str) -> bool:
    try:
        assert_safe_url(url)
        return True
    except SSRFError:
        return False


def run_journey(
    steps: list[dict],
    *,
    timeout_seconds: int = 60,
    viewport: tuple[int, int] = (1280, 720),
    artifacts_dir: str | Path = "/app/artifacts",
    capture_screenshot: bool = True,
) -> dict:
    """Execute ``steps`` in headless Chromium.

    Returns a result dict; it never raises for a failing journey, only for a
    programming error. ``status`` is PASSED, FAILED (an assertion broke) or
    ERROR (the run could not complete at all) — the distinction matters,
    because ERROR means *we* are broken, not the customer's site.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    step_results: list[dict] = []
    result = {
        "status": "ERROR",
        "started_at": started,
        "failed_step": None,
        "error": None,
        "screenshot": None,
        "final_url": None,
        "step_results": step_results,
    }

    step_timeout = min(DEFAULT_STEP_TIMEOUT_MS, timeout_seconds * 1000)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": viewport[0], "height": viewport[1]},
                    user_agent=USER_AGENT,
                    ignore_https_errors=False,
                )
                context.set_default_timeout(step_timeout)
                page = context.new_page()

                # Second line of defence. Step URLs are checked at definition
                # time, but a page can redirect anywhere, and sub-resources are
                # fetched without passing through our code at all.
                page.route("**/*", _block_private_requests)

                _execute(page, steps, step_results, result)

                result["final_url"] = page.url
                if result["status"] == "FAILED" and capture_screenshot:
                    result["screenshot"] = _capture(page, artifacts_dir)
            finally:
                browser.close()
    except (PlaywrightError, PlaywrightTimeout) as exc:
        result["error"] = f"browser error: {exc.__class__.__name__}: {str(exc)[:400]}"
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        logger.exception("synthetic run crashed")
        result["error"] = f"{exc.__class__.__name__}: {str(exc)[:400]}"

    result["duration_ms"] = round((time.perf_counter() - clock) * 1000, 2)
    result["finished_at"] = datetime.now(timezone.utc)
    return result


def _block_private_requests(route, request) -> None:
    """Abort any request whose host is not publicly routable."""
    if _is_safe_navigation(request.url):
        route.continue_()
    else:
        logger.warning("synthetic run blocked request to %s", request.url)
        route.abort("blockedbyclient")


def _execute(page, steps, step_results, result) -> None:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for index, step in enumerate(steps):
        action = step["action"]
        step_clock = time.perf_counter()
        try:
            _dispatch(page, step, index)
        except StepError as exc:
            step_results.append(_step_result(index, action, False, step_clock, exc.message))
            result.update({"status": "FAILED", "failed_step": index, "error": exc.message})
            return
        except PlaywrightTimeout as exc:
            message = f"timed out: {str(exc).splitlines()[0][:200]}"
            step_results.append(_step_result(index, action, False, step_clock, message))
            result.update({"status": "FAILED", "failed_step": index, "error": message})
            return
        except PlaywrightError as exc:
            message = f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:200]}"
            step_results.append(_step_result(index, action, False, step_clock, message))
            result.update({"status": "FAILED", "failed_step": index, "error": message})
            return

        step_results.append(_step_result(index, action, True, step_clock, None))

    result["status"] = "PASSED"


def _step_result(index, action, ok, clock, error) -> dict:
    return {
        "index": index,
        "action": action,
        "ok": ok,
        "duration_ms": round((time.perf_counter() - clock) * 1000, 2),
        "error": error,
    }


def _dispatch(page, step: dict, index: int) -> None:
    action = step["action"]

    if action == "goto":
        if not _is_safe_navigation(step["url"]):
            raise StepError(f"navigation to {step['url']} is blocked", index)
        page.goto(step["url"], wait_until="domcontentloaded")

    elif action == "click":
        page.click(step["selector"])

    elif action == "fill":
        page.fill(step["selector"], step["value"])

    elif action == "select":
        page.select_option(step["selector"], step["value"])

    elif action == "press":
        page.press(step.get("selector") or "body", step["key"])

    elif action == "wait_for_selector":
        page.wait_for_selector(step["selector"])

    elif action == "wait_for_timeout":
        page.wait_for_timeout(step["ms"])

    elif action == "expect_text":
        actual = page.text_content(step["selector"]) or ""
        if step["value"] not in actual:
            raise StepError(
                f"expected {step['value']!r} in {step['selector']}, found {actual.strip()[:120]!r}",
                index,
            )

    elif action == "expect_not_text":
        actual = page.text_content(step["selector"]) or ""
        if step["value"] in actual:
            raise StepError(
                f"did not expect {step['value']!r} in {step['selector']}, but it was there", index
            )

    elif action == "expect_url":
        if step["value"] not in page.url:
            raise StepError(f"expected URL to contain {step['value']!r}, got {page.url!r}", index)

    elif action == "expect_selector_count":
        actual = page.locator(step["selector"]).count()
        if actual != step["count"]:
            raise StepError(
                f"expected {step['count']} matches for {step['selector']}, found {actual}", index
            )

    elif action == "screenshot":
        pass  # Captured by the caller on failure; an explicit step is a no-op marker.

    else:  # pragma: no cover - validate_steps rejects these first
        raise StepError(f"unknown action {action!r}", index)


_SAFE_NAME = re.compile(r"[^a-z0-9-]")


def _capture(page, artifacts_dir: str | Path) -> str | None:
    """Screenshot the failure. Name is generated, never taken from user input."""
    try:
        directory = Path(artifacts_dir)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"run-{uuid.uuid4().hex}.png"
        page.screenshot(path=str(directory / filename), full_page=False)
        return filename
    except Exception as exc:  # noqa: BLE001 - a missing screenshot must not fail the run
        logger.warning("could not capture screenshot: %s", exc)
        return None
