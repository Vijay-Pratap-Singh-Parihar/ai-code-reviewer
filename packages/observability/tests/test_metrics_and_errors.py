"""The metrics registry and what reaches Sentry."""

from observability import bound, init_error_reporting, metrics
from observability.errors import before_breadcrumb, before_send


def test_metrics_render_in_prometheus_text_format() -> None:
    metrics.REVIEWS.labels("diff_only", "succeeded").inc()

    body, content_type = metrics.render()

    assert content_type.startswith("text/plain")
    assert b'revu_reviews_total{agent="diff_only",outcome="succeeded"}' in body


def test_error_reporting_is_off_without_a_dsn() -> None:
    assert init_error_reporting(dsn="", service="api", environment="test") is False


def test_events_carry_the_request_context_as_tags() -> None:
    with bound(request_id="req-1", run_id="run-1", org_id="org-1", user_email="a@b.c"):
        event = before_send({"exception": {"values": []}}, {})

    assert event["tags"] == {"request_id": "req-1", "run_id": "run-1", "org_id": "org-1"}


def test_credentials_and_local_variables_never_reach_sentry() -> None:
    key = "sk-ant-" + "x" * 30
    event = {
        "message": f"call failed with {key}",
        "logentry": {"message": "provider said %s", "params": [f"Bearer {'t' * 30}"]},
        "exception": {
            "values": [
                {
                    "type": "AuthenticationError",
                    "value": f"invalid api key {key}",
                    "stacktrace": {"frames": [{"function": "complete", "vars": {"key": key}}]},
                }
            ]
        },
    }

    sent = str(before_send(event, {}))

    assert key not in sent and "t" * 30 not in sent
    assert "vars" not in sent


def test_breadcrumbs_are_masked() -> None:
    crumb = before_breadcrumb({"message": "token=ghs_" + "a" * 36}, {})
    assert "a" * 36 not in crumb["message"]
