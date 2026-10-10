"""API routes for lead ingestion, scoring, and retrieval.
Unified pipeline: all leads now go through the Graph Engine (WorkflowGraphEngine).
The old process_inbound_lead() background function is REPLACED by run_pipeline_for_lead_bg().
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.graph.engine import workflow_graph
from app.models import Lead
from app.schemas import LeadCreate, LeadResponse
from app.services.notifier import broadcast_ws

logger = logging.getLogger("leads_router")
router = APIRouter(prefix="/api/leads", tags=["Leads"])

# H-5 Fix: per-route rate limits (gracefully degraded if slowapi not installed)
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
    def rate_limit(limit_value: str):
        return _limiter.limit(limit_value)
except ImportError:
    def rate_limit(limit_value: str):
        def decorator(f):
            return f
        return decorator


@router.post("", response_model=LeadResponse)
@rate_limit("10/minute")
async def create_lead(
    request: Request,
    payload: LeadCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Inbound lead intake — web forms, webhooks, WhatsApp bridge, Hermes integrations.
    Rate-limited: 10 submissions per minute per IP.
    """
    lead = Lead(
        name=payload.name,
        company=payload.company,
        email=payload.email,
        phone=payload.phone,
        source=payload.source or "website",
        requirement=payload.requirement,
        budget=payload.budget or "Flexible",
        timeline=payload.timeline or "Immediate",
        status="NEW",
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    # Broadcast lead receipt immediately to dashboard
    await broadcast_ws("LEAD_RECEIVED", {"lead_id": lead.id, "name": lead.name, "source": lead.source})

    # Run the unified Graph Engine pipeline (Dedup → Qualify → Budget → Plan → Gate)
    # Uses run_pipeline_for_lead_bg which opens its own DB session safely
    background_tasks.add_task(workflow_graph.run_pipeline_for_lead_bg, lead.id)

    # Re-fetch with selectin-loaded relationships for the response
    q = await db.execute(select(Lead).where(Lead.id == lead.id))
    return q.scalar_one()


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    temperature: str | None = Query(None, description="Filter: HOT | WARM | COLD"),
    status: str | None = Query(None, description="Filter by status string"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List leads with optional temperature/status filters."""
    query = select(Lead).order_by(desc(Lead.created_at)).limit(limit)
    if temperature:
        query = query.where(Lead.temperature == temperature.upper())
    if status:
        query = query.where(Lead.status == status.upper())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    """Get single lead details with embedded plan and builds."""
    q = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = q.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/{lead_id}/generate-plan", response_model=LeadResponse)
async def generate_plan_for_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    """Manually re-trigger plan generation for any lead (re-runs architect node only)."""
    import json

    from app.models import ProjectGoalPlan
    from app.services.ai_engine import generate_project_plan
    from app.services.notifier import notify_plan_approval_request

    q = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = q.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_dict = {
        "id": lead.id, "name": lead.name, "company": lead.company,
        "email": lead.email, "phone": lead.phone,
        "requirement": lead.requirement, "budget": lead.budget, "timeline": lead.timeline,
    }
    analysis = json.loads(lead.ai_analysis) if lead.ai_analysis else {}
    plan_data = await generate_project_plan(lead_dict, analysis)

    # Upsert plan
    pq = await db.execute(
        select(ProjectGoalPlan)
        .where(ProjectGoalPlan.lead_id == lead.id)
        .order_by(ProjectGoalPlan.id.desc())
    )
    plan = pq.scalar_one_or_none()
    if not plan:
        plan = ProjectGoalPlan(lead_id=lead.id)
        db.add(plan)

    plan.title = plan_data.get("title", f"{lead.name} Project")
    plan.target_deliverable = plan_data.get("target_deliverable", "Full-Stack Application")
    plan.core_features = json.dumps(plan_data.get("core_features", []))
    plan.tech_stack = json.dumps(plan_data.get("tech_stack", []))
    plan.architecture_overview = plan_data.get("architecture_overview", "")
    plan.implementation_steps = json.dumps(plan_data.get("implementation_steps", []))
    plan.approval_status = "PENDING"
    lead.status = "PLAN_PENDING"
    await db.commit()
    await db.refresh(lead)

    await notify_plan_approval_request(lead_dict, plan_data)
    await broadcast_ws("LEAD_SCORED", {"lead_id": lead.id, "temperature": lead.temperature, "has_plan": True})

    q2 = await db.execute(select(Lead).where(Lead.id == lead.id))
    return q2.scalar_one()
