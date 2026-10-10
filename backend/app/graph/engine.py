"""Graph Engineering Execution Engine & Orchestrator.
Unified pipeline: lead intake → dedup → qualify → budget → plan → gate → build → verify → deliver.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.graph.nodes import (
    AIQualifierNode,
    BudgetEstimatorNode,
    CodeVerifierNode,
    DeliveryDispatcherNode,
    LeadDeduplicationNode,
    PlanArchitectNode,
    ProjectBuilderNode,
    Stage1ApprovalGateNode,
)
from app.graph.state import LeadGraphState, NodeID, WorkflowStatus
from app.models import Lead, ProjectBuild, ProjectGoalPlan

logger = logging.getLogger("graph_engine")

# In-memory graph state registry for live execution tracing
_active_states: dict[int, LeadGraphState] = {}

# BUG #15 FIX: Removed generic English nouns ('system', 'platform') that caused
# false positives. Requires concrete action verbs or specific tech nouns.
_BUILD_KEYWORDS = re.compile(
    r"\b(build|develop|create a|automate|portal|dashboard|bot|software|app|website|web app)\b",
    re.IGNORECASE,
)


class WorkflowGraphEngine:
    """Deterministic DAG for Hermes Autonomous Lead-to-Code Pipelines."""

    def __init__(self):
        self.dedup_node    = LeadDeduplicationNode()
        self.qualifier_node = AIQualifierNode()
        self.budget_node   = BudgetEstimatorNode()
        self.architect_node = PlanArchitectNode()
        self.stage1_gate   = Stage1ApprovalGateNode()
        self.builder_node  = ProjectBuilderNode()
        self.verifier_node = CodeVerifierNode()
        self.delivery_node = DeliveryDispatcherNode()

    def get_topology(self) -> dict[str, Any]:
        """Return the complete DAG specification for Hermes reviewers and dashboards."""
        return {
            "graph_name": "Hermes Autonomous Lead Qualification & Software Synthesis DAG",
            "version": "3.0.0",
            "nodes": [
                {"id": NodeID.LEAD_INGESTION.value,       "name": "Lead Ingestion Node",             "type": "Ingress"},
                {"id": NodeID.LEAD_DEDUP.value,            "name": "Lead Deduplication Agent",         "type": "Validation Gate"},
                {"id": NodeID.AI_QUALIFIER.value,          "name": "AI Qualification & Scoring Agent", "type": "Compute", "engine": "Local Qwen3:4b (RTX 3050)"},
                {"id": NodeID.BUDGET_ESTIMATOR.value,      "name": "Budget Estimator Agent",           "type": "Enrichment", "engine": "Local Qwen3:4b"},
                {"id": NodeID.PLAN_ARCHITECT.value,        "name": "Technical Architecture Agent",     "type": "Compute", "engine": "Local Qwen3:4b"},
                {"id": NodeID.STAGE1_APPROVAL_GATE.value,  "name": "Stage 1 Plan Review Gate",        "type": "Human-in-the-loop", "channels": ["Telegram @Preetikaa_bot", "Discord", "Web Dashboard"]},
                {"id": NodeID.STAGE2_BUILD_GATE.value,     "name": "Stage 2 Build Authorization Gate","type": "Human-in-the-loop"},
                {"id": NodeID.PROJECT_BUILDER.value,       "name": "Autonomous Project Builder",       "type": "Synthesis", "engine": "Qwen3:4b Compiler"},
                {"id": NodeID.CODE_VERIFIER.value,         "name": "Code Verifier & Self-Healing",     "type": "Verification"},
                {"id": NodeID.DELIVERY_DISPATCHER.value,   "name": "Multi-Channel Delivery Dispatcher","type": "Egress"},
            ],
            "edges": [
                {"from": NodeID.LEAD_INGESTION.value,      "to": NodeID.LEAD_DEDUP.value,            "type": "Deterministic"},
                {"from": NodeID.LEAD_DEDUP.value,           "to": NodeID.AI_QUALIFIER.value,          "type": "Conditional", "condition": "Not duplicate"},
                {"from": NodeID.AI_QUALIFIER.value,         "to": NodeID.BUDGET_ESTIMATOR.value,      "type": "Deterministic"},
                {"from": NodeID.BUDGET_ESTIMATOR.value,     "to": NodeID.PLAN_ARCHITECT.value,        "type": "Conditional", "condition": "Score >= 70 OR explicit build request"},
                {"from": NodeID.PLAN_ARCHITECT.value,       "to": NodeID.STAGE1_APPROVAL_GATE.value,  "type": "Deterministic"},
                {"from": NodeID.STAGE1_APPROVAL_GATE.value, "to": NodeID.STAGE2_BUILD_GATE.value,     "type": "Human Decision"},
                {"from": NodeID.STAGE2_BUILD_GATE.value,    "to": NodeID.PROJECT_BUILDER.value,       "type": "Human Trigger"},
                {"from": NodeID.PROJECT_BUILDER.value,      "to": NodeID.CODE_VERIFIER.value,         "type": "Deterministic"},
                {"from": NodeID.CODE_VERIFIER.value,        "to": NodeID.DELIVERY_DISPATCHER.value,   "type": "Deterministic"},
            ],
        }

    async def initialize_lead_state(
        self, lead_id: int, db: AsyncSession, force_reload: bool = False
    ) -> LeadGraphState:
        """Fetch or build the graph state for a lead from the DB.

        BUG #2 FIX: Added force_reload param. Without it, a failed pipeline retry
        would reuse stale cached state instead of reading fresh data from the DB.
        """
        if lead_id in _active_states and not force_reload:
            return _active_states[lead_id]

        q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = q.scalar_one_or_none()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        plan_q = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_q.scalar_one_or_none()

        build_q = await db.execute(
            select(ProjectBuild)
            .where(ProjectBuild.lead_id == lead_id)
            .order_by(ProjectBuild.id.desc())
        )
        build = build_q.scalar_one_or_none()

        state = LeadGraphState(
            lead_id=lead.id,
            client_name=lead.name,
            email=lead.email,
            phone=lead.phone,
            requirement=lead.requirement,
            budget=lead.budget,
            timeline=lead.timeline,
            company=lead.company,
            source=lead.source,
            score=lead.score or 0,
            temperature=lead.temperature or "WARM",
            plan_id=plan.id if plan else None,
            plan_title=plan.title if plan else None,
            stage1_approved=plan.approval_status == "APPROVED" if plan else False,
            build_id=build.id if build else None,
            project_path=build.project_path if build else None,
            progress_percent=build.progress_percent if build else 0,
        )
        _active_states[lead_id] = state
        return state

    async def run_pipeline_for_lead(self, lead_id: int, db: AsyncSession) -> LeadGraphState:
        """Execute DAG: Dedup → Qualify → Budget → (if qualified) Architect → Stage1 Gate."""
        state = await self.initialize_lead_state(lead_id, db)

        # Node 0: Deduplication
        state = await self.dedup_node.execute(state, db)
        if state.is_duplicate:
            _active_states[lead_id] = state
            return state   # halt pipeline — duplicate notified via Hermes

        # Node 1: AI Qualification
        state = await self.qualifier_node.execute(state, db)

        # Node 1.5: Budget Estimator (HOT only)
        state = await self.budget_node.execute(state, db)

        # Conditional Edge: Score >= 70 OR explicit build/software keyword (whole-word)
        is_build_request = bool(_BUILD_KEYWORDS.search(state.requirement))
        if state.score >= 70 or is_build_request:
            state = await self.architect_node.execute(state, db)
            state = await self.stage1_gate.execute(state, db)
        else:
            state.workflow_status = WorkflowStatus.IDLE

        _active_states[lead_id] = state
        return state

    async def run_pipeline_for_lead_bg(self, lead_id: int) -> None:
        """Background-safe wrapper — opens its own DB session.

        BUG #2 FIX: Passes force_reload=True so retries never reuse stale
        in-memory state from a previously failed pipeline execution.
        """
        async with AsyncSessionLocal() as db:
            try:
                # force_reload=True ensures retries read fresh state from DB
                state = await self.initialize_lead_state(lead_id, db, force_reload=True)
                await self.run_pipeline_for_lead(lead_id, db)
            except Exception as e:
                logger.exception("Pipeline failed for Lead #%s: %s", lead_id, e)
                from app.services.notifier import send_telegram
                await send_telegram(
                    f"❌ *PIPELINE ERROR* — Lead #{lead_id}\n"
                    f"Error: `{str(e)[:300]}`\n"
                    f"Check logs and use `/leads` to review."
                )

    async def handle_plan_approval(self, lead_id: int, decision: str, feedback: str | None, db: AsyncSession) -> LeadGraphState:
        """Process human gate decision for Stage 1."""
        state = await self.initialize_lead_state(lead_id, db)
        if decision.upper() == "APPROVED":
            state.stage1_approved = True
            state.workflow_status = WorkflowStatus.STAGE1_APPROVED
            state.approval_feedback = feedback
        else:
            state.stage1_approved = False
            state.workflow_status = WorkflowStatus.REJECTED
        _active_states[lead_id] = state
        return state

    async def execute_build_subgraph(self, lead_id: int, db: AsyncSession) -> LeadGraphState:
        """Execute Stage 2: Builder → Verifier → Delivery Dispatcher."""
        state = await self.initialize_lead_state(lead_id, db)

        # Guard: don't double-run if already building
        if state.workflow_status == WorkflowStatus.BUILDING:
            logger.warning("Lead #%s is already building — skipping duplicate trigger", lead_id)
            return state

        state = await self.builder_node.execute(state, db)
        state = await self.verifier_node.execute(state, db)
        state = await self.delivery_node.execute(state, db)

        _active_states[lead_id] = state
        return state

    def get_state(self, lead_id: int) -> LeadGraphState | None:
        return _active_states.get(lead_id)


# Global singleton — used by all routers and background tasks
workflow_graph = WorkflowGraphEngine()
workflow_graph_engine = workflow_graph
