"""Stats, activity log, and daily digest routes."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ActivityLog, Lead, ProjectBuild
from app.schemas import ActivityLogResponse, StatsResponse

router = APIRouter(prefix="/api", tags=["Stats"])


@router.get("/stats", response_model=StatsResponse)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Calculate platform metrics.
    Uses aggregate SQL queries instead of loading all rows into memory.
    """
    # FIX: Use func.count() — O(1) SQL, not O(N) Python
    total       = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
    hot         = (await db.execute(select(func.count(Lead.id)).where(Lead.temperature == "HOT"))).scalar() or 0
    warm        = (await db.execute(select(func.count(Lead.id)).where(Lead.temperature == "WARM"))).scalar() or 0
    cold        = (await db.execute(select(func.count(Lead.id)).where(Lead.temperature == "COLD"))).scalar() or 0
    pending     = (await db.execute(select(func.count(Lead.id)).where(Lead.status.in_(["PLAN_PENDING", "PLAN_APPROVED"])))).scalar() or 0
    built_count = (await db.execute(select(func.count(ProjectBuild.id)).where(ProjectBuild.status == "COMPLETED"))).scalar() or 0

    # Pipeline value — FIX: strip commas first before regex (₹35,000 now parses as 35000 not 35)
    budgets_q = await db.execute(select(Lead.budget).where(Lead.budget.isnot(None)))
    budgets = budgets_q.scalars().all()
    pipeline_val = 0
    for b in budgets:
        cleaned = (b or "").replace(",", "")
        nums = [int(n) for n in re.findall(r"\d+", cleaned)]
        if nums:
            pipeline_val += max(nums)
    formatted_val = f"₹{pipeline_val:,}" if pipeline_val > 0 else "₹0"

    return StatsResponse(
        total_leads=total,
        hot_deals=hot,
        warm_leads=warm,
        cold_leads=cold,
        pending_approvals=pending,
        built_projects=built_count,
        estimated_pipeline_value=formatted_val,
    )


@router.get("/activity", response_model=list[ActivityLogResponse])
async def get_recent_activity(db: AsyncSession = Depends(get_db)):
    """Get latest 25 activity log entries ordered by most recent."""
    q = await db.execute(
        select(ActivityLog).order_by(desc(ActivityLog.created_at)).limit(25)
    )
    return q.scalars().all()
