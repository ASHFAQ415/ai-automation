"""SystemHealthMonitorAgent — Hermes autonomous health watchdog.
Runs every 5 minutes in the background. Checks:
  1. Ollama daemon liveness (primary AI engine)
  2. Disk space for projects/ directory
  3. Stuck builds (BUILDING > 30 min with no progress)
  4. Stuck leads (PLAN_PENDING > 2 hours with no approval)
Alerts Hermes owner via Telegram + WebSocket dashboard on any issue found.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Lead, ProjectBuild
from app.services.notifier import broadcast_ws, notify_health_alert

logger = logging.getLogger("health_monitor")

_monitor_task: asyncio.Task | None = None
_INTERVAL_SECONDS = 300  # 5 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _check_ollama() -> tuple[bool, str]:
    """Verify the local Ollama daemon is reachable."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                has_model = any(settings.OLLAMA_MODEL in m for m in models)
                if has_model:
                    return True, f"Ollama online — `{settings.OLLAMA_MODEL}` loaded"
                return False, f"Ollama online but `{settings.OLLAMA_MODEL}` NOT found. Available: {', '.join(models[:3])}"
            return False, f"Ollama returned HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Ollama unreachable: {str(e)[:120]}"


async def _check_disk() -> tuple[bool, str]:
    """Check disk space for the projects directory."""
    try:
        projects_dir = settings.PROJECTS_DIR
        projects_dir.mkdir(parents=True, exist_ok=True)
        usage = _get_dir_size_mb(projects_dir)
        stat = os.statvfs(str(projects_dir)) if hasattr(os, "statvfs") else None
        if stat:
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            if free_gb < 2.0:
                return False, f"Low disk space: only {free_gb:.1f} GB free on projects drive"
            return True, f"Disk OK — {free_gb:.1f} GB free | Projects: {usage:.0f} MB used"
        return True, f"Projects directory OK — {usage:.0f} MB used"
    except Exception as e:
        return True, f"Disk check skipped (Windows): {str(e)[:80]}"


def _get_dir_size_mb(path: Path) -> float:
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total / (1024 * 1024)


async def _check_stuck_builds() -> list[str]:
    """Detect builds stuck in GENERATING_CODE/SCAFFOLDING for > 30 minutes."""
    issues = []
    cutoff = _now() - timedelta(minutes=30)
    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(ProjectBuild)
            .where(ProjectBuild.status.in_(["SCAFFOLDING", "GENERATING_CODE"]))
            .where(ProjectBuild.created_at < cutoff)
        )
        stuck = q.scalars().all()
        for build in stuck:
            issues.append(
                f"Build #{build.id} ({build.project_name}) stuck in `{build.status}` for >30 min"
            )
    return issues


async def _check_stuck_leads() -> list[str]:
    """Detect leads stuck in PLAN_PENDING for > 2 hours (approval not received)."""
    issues = []
    cutoff = _now() - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(Lead)
            .where(Lead.status == "PLAN_PENDING")
            .where(Lead.updated_at < cutoff)
        )
        stuck = q.scalars().all()
        for lead in stuck:
            issues.append(
                f"Lead #{lead.id} ({lead.name}) stuck in `PLAN_PENDING` for >2 hours — needs approval"
            )
    return issues


async def _run_health_check() -> None:
    """Run all health checks and alert Hermes if any issue is found."""
    logger.info("[HealthMonitor] Running system health check...")

    # Parallel checks
    ollama_ok, ollama_msg = await _check_ollama()
    disk_ok, disk_msg = await _check_disk()
    stuck_builds = await _check_stuck_builds()
    stuck_leads = await _check_stuck_leads()

    issues = []
    if not ollama_ok:
        issues.append(f"🔴 *Ollama Engine*: {ollama_msg}")
    if not disk_ok:
        issues.append(f"🟡 *Disk Space*: {disk_msg}")
    for s in stuck_builds:
        issues.append(f"⚠️ *Stuck Build*: {s}")
    for s in stuck_leads:
        issues.append(f"⏳ *Stuck Lead*: {s}")

    if issues:
        issue_summary = f"{len(issues)} issue(s) detected"
        details = "\n".join(issues)
        await notify_health_alert(issue_summary, details)
        logger.warning("[HealthMonitor] Issues found: %s", details)
    else:
        logger.info("[HealthMonitor] All systems nominal — Ollama: %s | Disk: %s", ollama_msg, disk_msg)
        await broadcast_ws("HEALTH_OK", {
            "ollama": ollama_msg,
            "disk": disk_msg,
            "timestamp": _now().isoformat(),
        })


async def _monitor_loop() -> None:
    """Continuous monitoring loop — runs every 5 minutes."""
    logger.info("[HealthMonitor] Hermes Health Monitor started (interval: %ds)", _INTERVAL_SECONDS)
    while True:
        try:
            await _run_health_check()
        except Exception as e:
            logger.error("[HealthMonitor] Unexpected error: %s", e)
        await asyncio.sleep(_INTERVAL_SECONDS)


def start_health_monitor() -> None:
    """Launch the health monitor as a background asyncio task."""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_loop())
        logger.info("[HealthMonitor] Background task launched.")


async def stop_health_monitor() -> None:
    """Gracefully stop the health monitor."""
    global _monitor_task
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None
        logger.info("[HealthMonitor] Stopped.")
