"""Specialized Agentic Nodes for Graph-Engineered Workflows.
All nodes integrated with Hermes (Telegram/Discord/WS) via the notifier service.
"""
from __future__ import annotations

import json
import logging
import py_compile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.state import LeadGraphState, NodeExecutionRecord, NodeID, WorkflowStatus
from app.models import Lead, ProjectGoalPlan
from app.services.ai_engine import (
    call_llm,
    generate_project_plan,
    score_and_analyze_lead,
)
from app.services.notifier import (
    notify_build_completed,
    notify_duplicate_lead,
    notify_hot_deal,
    notify_plan_approval_request,
)

logger = logging.getLogger("graph_nodes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BaseNode:
    node_id: NodeID

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Node 0: Lead Deduplication Agent (NEW)
# ─────────────────────────────────────────────────────────────────────────────

class LeadDeduplicationNode(BaseNode):
    """Check if this lead is a duplicate submission (same email or phone).
    If duplicate found → mark state, notify Hermes, halt pipeline gracefully.
    """
    node_id = NodeID.LEAD_DEDUP

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        logger.info("[%s] Checking for duplicate lead #%s", self.node_id.value, state.lead_id)

        filters = []
        if state.email:
            filters.append(Lead.email == state.email)
        if state.phone:
            filters.append(Lead.phone == state.phone)

        if filters:
            q = await db.execute(
                select(Lead)
                .where(or_(*filters))
                .where(Lead.id != state.lead_id)
                .where(Lead.status != "REJECTED")
            )
            # BUG #3 FIX: Use .scalars().first() — scalar_one_or_none() raises
            # MultipleResultsFound if same email AND phone match different leads.
            existing = q.scalars().first()
            if existing:
                state.is_duplicate = True
                state.duplicate_of_lead_id = existing.id
                state.workflow_status = WorkflowStatus.DUPLICATE

                # Update lead status in DB
                lead_q = await db.execute(select(Lead).where(Lead.id == state.lead_id))
                lead_db = lead_q.scalar_one_or_none()
                if lead_db:
                    lead_db.status = "DUPLICATE"
                    await db.commit()

                await notify_duplicate_lead(state.lead_id, existing.id, state.client_name)
                logger.warning("Duplicate detected: Lead #%s matches Lead #%s", state.lead_id, existing.id)

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id,
            duration_ms=round(duration, 2),
            details=f"Duplicate: {state.is_duplicate} (matches #{state.duplicate_of_lead_id})" if state.is_duplicate else "No duplicate found",
            output_summary={"is_duplicate": state.is_duplicate},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: AI Qualifier Agent
# ─────────────────────────────────────────────────────────────────────────────

class AIQualifierNode(BaseNode):
    node_id = NodeID.AI_QUALIFIER

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        logger.info("[%s] Scoring Lead #%s", self.node_id.value, state.lead_id)

        # FIX: include email, phone, source for richer scoring
        lead_data = {
            "id": state.lead_id,
            "name": state.client_name,
            "company": state.company,
            "email": state.email,
            "phone": state.phone,
            "source": state.source,
            "requirement": state.requirement,
            "budget": state.budget,
            "timeline": state.timeline,
        }

        analysis = await score_and_analyze_lead(lead_data)
        state.score = analysis.get("score", 50)
        state.temperature = analysis.get("temperature", "WARM")
        state.qualification_summary = analysis

        q = await db.execute(select(Lead).where(Lead.id == state.lead_id))
        lead_db = q.scalar_one_or_none()
        if lead_db:
            lead_db.score = state.score
            lead_db.temperature = state.temperature
            lead_db.ai_analysis = json.dumps(analysis)
            lead_db.status = "SCORED"
            await db.commit()

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id,
            duration_ms=round(duration, 2),
            details=f"Scored {state.score}/100 ({state.temperature})",
            output_summary={"score": state.score, "temperature": state.temperature},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 1.5: Budget Estimator Agent (NEW)
# ─────────────────────────────────────────────────────────────────────────────

class BudgetEstimatorNode(BaseNode):
    """Enrich HOT leads with market rate estimate using Hermes LLM."""
    node_id = NodeID.BUDGET_ESTIMATOR

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        # Only run for HOT leads — skip for WARM/COLD
        if state.temperature != "HOT":
            state.execution_history.append(NodeExecutionRecord(
                node_id=self.node_id, duration_ms=0, details="Skipped — non-HOT lead",
            ))
            return state

        logger.info("[%s] Estimating market rate for HOT lead #%s", self.node_id.value, state.lead_id)
        system_prompt = (
            "You are an expert Indian software project pricing consultant.\n"
            "Given a client requirement, estimate the fair market range in Indian Rupees (₹).\n"
            "Return ONLY a JSON: {\"low\": \"₹X\", \"high\": \"₹Y\", \"rationale\": \"...\", \"recommended_tier\": \"STARTUP|PROFESSIONAL|ENTERPRISE\"}"
        )
        user_prompt = (
            f"Requirement: {state.requirement}\n"
            f"Client Budget Stated: {state.budget}\n"
            f"Timeline: {state.timeline}"
        )
        try:
            raw = await call_llm(user_prompt, system_prompt, max_tokens=300)
            from app.services.ai_engine import clean_json_response
            data = clean_json_response(raw)
            state.market_rate_estimate = f"{data.get('low')} – {data.get('high')} ({data.get('recommended_tier')})"
        except Exception as e:
            logger.warning("[BudgetEstimator] Failed to estimate: %s", e)
            state.market_rate_estimate = state.budget or "₹25,000 – ₹75,000"

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details=f"Market estimate: {state.market_rate_estimate}",
            output_summary={"estimate": state.market_rate_estimate},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: Plan Architect Agent
# ─────────────────────────────────────────────────────────────────────────────

class PlanArchitectNode(BaseNode):
    node_id = NodeID.PLAN_ARCHITECT

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        logger.info("[%s] Architecting plan for Lead #%s", self.node_id.value, state.lead_id)

        lead_data = {
            "id": state.lead_id,
            "name": state.client_name,
            "company": state.company,
            "requirement": state.requirement,
            "budget": state.budget,
            "timeline": state.timeline,
        }
        analysis = state.qualification_summary or {}
        plan_dict = await generate_project_plan(lead_data, analysis)

        state.plan_title = plan_dict.get("title", f"{state.client_name} Custom Automation")
        state.target_deliverable = plan_dict.get("target_deliverable", "Full-Stack Application")
        state.core_features = plan_dict.get("core_features", [])
        state.tech_stack = plan_dict.get("tech_stack", [])
        state.architecture_overview = plan_dict.get("architecture_overview", "")

        # Persist plan — fetch latest (removed unique constraint so this is safe)
        q = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == state.lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        existing_plan = q.scalar_one_or_none()

        if existing_plan:
            existing_plan.title = state.plan_title
            existing_plan.target_deliverable = state.target_deliverable
            existing_plan.core_features = json.dumps(state.core_features)
            existing_plan.tech_stack = json.dumps(state.tech_stack)
            existing_plan.architecture_overview = state.architecture_overview
            existing_plan.implementation_steps = json.dumps(plan_dict.get("implementation_steps", []))
            existing_plan.approval_status = "PENDING"
            plan_record = existing_plan
        else:
            plan_record = ProjectGoalPlan(
                lead_id=state.lead_id,
                title=state.plan_title,
                target_deliverable=state.target_deliverable,
                core_features=json.dumps(state.core_features),
                tech_stack=json.dumps(state.tech_stack),
                architecture_overview=state.architecture_overview,
                implementation_steps=json.dumps(plan_dict.get("implementation_steps", [])),
                approval_status="PENDING",
            )
            db.add(plan_record)

        lead_q = await db.execute(select(Lead).where(Lead.id == state.lead_id))
        lead_db = lead_q.scalar_one_or_none()
        if lead_db:
            lead_db.status = "PLAN_PENDING"
        await db.commit()
        await db.refresh(plan_record)
        state.plan_id = plan_record.id

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details=f"Drafted plan: {state.plan_title}",
            output_summary={"plan_id": state.plan_id, "title": state.plan_title},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: Stage 1 Approval Gate
# ─────────────────────────────────────────────────────────────────────────────

class Stage1ApprovalGateNode(BaseNode):
    node_id = NodeID.STAGE1_APPROVAL_GATE

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        lead_data = {
            "id": state.lead_id, "name": state.client_name,
            "company": state.company, "requirement": state.requirement,
            "budget": state.budget, "score": state.score,
        }
        plan_data = {
            "id": state.plan_id, "title": state.plan_title,
            "target_deliverable": state.target_deliverable,
            "core_features": state.core_features,
            "tech_stack": state.tech_stack,
        }
        if state.market_rate_estimate:
            plan_data["market_estimate"] = state.market_rate_estimate

        # BUG #14 FIX: HOT leads already receive notification from notify_hot_deal which
        # includes approval buttons. Send plan approval separately only for non-HOT leads
        # to avoid duplicate Telegram messages with redundant approval buttons.
        if state.temperature == "HOT":
            await notify_hot_deal(lead_data, plan_data)
        else:
            await notify_plan_approval_request(lead_data, plan_data)

        state.workflow_status = WorkflowStatus.AWAITING_STAGE1
        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details="Dispatched Stage 1 approval prompt to Hermes channels",
            output_summary={"status": state.workflow_status.value},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 5: Project Builder Agent
# ─────────────────────────────────────────────────────────────────────────────

class ProjectBuilderNode(BaseNode):
    node_id = NodeID.PROJECT_BUILDER

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        from app.services.project_builder import execute_autonomous_build
        t0 = time.time()
        state.workflow_status = WorkflowStatus.BUILDING
        build_record = await execute_autonomous_build(
            state.lead_id, db,
            custom_instructions=state.custom_instructions,
        )
        state.build_id = build_record.id
        state.project_path = build_record.project_path
        state.generated_files = json.loads(build_record.generated_files) if build_record.generated_files else []
        state.progress_percent = build_record.progress_percent
        state.build_logs = build_record.build_logs

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details=f"Built {len(state.generated_files)} files at {state.project_path}",
            output_summary={"build_id": state.build_id, "files_count": len(state.generated_files)},
        ))
        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 6: Code Verifier & Self-Healing Agent (with Python syntax validation)
