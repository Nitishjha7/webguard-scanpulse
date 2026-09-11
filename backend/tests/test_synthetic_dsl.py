"""Synthetic step DSL: validation and secret handling.

Validation runs in the API process at write time, so a malformed journey is
rejected while the user is looking at it rather than failing on a worker at 4am.
No browser is needed for any of this.
"""
from unittest.mock import patch

import pytest

from app.engines.synthetic import MAX_STEPS, STEP_SCHEMA, ValidationError, validate_steps
from app.models.synthetic import REDACTED, redact_steps
from app.utils.ssrf import SSRFError

GOTO = {"action": "goto", "url": "https://shop.example.com/login"}


@pytest.fixture(autouse=True)
def public_urls():
    """Treat every URL as publicly routable unless a test says otherwise."""
    with patch("app.engines.synthetic.assert_safe_url", return_value=("host", 443, ["1.2.3.4"])):
        yield


class TestShape:
    def test_a_minimal_journey_validates(self):
        assert validate_steps([GOTO]) == [GOTO]

    @pytest.mark.parametrize("steps", [[], None, "goto", {}, 42])
    def test_empty_or_non_list_is_rejected(self, steps):
        with pytest.raises(ValidationError, match="non-empty list"):
            validate_steps(steps)

    def test_first_step_must_be_a_goto(self):
        """Without an opening navigation there is no page to act on."""
        with pytest.raises(ValidationError, match="first step must be a 'goto'"):
            validate_steps([{"action": "click", "selector": "#go"}])

    def test_step_count_is_capped(self):
        with pytest.raises(ValidationError, match=f"at most {MAX_STEPS}"):
            validate_steps([GOTO] + [{"action": "click", "selector": "#x"}] * MAX_STEPS)

    def test_non_object_step_is_rejected(self):
        with pytest.raises(ValidationError, match="must be an object"):
            validate_steps([GOTO, "click #go"])


class TestActionVocabulary:
    def test_unknown_action_is_rejected_and_lists_the_alternatives(self):
        with pytest.raises(ValidationError) as exc:
            validate_steps([GOTO, {"action": "evaluate", "script": "fetch('/admin')"}])

        assert "unknown action" in str(exc.value)
        assert "expect_text" in str(exc.value)

    def test_every_documented_action_is_accepted(self):
        samples = {
            "goto": GOTO,
            "click": {"action": "click", "selector": "#submit"},
            "fill": {"action": "fill", "selector": "#email", "value": "a@example.com"},
            "select": {"action": "select", "selector": "#country", "value": "IN"},
            "press": {"action": "press", "key": "Enter"},
            "wait_for_selector": {"action": "wait_for_selector", "selector": ".ready"},
            "wait_for_timeout": {"action": "wait_for_timeout", "ms": 500},
            "expect_text": {"action": "expect_text", "selector": "h1", "value": "Hi"},
            "expect_not_text": {"action": "expect_not_text", "selector": "h1", "value": "Error"},
            "expect_url": {"action": "expect_url", "value": "/dashboard"},
            "expect_selector_count": {
                "action": "expect_selector_count",
                "selector": ".row",
                "count": 3,
            },
            "screenshot": {"action": "screenshot"},
        }
        assert set(samples) == set(STEP_SCHEMA), "a documented action is missing a sample"

        for action, step in samples.items():
            validate_steps([GOTO, step]), action


class TestRequiredFields:
    @pytest.mark.parametrize(
        "step,missing",
        [
            ({"action": "click"}, "selector"),
            ({"action": "fill", "selector": "#a"}, "value"),
            ({"action": "expect_text", "selector": "#a"}, "value"),
            ({"action": "press"}, "key"),
            ({"action": "expect_url"}, "value"),
        ],
    )
    def test_missing_required_field_is_named(self, step, missing):
        with pytest.raises(ValidationError, match=missing):
            validate_steps([GOTO, step])

    def test_empty_string_counts_as_missing(self):
        with pytest.raises(ValidationError, match="selector"):
            validate_steps([GOTO, {"action": "click", "selector": ""}])

    def test_non_string_selector_is_rejected(self):
        with pytest.raises(ValidationError, match="must be a string"):
            validate_steps([GOTO, {"action": "click", "selector": {"css": "#a"}}])


