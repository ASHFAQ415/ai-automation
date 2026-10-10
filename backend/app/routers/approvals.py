"""Approval routes for Stage 1 (Goals & Plan) and Stage 2 (Autonomous Build).
Integrated with WorkflowGraphEngine for state synchronization.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.dependencies import require_api_key
from app.graph.engine import workflow_graph
from app.models import ActivityLog, Lead, ProjectBuild, ProjectGoalPlan
from app.schemas import BuildTriggerRequest, PlanApprovalRequest
from app.services.notifier import broadcast_ws, notify_build_failed, send_telegram

logger = logging.getLogger("approvals_router")
router = APIRouter(prefix="/api/approvals", tags=["Approvals"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _run_build_task(lead_id: int, lead_name: str, project_name: str, custom_instructions: str = None) -> None:
    """Background task: run build sub-graph (Builder → Verifier → Delivery).
    Reports failures to Hermes owner via Telegram (was previously silent).
    """
    async with AsyncSessionLocal() as db:
        try:
            state = await workflow_graph.initialize_lead_state(lead_id, db)
            if custom_instructions:
                state.custom_instructions = custom_instructions
            await workflow_graph.execute_build_subgraph(lead_id, db)
        except Exception as e:
            logger.error("Build execution failed for lead %s: %s", lead_id, e, exc_info=True)
            # FIX: was silently swallowing exception — now alerts Hermes owner
            await notify_build_failed(
                {"id": lead_id, "name": lead_name},
                project_name,
                str(e),
            )


@router.post("/{lead_id}/plan", dependencies=[Depends(require_api_key)])
async def review_plan(
    lead_id: int,
    payload: PlanApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Stage 1: Approve or Reject the Project Goals & Implementation Plan. Requires API key."""
    lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_q.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    plan_q = await db.execute(
        select(ProjectGoalPlan)
        .where(ProjectGoalPlan.lead_id == lead_id)
        .order_by(desc(ProjectGoalPlan.id))
    )
    plan = plan_q.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Implementation plan not found — run /generate-plan first")

    decision = payload.decision.upper()  # already validated by Literal schema

    plan.approval_status = decision
    if decision == "APPROVED":
        plan.approved_at = _now()
        lead.status = "PLAN_APPROVED"
        log_title = f"Plan APPROVED for {lead.name}"
        tg_text = (
            f"✅ *PLAN APPROVED*\n"
            f"Project: *{plan.title}*\n"
            f"Client: {lead.name}\n"
            f"Ready for autonomous build. Use `/build {lead_id}` to launch."
        )
    else:
        lead.status = "REJECTED"
        log_title = f"Plan REJECTED for {lead.name}"
        tg_text = (
            f"❌ *PLAN REJECTED*\n"
            f"Project: *{plan.title}*\n"
            f"Feedback: {payload.feedback or 'No feedback provided.'}"
        )

    log = ActivityLog(
        lead_id=lead.id,
        event_type="PLAN_REVIEWED",
        title=log_title,
        details=payload.feedback or "Reviewed via API / Dashboard",
        channel="DASHBOARD",
    )
    db.add(log)
    await db.commit()

    # Sync graph engine state
    await workflow_graph.handle_plan_approval(lead_id, decision, payload.feedback, db)

    await send_telegram(tg_text)
    await broadcast_ws("PLAN_REVIEWED", {"lead_id": lead.id, "decision": decision})
    return {"status": "success", "lead_status": lead.status, "plan_status": plan.approval_status}


@router.post("/{lead_id}/build", dependencies=[Depends(require_api_key)])
async def trigger_build(
    lead_id: int,
    background_tasks: BackgroundTasks,
    payload: BuildTriggerRequest = BuildTriggerRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Stage 2: Authorize and start autonomous code generation & build. Requires API key."""
    lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_q.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    plan_q = await db.execute(
        select(ProjectGoalPlan)
        .where(ProjectGoalPlan.lead_id == lead_id)
        .order_by(desc(ProjectGoalPlan.id))
    )
    plan = plan_q.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=400, detail="No implementation plan — run generate-plan first")
    if plan.approval_status != "APPROVED":
        raise HTTPException(status_code=400, detail="Plan must be approved before building")

    # FIX: Double-build guard — prevent concurrent builds for same lead
    if lead.status == "BUILDING":
        raise HTTPException(status_code=400, detail="Build already in progress for this lead")

    # Check for any RUNNING builds in DB
    active_build_q = await db.execute(
        select(ProjectBuild)
        .where(ProjectBuild.lead_id == lead_id)
        .where(ProjectBuild.status.in_(["QUEUED", "SCAFFOLDING", "GENERATING_CODE", "VERIFYING"]))
    )
    active = active_build_q.scalar_one_or_none()
    if active:
        raise HTTPException(status_code=400, detail=f"Build #{active.id} already running for this lead")

    lead.status = "BUILDING"
    await db.commit()

    background_tasks.add_task(
        _run_build_task, lead.id, lead.name, plan.title or "Project", payload.custom_instructions
    )
    await broadcast_ws("BUILD_TRIGGERED", {"lead_id": lead_id, "project_name": plan.title})
    return {"status": "started", "message": f"Autonomous build initiated for {lead.name}"}