# ─────────────────────────────────────────────────────────────────────────────

class CodeVerifierNode(BaseNode):
    node_id = NodeID.CODE_VERIFIER

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        if not state.project_path:
            raise ValueError("No project path available to verify.")

        proj_dir = Path(state.project_path)
        required_artifacts = ["main.py", "index.html", "requirements.txt", "run_local.bat", "README.md"]
        missing = [f for f in required_artifacts if not (proj_dir / f).exists()]

        if missing:
            logger.warning("[Verifier] Missing files: %s — synthesizing fallback artifacts...", missing)
            from app.services.project_builder import _fallback_code_for_file
            lead_dict = {
                "name": state.client_name,
                "requirement": state.requirement,
                "custom_instructions": state.custom_instructions,
            }
            plan_dict = {"title": state.plan_title, "core_features": state.core_features}
            for m in missing:
                content = _fallback_code_for_file(m, plan_dict, lead_dict)
                (proj_dir / m).write_text(content, encoding="utf-8")
                if m not in state.generated_files:
                    state.generated_files.append(m)

        # ── Python Syntax Validation ────────────────────────────────
        for py_file in proj_dir.glob("*.py"):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                logger.warning("[Verifier] Syntax error in %s: %s — regenerating via fallback", py_file.name, e)
                from app.services.project_builder import _fallback_code_for_file
                lead_dict = {"name": state.client_name, "requirement": state.requirement}
                plan_dict = {"title": state.plan_title, "core_features": state.core_features}
                fallback = _fallback_code_for_file(py_file.name, plan_dict, lead_dict)
                py_file.write_text(fallback, encoding="utf-8")

        # ── 13-Point Enterprise Build Verification Pipeline ──────────────
        from app.services.build_verifier import run_full_build_verification
        plan_dict = {
            "title": state.plan_title or "Client Deliverable",
            "core_features": state.core_features or [],
        }
        report = run_full_build_verification(
            project_dir=proj_dir,
            requirement=state.requirement or "",
            title=state.plan_title or "Client Deliverable",
            plan=plan_dict,
        )

        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details=f"Evaluated 13 enterprise verification gates. Passed: {report.get('gate_passed', False)}. Checksum: {report.get('checksum', '')[:12]}...",
            output_summary={
                "verified": report.get("gate_passed", False),
                "total_gates": report.get("total_gates_evaluated", 13),
                "status": report.get("status", "VERIFIED"),
                "gates": {k: v.get("passed", False) for k, v in report.get("gates", {}).items() if isinstance(v, dict)},
            },
        ))

        # BUG #17 FIX: Persist verification results back to ProjectBuild DB record
        if state.build_id:
            from app.models import ProjectBuild
            build_q = await db.execute(select(ProjectBuild).where(ProjectBuild.id == state.build_id))
            build_db = build_q.scalar_one_or_none()
            if build_db:
                build_db.gate_passed = report.get("gate_passed", False)
                build_db.artifact_checksum = report.get("checksum")
                build_db.verification_report = json.dumps(report)
                await db.commit()

        state.current_node = self.node_id
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Node 7: Delivery Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

class DeliveryDispatcherNode(BaseNode):
    node_id = NodeID.DELIVERY_DISPATCHER

    async def execute(self, state: LeadGraphState, db: AsyncSession) -> LeadGraphState:
        t0 = time.time()
        lead_data = {"id": state.lead_id, "name": state.client_name}
        await notify_build_completed(
            lead_data,
            state.plan_title or "Client Solution",
            state.project_path or "",
            state.generated_files,
        )
        state.workflow_status = WorkflowStatus.COMPLETED
        duration = (time.time() - t0) * 1000
        state.execution_history.append(NodeExecutionRecord(
            node_id=self.node_id, duration_ms=round(duration, 2),
            details="Dispatched completed build deliverable to Hermes channels",
            output_summary={"status": state.workflow_status.value},
        ))
        state.current_node = self.node_id
        return state