class TestBounds:
    def test_oversized_selector_is_rejected(self):
        with pytest.raises(ValidationError, match="selector is too long"):
            validate_steps([GOTO, {"action": "click", "selector": "a" * 5000}])

    def test_oversized_value_is_rejected(self):
        with pytest.raises(ValidationError, match="value is too long"):
            validate_steps(
                [GOTO, {"action": "fill", "selector": "#a", "value": "x" * 50_000}]
            )

    @pytest.mark.parametrize("ms", [0, -1, 999_999, "500", 1.5])
    def test_bad_wait_duration_is_rejected(self, ms):
        with pytest.raises(ValidationError, match="'ms' must be"):
            validate_steps([GOTO, {"action": "wait_for_timeout", "ms": ms}])

    @pytest.mark.parametrize("count", [-1, "3", None])
    def test_bad_selector_count_is_rejected(self, count):
        with pytest.raises(ValidationError):
            validate_steps(
                [GOTO, {"action": "expect_selector_count", "selector": ".r", "count": count}]
            )

    def test_zero_expected_matches_is_allowed(self):
        """Asserting an element is *absent* is a legitimate check."""
        validate_steps(
            [GOTO, {"action": "expect_selector_count", "selector": ".error", "count": 0}]
        )


class TestSsrfAtDefinitionTime:
    def test_private_navigation_target_is_rejected(self):
        """A real browser pointed at the metadata endpoint would read cloud
        credentials, so this has to be caught before the journey is stored."""
        with patch(
            "app.engines.synthetic.assert_safe_url",
            side_effect=SSRFError("169.254.169.254 resolves to non-public address"),
        ):
            with pytest.raises(ValidationError, match="non-public"):
                validate_steps([{"action": "goto", "url": "http://169.254.169.254/"}])

    def test_a_later_goto_is_checked_too(self):
        calls = []

        def _checker(url):
            calls.append(url)
            if "internal" in url:
                raise SSRFError("blocked")
            return ("host", 443, ["1.2.3.4"])

        with patch("app.engines.synthetic.assert_safe_url", side_effect=_checker):
            with pytest.raises(ValidationError, match="step 1"):
                validate_steps([GOTO, {"action": "goto", "url": "http://internal.corp/"}])

        assert len(calls) == 2

    def test_non_string_url_is_rejected(self):
        with pytest.raises(ValidationError, match="'url' must be a string"):
            validate_steps([{"action": "goto", "url": ["https://example.com"]}])


class TestSecretRedaction:
    def test_secret_values_are_masked(self):
        steps = [
            GOTO,
            {"action": "fill", "selector": "#email", "value": "bot@example.com"},
            {"action": "fill", "selector": "#password", "value": "hunter2", "secret": True},
        ]

        redacted = redact_steps(steps)

        assert redacted[2]["value"] == REDACTED
        # Non-secret values stay readable — the user needs to see what it types.
        assert redacted[1]["value"] == "bot@example.com"

    def test_redaction_does_not_mutate_the_stored_steps(self):
        steps = [{"action": "fill", "selector": "#p", "value": "hunter2", "secret": True}]

        redact_steps(steps)

        assert steps[0]["value"] == "hunter2"

    def test_secret_flag_survives_redaction(self):
        steps = [{"action": "fill", "selector": "#p", "value": "hunter2", "secret": True}]

        assert redact_steps(steps)[0]["secret"] is True

    def test_empty_and_none_are_handled(self):
        assert redact_steps(None) == []
        assert redact_steps([]) == []
