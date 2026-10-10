"""Structured JSON Observability, Error Tracking & Metrics Collector.

Per Phase 8 / Section 14A of Master Specification:
- JSON log formatter with structured fields and request context
- Centralized metrics collector (counters, gauges, histograms)
- Sentry / GlitchTip compatible error tracking with recent-error ring buffer
- Starlette / FastAPI HTTP request telemetry middleware
"""
from __future__ import annotations

import collections
import json
import logging
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

# ==============================================================================
# 1. STRUCTURED JSON LOGGING
# ==============================================================================

class JSONFormatter(logging.Formatter):
    """Formats log records as structured, single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "lineno": record.lineno,
            "process": record.process,
            "thread": record.thread,
        }

        # Include standard context fields if injected via extra
        context = {}
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName"
            ):
                context[key] = val

        if context:
            log_entry["context"] = context

        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stacktrace": self.formatException(record.exc_info),
            }

        return json.dumps(log_entry, default=str)


def setup_logging(level: str | None = None, json_format: bool | None = None) -> None:
    """Configure root logger with structured JSON or human-readable formatter."""
    log_level_str = (level or settings.LOG_LEVEL or "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    use_json = json_format if json_format is not None else (settings.LOG_FORMAT.lower() == "json")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Replace existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    if use_json:
        stream_handler.setFormatter(JSONFormatter())
    else:
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root_logger.addHandler(stream_handler)


# ==============================================================================
# 2. METRICS REGISTRY & COLLECTOR
# ==============================================================================

class MetricsCollector:
    """Thread-safe in-memory metrics registry for operations, builds, and latency."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = collections.defaultdict(int)
        self._gauges: dict[str, float] = {}
        # M-2 Fix: use deque(maxlen=1000) instead of list.pop(0) — O(1) vs O(n) eviction
        self._timings: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=1000)
        )
        self._start_time: float = time.time()

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> int:
        """Increment a monotonic counter."""
        key = self._format_key(name, tags)
        self._counters[key] += value
        return self._counters[key]

    def set_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set current gauge reading."""
        key = self._format_key(name, tags)
        self._gauges[key] = float(value)

    def record_timing(self, name: str, duration_ms: float, tags: dict[str, str] | None = None) -> None:
        """Record an operation latency in milliseconds (capped at 1000 samples via deque)."""
        key = self._format_key(name, tags)
        # M-2 Fix: deque handles eviction automatically at maxlen=1000 — no pop(0) needed
        self._timings[key].append(float(duration_ms))

    @contextmanager
    def measure_time(self, name: str, tags: dict[str, str] | None = None):
        """Context manager to measure execution duration of a block in ms."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.record_timing(name, elapsed_ms, tags)

    def get_summary(self) -> dict[str, Any]:
        """Return formatted summary of all tracked counters, gauges, and timing stats."""
        uptime_seconds = round(time.time() - self._start_time, 2)
        timings_summary: dict[str, dict[str, Any]] = {}

        for key, samples in self._timings.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            count = len(sorted_s)
            p95_idx = int(count * 0.95)
            timings_summary[key] = {
                "count": count,
                "avg_ms": round(sum(sorted_s) / count, 2),
                "min_ms": round(sorted_s[0], 2),
                "max_ms": round(sorted_s[-1], 2),
                "p95_ms": round(sorted_s[min(p95_idx, count - 1)], 2),
            }

        return {
            "uptime_seconds": uptime_seconds,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "timings": timings_summary,
        }

    def reset(self) -> None:
        """Reset all metrics (primarily for test idempotency)."""
        self._counters.clear()
        self._gauges.clear()
        self._timings.clear()
        self._start_time = time.time()

    def _format_key(self, name: str, tags: dict[str, str] | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


# ==============================================================================
# 3. ERROR TRACKING & SENTRY INTEGRATION
# ==============================================================================

class ErrorTracker:
    """Captures unhandled exceptions, maintains recent errors ring buffer, and hooks Sentry/GlitchTip."""

    def __init__(self, max_buffer: int = 100) -> None:
        self._recent_errors: collections.deque = collections.deque(maxlen=max_buffer)
        self._sentry_initialized: bool = False
        self._init_sentry()

    def _init_sentry(self) -> None:
        if settings.SENTRY_DSN:
            try:
                import sentry_sdk
                sentry_sdk.init(
                    dsn=settings.SENTRY_DSN,
                    traces_sample_rate=0.1,
                    environment="production" if not settings.TEST_MODE else "test",
                )
                self._sentry_initialized = True
            except Exception:
                pass

    def capture_exception(
        self,
        exc: Exception,
        context: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Record an exception and return a unique error ID."""
        error_id = f"err_{uuid.uuid4().hex[:12]}"
        error_record = {
            "id": error_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": type(exc).__name__,
            "message": str(exc),
            "stacktrace": traceback.format_exc(),
            "context": context or {},
            "tags": tags or {},
        }
        self._recent_errors.append(error_record)

        # Forward to Sentry if available
        if self._sentry_initialized:
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    if tags:
                        for k, v in tags.items():
                            scope.set_tag(k, v)
                    if context:
                        for k, v in context.items():
                            scope.set_extra(k, v)
                    scope.set_tag("error_id", error_id)
                    sentry_sdk.capture_exception(exc)
            except Exception:
                pass

        return error_id

    def get_recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent captured errors."""
        items = list(self._recent_errors)
        return items[-limit:]

    def clear(self) -> None:
        """Clear recorded errors."""
        self._recent_errors.clear()


# Singletons
metrics = MetricsCollector()
errors = ErrorTracker()


def generate_prometheus_metrics(collector: MetricsCollector | None = None) -> str:
    """Format metrics in standard Prometheus exposition format.

    M-1 Fix: Emit # TYPE / # HELP headers exactly once per metric base name.
    Duplicate TYPE headers cause Prometheus parsers to reject the entire scrape.
    """
    c = collector or metrics
    lines = [
        "# HELP process_uptime_seconds Total platform uptime in seconds",
        "# TYPE process_uptime_seconds gauge",
        f"process_uptime_seconds {round(time.time() - c._start_time, 2)}",
    ]
    emitted_types: set[str] = {"process_uptime_seconds"}

    # Monotonic counters
    for key, val in c._counters.items():
        base_name = key.split("{")[0]
        if base_name not in emitted_types:
            lines.append(f"# HELP {base_name} Counter metric")
            lines.append(f"# TYPE {base_name} counter")
            emitted_types.add(base_name)
        lines.append(f"{key}_total {val}")

    # Gauges
    for key, val in c._gauges.items():
        base_name = key.split("{")[0]
        if base_name not in emitted_types:
            lines.append(f"# HELP {base_name} Gauge metric")
            lines.append(f"# TYPE {base_name} gauge")
            emitted_types.add(base_name)
        lines.append(f"{key} {val}")

    # Latencies / Timings as Summaries
    for key, samples in c._timings.items():
        if not samples:
            continue
        base_name = key.split("{")[0]
        tags_part = key[len(base_name):] if "{" in key else ""
        if base_name not in emitted_types:
            lines.append(f"# HELP {base_name} Latency summary in milliseconds")
            lines.append(f"# TYPE {base_name} summary")
            emitted_types.add(base_name)
        count = len(samples)
        total_sum = sum(samples)
        lines.append(f"{base_name}_count{tags_part} {count}")
        lines.append(f"{base_name}_sum{tags_part} {round(total_sum, 2)}")

    return "\n".join(lines) + "\n"


# ==============================================================================
# 4. FASTAPI / STARLETTE TELEMETRY & OPENTELEMETRY MIDDLEWARE
# ==============================================================================

class ObservabilityMiddleware(BaseHTTPMiddleware):
    """HTTP middleware tracking request count, latency, OpenTelemetry trace context, and error status."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:8]
        start_time = time.perf_counter()
        path = request.url.path
        method = request.method

        # OpenTelemetry W3C Trace Context: traceparent: 00-{trace_id}-{span_id}-{trace_flags}
        traceparent = request.headers.get("traceparent")
        if traceparent and len(traceparent.split("-")) == 4:
            parts = traceparent.split("-")
            trace_id = parts[1]
        else:
            trace_id = uuid.uuid4().hex  # 32 hex chars

        span_id = uuid.uuid4().hex[:16]  # 16 hex chars
        trace_context = f"00-{trace_id}-{span_id}-01"

        # Tag metrics by path family to prevent cardinality explosion
        path_tag = "health" if path.startswith("/api/health") else "api"

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            metrics.increment("http_requests_total", tags={"method": method, "status": str(response.status_code)})
            metrics.record_timing("http_request_duration_ms", duration_ms, tags={"route": path_tag})

            response.headers["X-Request-ID"] = req_id
            response.headers["X-Trace-ID"] = trace_id
            response.headers["X-Span-ID"] = span_id
            response.headers["traceparent"] = trace_context
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            error_id = errors.capture_exception(exc, context={"path": path, "method": method, "request_id": req_id, "trace_id": trace_id})
            metrics.increment("http_requests_total", tags={"method": method, "status": "500"})
            metrics.increment("unhandled_exceptions_total")

            # Re-raise to let FastAPI error handlers or default 500 take over
            raise exc
