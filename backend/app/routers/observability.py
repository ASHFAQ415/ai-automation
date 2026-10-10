"""Observability API routes: Prometheus/JSON metrics and runtime error inspector.

C-1 Fix: Mutating endpoints (reset/clear) protected by API key authentication.
L-4 Fix: Wipe operations logged to audit trail.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.dependencies import require_api_key
from app.services.observability import errors, metrics, generate_prometheus_metrics

logger = logging.getLogger("observability_router")
router = APIRouter(prefix="/api", tags=["Observability"])


@router.get("/metrics")
async def get_metrics() -> dict[str, Any]:
    """Retrieve runtime operational metrics, counters, and latency percentiles (JSON format)."""
    return metrics.get_summary()


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def get_prometheus_metrics() -> str:
    """Retrieve runtime metrics in standard Prometheus exposition format."""
    return generate_prometheus_metrics(metrics)


@router.post("/metrics/reset", dependencies=[Depends(require_api_key)])
async def reset_metrics() -> dict[str, str]:
    """Reset operational metrics. Requires API key. Action is audit-logged."""
    # L-4 Fix: record wipe action before clearing
    logger.warning(
        "[AUDIT] metrics/reset called at %s — all operational counters wiped.",
        datetime.now(timezone.utc).isoformat(),
    )
    metrics.reset()
    return {"status": "METRICS_RESET"}


@router.get("/observability/errors")
async def get_recent_errors(limit: int = 50) -> dict[str, Any]:
    """Inspect recent unhandled exceptions captured by ErrorTracker."""
    records = errors.get_recent_errors(limit=limit)
    return {
        "count": len(records),
        "errors": records,
    }


@router.post("/observability/errors/clear", dependencies=[Depends(require_api_key)])
async def clear_recent_errors() -> dict[str, str]:
    """Clear captured runtime error log. Requires API key. Action is audit-logged."""
    # L-4 Fix: record wipe action before clearing
    logger.warning(
        "[AUDIT] observability/errors/clear called at %s — error ring buffer wiped.",
        datetime.now(timezone.utc).isoformat(),
    )
    errors.clear()
    return {"status": "ERRORS_CLEARED"}
