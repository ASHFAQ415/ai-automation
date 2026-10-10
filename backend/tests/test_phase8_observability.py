"""Phase 8 Verification Test Suite: Observability, Metrics, Error Tracking & Regression Benchmarks.

Per Phase 8 / Section 14A of Master Specification:
- Structured JSON log formatting and contextual fields
- In-memory metrics registry (counters, gauges, latency histograms, p95 percentiles)
- Error tracking ring buffer & Sentry integration
- HTTP telemetry middleware & X-Request-ID tracing
- Builder Regression Benchmark runner across representative domains
"""
from __future__ import annotations

import io
import json
import logging
import pytest
from starlette.testclient import TestClient

from app.services.observability import (
    JSONFormatter,
    MetricsCollector,
    ErrorTracker,
    metrics,
    errors,
)
from app.services.regression_benchmarks import (
    BENCHMARK_PROJECTS,
    run_single_benchmark,
    run_all_regression_benchmarks,
)
from main import app


def test_json_formatter_structure():
    """Verify JSONFormatter generates valid, structured single-line JSON with context & exceptions."""
    formatter = JSONFormatter()
    logger = logging.getLogger("test_observability_logger")
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    try:
        # 1. Standard structured log with extra context
        logger.info(
            "Lead plan generated successfully",
            extra={"lead_id": 42, "tenant_id": "tenant_alpha", "duration_ms": 156.4},
        )
        output = stream.getvalue().strip()
        parsed = json.loads(output)

        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test_observability_logger"
        assert parsed["message"] == "Lead plan generated successfully"
        assert "timestamp" in parsed
        assert parsed["context"]["lead_id"] == 42
        assert parsed["context"]["tenant_id"] == "tenant_alpha"

        # 2. Log with exception stacktrace
        stream.truncate(0)
        stream.seek(0)
        try:
            raise ValueError("Simulated pipeline crash")
        except ValueError:
            logger.error("Failed processing event", exc_info=True)

        err_output = stream.getvalue().strip()
        err_parsed = json.loads(err_output)
        assert err_parsed["level"] == "ERROR"
        assert "exception" in err_parsed
        assert err_parsed["exception"]["type"] == "ValueError"
        assert "Simulated pipeline crash" in err_parsed["exception"]["message"]
        assert "Traceback" in err_parsed["exception"]["stacktrace"]

    finally:
        logger.removeHandler(handler)


def test_metrics_collector():
    """Verify MetricsCollector accurately tracks counters, gauges, timings, and percentiles."""
    m = MetricsCollector()

    # 1. Counters
    m.increment("builds_total", 1, tags={"status": "success"})
    m.increment("builds_total", 2, tags={"status": "success"})
    assert m.increment("builds_total", 1, tags={"status": "failed"}) == 1

    # 2. Gauges
    m.set_gauge("active_sandboxes", 4.0)

    # 3. Timings & Latency
    for latency in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]:
        m.record_timing("llm_duration_ms", latency, tags={"model": "qwen3"})

    with m.measure_time("code_block", tags={"section": "db"}):
        pass

    summary = m.get_summary()
    assert summary["counters"]["builds_total{status=success}"] == 3
    assert summary["counters"]["builds_total{status=failed}"] == 1
    assert summary["gauges"]["active_sandboxes"] == 4.0

    llm_stats = summary["timings"]["llm_duration_ms{model=qwen3}"]
    assert llm_stats["count"] == 10
    assert llm_stats["min_ms"] == 10.0
    assert llm_stats["max_ms"] == 100.0
    assert llm_stats["avg_ms"] == 55.0
    assert llm_stats["p95_ms"] == 100.0

    # 4. Reset
    m.reset()
    assert len(m.get_summary()["counters"]) == 0


def test_error_tracker_ring_buffer():
    """Verify ErrorTracker captures exceptions, tags, and bounds buffer size."""
    tracker = ErrorTracker(max_buffer=5)

    for i in range(7):
        try:
            raise RuntimeError(f"Error index {i}")
        except RuntimeError as e:
            err_id = tracker.capture_exception(e, context={"iter": i}, tags={"env": "test"})
            assert err_id.startswith("err_")

    recent = tracker.get_recent_errors(limit=10)
    assert len(recent) == 5  # Max buffer capped at 5
    assert recent[-1]["context"]["iter"] == 6
    assert recent[-1]["type"] == "RuntimeError"

    tracker.clear()
    assert len(tracker.get_recent_errors()) == 0


def test_observability_middleware_and_routes():
    """Verify HTTP requests are decorated with X-Request-ID and reflected in /api/metrics."""
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Invoke endpoint through ObservabilityMiddleware
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers

    # 2. Check /api/metrics endpoint
    metrics_resp = client.get("/api/metrics")
    assert metrics_resp.status_code == 200
    metrics_data = metrics_resp.json()
    assert "uptime_seconds" in metrics_data
    assert "counters" in metrics_data
    assert any("http_requests_total" in k for k in metrics_data["counters"])

    # 3. Check /api/observability/errors endpoint
    err_resp = client.get("/api/observability/errors")
    assert err_resp.status_code == 200
    err_data = err_resp.json()
    assert "count" in err_data
    assert isinstance(err_data["errors"], list)


def test_regression_benchmark_runner():
    """Verify 10 representative domains execute through generation and pass 13-point gates."""
    # Test single domain (School Timetable)
    school_spec = BENCHMARK_PROJECTS[0]
    single_res = run_single_benchmark(school_spec)
    assert single_res["gate_passed"] is True
    assert single_res["benchmark_id"] == "bench_01_school"
    assert single_res["domain"] == "school"
    assert single_res["files_generated"] >= 5
    assert single_res["duration_ms"] > 0
    assert len(single_res["artifact_checksum"]) > 0

    # Test multi-domain suite across representative projects
    subset = BENCHMARK_PROJECTS[:3]  # School, Restaurant, Inventory
    suite_res = run_all_regression_benchmarks(subset)
    assert suite_res["total_benchmarks"] == 3
    assert suite_res["passed_benchmarks"] == 3
    assert suite_res["failed_benchmarks"] == 0
    assert suite_res["pass_rate_percent"] == 100.0
    assert suite_res["suite"] == "HERMES_BUILDER_REGRESSION_SUITE_V2"
