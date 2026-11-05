"""Prometheus metrics for the API and the worker.

Labels are kept to a small, fixed set of values (route templates, job names,
outcomes, agents), never ids: one series per organisation or run would grow
without bound, and a metrics endpoint is not the place to say which tenants
exist. Per-organisation spend lives in Postgres (the Usage & Budget screen).

The API serves these at `/metrics`; the worker on its own port
(`METRICS_PORT`), since it has no HTTP server of its own.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

REGISTRY = CollectorRegistry(auto_describe=True)

# Requests answer in milliseconds; reviews take seconds (a hosted model) to
# many minutes (a local one on a laptop CPU).
_REQUEST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_JOB_BUCKETS = (0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600)

HTTP_REQUESTS = Counter(
    "revu_http_requests_total",
    "API requests answered, by route template and status code.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "revu_http_request_duration_seconds",
    "Time to answer an API request.",
    ("method", "route"),
    buckets=_REQUEST_BUCKETS,
    registry=REGISTRY,
)
JOBS = Counter(
    "revu_jobs_total",
    "Worker job attempts, by job and how the attempt ended.",
    ("job", "outcome"),
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "revu_job_duration_seconds",
    "Time a worker job attempt took.",
    ("job",),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
REVIEWS = Counter(
    "revu_reviews_total",
    "Reviews finished, by agent and outcome "
    "(succeeded, failed, retried, interrupted, over_budget).",
    ("agent", "outcome"),
    registry=REGISTRY,
)
REVIEW_DURATION = Histogram(
    "revu_review_duration_seconds",
    "Model time a successful review took.",
    ("agent",),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
REVIEW_TOKENS = Counter(
    "revu_review_tokens_total",
    "Model tokens used by reviews.",
    ("direction",),
    registry=REGISTRY,
)
REVIEW_COST = Counter(
    "revu_review_cost_usd_total",
    "Provider cost of reviews in US dollars (0 for local models).",
    registry=REGISTRY,
)
REVIEW_FINDINGS = Counter(
    "revu_review_findings_total",
    "Findings reported by reviews, by severity.",
    ("severity",),
    registry=REGISTRY,
)
QUEUE_DEPTH = Gauge(
    "revu_queue_depth",
    "Jobs waiting in the worker queue (read when the API is scraped).",
    registry=REGISTRY,
)


def render() -> tuple[bytes, str]:
    """The current values in Prometheus' text format, and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def serve(port: int, addr: str = "0.0.0.0") -> None:
    """Serve `/metrics` on a background thread (the worker's exporter)."""
    start_http_server(port, addr=addr, registry=REGISTRY)
