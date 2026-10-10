"""Autonomous Agent Tool Registry & Execution Engine.

Provides high-level, production-grade tools that the LLM ReAct agent can autonomously call
to query, manage, architect, and build client solutions across the platform.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from sqlalchemy import desc, func, or_, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ActivityLog, Lead, PaymentRecord, ProjectBuild, ProjectGoalPlan
from app.services.invoice_service import create_invoice_pdf, generate_invoice_number, generate_quote_number

logger = logging.getLogger("agent_tools")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------

async def tool_query_leads(
    status: str | None = None,
    temperature: str | None = None,
    search: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search and filter inbound leads from the database."""
    async with AsyncSessionLocal() as db:
        stmt = select(Lead)
        if status:
            stmt = stmt.where(Lead.status == status.upper())
        if temperature:
            stmt = stmt.where(Lead.temperature == temperature.upper())
        if search:
            kw = f"%{search}%"
            stmt = stmt.where(or_(Lead.name.ilike(kw), Lead.requirement.ilike(kw), Lead.company.ilike(kw)))

        stmt = stmt.order_by(desc(Lead.created_at)).limit(min(limit, 20))
        result = await db.execute(stmt)
        leads = result.scalars().all()

        return {
            "count": len(leads),
            "leads": [
                {
                    "id": l.id,
                    "name": l.name,
                    "company": l.company or "N/A",
                    "status": l.status,
                    "temperature": l.temperature,
                    "score": l.score,
                    "budget": l.budget,
                    "requirement": (l.requirement[:140] + "...") if len(l.requirement) > 140 else l.requirement,
                    "payment_status": l.payment_status,
                }
                for l in leads
            ]
        }


async def tool_get_lead_details(lead_id: int) -> dict[str, Any]:
    """Retrieve full deep profile for a specific lead, including plan, builds, and payments."""
    async with AsyncSessionLocal() as db:
        q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = q.scalar_one_or_none()
        if not lead:
            return {"error": f"Lead #{lead_id} not found"}

        plan_q = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_q.scalar_one_or_none()

        builds_q = await db.execute(
            select(ProjectBuild)
            .where(ProjectBuild.lead_id == lead_id)
            .order_by(ProjectBuild.id.desc())
            .limit(3)
        )
        builds = builds_q.scalars().all()

        return {
            "id": lead.id,
            "name": lead.name,
            "company": lead.company,
            "email": lead.email,
            "phone": lead.phone,
            "requirement": lead.requirement,
            "budget": lead.budget,
            "timeline": lead.timeline,
            "status": lead.status,
            "temperature": lead.temperature,
            "score": lead.score,
            "quote_number": lead.quote_number,
            "invoice_number": lead.invoice_number,
            "payment_status": lead.payment_status,
            "advance_amount": lead.advance_amount,
            "plan": {
                "id": plan.id,
                "title": plan.title,
                "target_deliverable": plan.target_deliverable,
                "approval_status": plan.approval_status,
                "tech_stack": json.loads(plan.tech_stack) if plan.tech_stack and plan.tech_stack.startswith("[") else plan.tech_stack,
                "risk_level": plan.risk_level,
            } if plan else None,
            "recent_builds": [
                {
                    "id": b.id,
                    "project_name": b.project_name,
                    "status": b.status,
                    "progress_percent": b.progress_percent,
                    "project_path": b.project_path,
                    "gate_passed": b.gate_passed,
                }
                for b in builds
            ]
        }


async def tool_create_lead(
    name: str,
    requirement: str,
    company: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    budget: str = "Flexible",
    timeline: str = "Immediate",
    source: str = "agent_chat",
) -> dict[str, Any]:
    """Ingest a new lead into the system and trigger automated qualification in the background."""
    async with AsyncSessionLocal() as db:
        new_lead = Lead(
            name=name.strip(),
            requirement=requirement.strip(),
            company=company.strip() if company else None,
            email=email.strip() if email else None,
            phone=phone.strip() if phone else None,
            budget=budget.strip(),
            timeline=timeline.strip(),
            source=source,
            status="NEW",
            temperature="WARM",
            score=0,
        )
        db.add(new_lead)
        await db.commit()
        await db.refresh(new_lead)
        lead_id = new_lead.id

    # Spawn background pipeline execution
    from app.graph.engine import workflow_graph
    asyncio.create_task(workflow_graph.run_pipeline_for_lead_bg(lead_id))

    return {
        "success": True,
        "lead_id": lead_id,
        "message": f"Lead #{lead_id} created for '{name}'. Autonomous DAG pipeline dispatched.",
    }


async def tool_approve_plan(lead_id: int, decision: str = "APPROVED", feedback: str | None = None) -> dict[str, Any]:
    """Approve or reject a Stage 1 architecture plan for a lead."""
    dec = decision.upper()
    if dec not in ("APPROVED", "REJECTED"):
        return {"error": f"Invalid decision '{decision}'. Must be APPROVED or REJECTED."}

    async with AsyncSessionLocal() as db:
        plan_q = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_q.scalar_one_or_none()
        if not plan:
            return {"error": f"No architecture plan found for Lead #{lead_id}"}

        plan.approval_status = dec
        plan.approved_at = _now() if dec == "APPROVED" else None

        lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_q.scalar_one_or_none()
        if lead:
            lead.status = "PLAN_APPROVED" if dec == "APPROVED" else "REJECTED"

        await db.commit()

        # Update in-memory graph state
        from app.graph.engine import workflow_graph
        await workflow_graph.handle_plan_approval(lead_id, dec, feedback, db)

    return {
        "success": True,
        "lead_id": lead_id,
        "decision": dec,
        "message": f"Stage 1 plan for Lead #{lead_id} marked as {dec}."
    }


async def tool_trigger_build(lead_id: int) -> dict[str, Any]:
    """Authorize Stage 2 and trigger autonomous project code synthesis."""
    async with AsyncSessionLocal() as db:
        lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_q.scalar_one_or_none()
        if not lead:
            return {"error": f"Lead #{lead_id} not found"}

        if lead.status == "BUILDING":
            return {"message": f"Lead #{lead_id} is already actively building."}

        # Check existing plan
        plan_q = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_q.scalar_one_or_none()
        if not plan or plan.approval_status != "APPROVED":
            return {"error": f"Lead #{lead_id} does not have an approved plan. Approve plan first."}

        lead.status = "BUILDING"
        await db.commit()

    # Launch build in background task
    async def _run_build():
        async with AsyncSessionLocal() as bdb:
            try:
                from app.services.project_builder import execute_autonomous_build
                await execute_autonomous_build(lead_id=lead_id, db=bdb)
            except Exception as e:
                logger.exception("Build background task failed for Lead #%d: %s", lead_id, e)

    asyncio.create_task(_run_build())

    return {
        "success": True,
        "lead_id": lead_id,
        "status": "BUILDING",
        "message": f"Stage 2 build triggered for Lead #{lead_id}. Autonomous code generation started."
    }


async def tool_get_build_status(lead_id: int | None = None, build_id: int | None = None) -> dict[str, Any]:
    """Check live status, verification metrics, and deliverable paths for a build."""
    async with AsyncSessionLocal() as db:
        stmt = select(ProjectBuild)
        if build_id:
            stmt = stmt.where(ProjectBuild.id == build_id)
        elif lead_id:
            stmt = stmt.where(ProjectBuild.lead_id == lead_id).order_by(desc(ProjectBuild.created_at))
        else:
            return {"error": "Either lead_id or build_id must be provided."}

        result = await db.execute(stmt)
        build = result.scalars().first()
        if not build:
            return {"error": "No project build found for the given criteria."}

        gen_files = []
        if build.generated_files:
            try:
                gen_files = json.loads(build.generated_files)
            except Exception:
                gen_files = [build.generated_files]

        return {
            "build_id": build.id,
            "lead_id": build.lead_id,
            "project_name": build.project_name,
            "status": build.status,
            "progress_percent": build.progress_percent,
            "gate_passed": build.gate_passed,
            "project_path": build.project_path,
            "generated_files_count": len(gen_files),
            "generated_files": gen_files[:8],
            "completed_at": build.completed_at.isoformat() if build.completed_at else None,
        }


async def tool_generate_invoice(lead_id: int, advance_amount: int | None = None) -> dict[str, Any]:
    """Generate commercial quotation and invoice identifiers for a client."""
    async with AsyncSessionLocal() as db:
        lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_q.scalar_one_or_none()
        if not lead:
            return {"error": f"Lead #{lead_id} not found"}

        quote_num = lead.quote_number or generate_quote_number(lead.id)
        inv_num = lead.invoice_number or generate_invoice_number(lead.id)

        lead.quote_number = quote_num
        lead.invoice_number = inv_num
        if advance_amount is not None:
            lead.advance_amount = advance_amount
            lead.payment_status = "ADVANCE_PAID" if advance_amount > 0 else "UNPAID"

        await db.commit()

        # Build PDF preview buffer
        line_items = [
            {"description": f"Autonomous Software Deliverable — {lead.requirement[:60]}", "amount": 35000.0}
        ]
        pdf_bytes = create_invoice_pdf(
            doc_number=inv_num,
            client_name=lead.name,
            client_email=lead.email or "client@example.com",
            client_company=lead.company,
            line_items=line_items,
            advance_paid=float(lead.advance_amount or 0),
        )

        return {
            "success": True,
            "lead_id": lead.id,
            "quote_number": quote_num,
            "invoice_number": inv_num,
            "advance_amount": lead.advance_amount,
            "pdf_size_bytes": len(pdf_bytes),
            "message": f"Invoice {inv_num} & Quote {quote_num} generated for '{lead.name}'.",
        }


async def tool_get_revenue_summary() -> dict[str, Any]:
    """Aggregate collected revenue, pending quotes, and payment metrics."""
    async with AsyncSessionLocal() as db:
        # Total payments in payment_records
        records_q = await db.execute(select(PaymentRecord))
        records = records_q.scalars().all()
        total_collected = sum(r.amount for r in records)

        # Advance amounts on leads
        adv_q = await db.execute(select(func.sum(Lead.advance_amount)))
        adv_total = adv_q.scalar() or 0

        paid_leads_count = (await db.execute(
            select(func.count(Lead.id)).where(Lead.payment_status.in_(["ADVANCE_PAID", "FULLY_PAID"]))
        )).scalar() or 0

        unpaid_leads_count = (await db.execute(
            select(func.count(Lead.id)).where(Lead.payment_status == "UNPAID")
        )).scalar() or 0

        return {
            "total_collected_inr": total_collected if total_collected > 0 else adv_total,
            "successful_payments_count": len(records),
            "leads_with_advance": paid_leads_count,
            "leads_unpaid": unpaid_leads_count,
            "currency": "INR",
        }


async def tool_get_system_telemetry() -> dict[str, Any]:
    """Retrieve live hardware, database volume, and local AI daemon metrics."""
    import httpx

    # Check Ollama
    ollama_ok = False
    ollama_info = "Checking..."
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                models = [m.get("name") for m in resp.json().get("models", [])]
                ollama_info = f"Online ({len(models)} models available: {', '.join(models[:2])})"
            else:
                ollama_info = f"HTTP {resp.status_code}"
    except Exception as e:
        ollama_info = f"Unreachable: {str(e)[:60]}"

    async with AsyncSessionLocal() as db:
        lead_count = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
        approved_plans = (await db.execute(
            select(func.count(ProjectGoalPlan.id)).where(ProjectGoalPlan.approval_status == "APPROVED")
        )).scalar() or 0
        active_builds = (await db.execute(
            select(func.count(ProjectBuild.id)).where(
                ProjectBuild.status.in_(["QUEUED", "SCAFFOLDING", "GENERATING_CODE", "BUILDING"])
            )
        )).scalar() or 0
        completed_builds = (await db.execute(
            select(func.count(ProjectBuild.id)).where(ProjectBuild.status == "COMPLETED")
        )).scalar() or 0

    return {
        "local_llm_status": "ONLINE" if ollama_ok else "OFFLINE",
        "local_llm_details": ollama_info,
        "model_configured": settings.OLLAMA_MODEL,
        "hardware": "NVIDIA GeForce RTX 3050 (Port 11434)",
        "total_leads": lead_count,
        "approved_plans": approved_plans,
        "active_builds": active_builds,
        "completed_deliverables": completed_builds,
    }


async def tool_get_daily_brief() -> dict[str, Any]:
    """Generate high-level daily operations briefing."""
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as db:
        all_leads = (await db.execute(select(Lead))).scalars().all()
        today_leads = [
            l for l in all_leads
            if l.created_at and (
                l.created_at if l.created_at.tzinfo is not None else l.created_at.replace(tzinfo=timezone.utc)
            ) >= today
        ]
        hot_count = sum(1 for l in all_leads if l.temperature == "HOT")
        warm_count = sum(1 for l in all_leads if l.temperature == "WARM")
        pending = sum(1 for l in all_leads if l.status in ("PLAN_PENDING", "PLAN_APPROVED"))
        building = sum(1 for l in all_leads if l.status == "BUILDING")

        recent_builds = (await db.execute(
            select(ProjectBuild).where(ProjectBuild.status == "COMPLETED").order_by(desc(ProjectBuild.created_at)).limit(3)
        )).scalars().all()

    return {
        "timestamp": _now().isoformat(),
        "today_new_leads": len(today_leads),
        "hot_deals": hot_count,
        "warm_leads": warm_count,
        "pending_approval": pending,
        "currently_building": building,
        "recent_completed_builds": [
            {"id": b.id, "project_name": b.project_name, "lead_id": b.lead_id}
            for b in recent_builds
        ]
    }


# ---------------------------------------------------------------------------
# Tool Registry Map & Dispatcher
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {
    "query_leads": {
        "fn": tool_query_leads,
        "description": "Search and filter inbound business leads by status (NEW, SCORED, PLAN_PENDING, PLAN_APPROVED, BUILDING, BUILT, REJECTED), temperature (HOT, WARM, COLD), or search keywords.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by lead status"},
                "temperature": {"type": "string", "description": "Filter by temperature: HOT, WARM, COLD"},
                "search": {"type": "string", "description": "Keyword search across requirement, name, company"},
                "limit": {"type": "integer", "description": "Max number of leads to return (default 5)"},
            },
        },
    },
    "get_lead_details": {
        "fn": tool_get_lead_details,
        "description": "Get complete detailed information for a specific lead, including requirement, budget, goal plan, active builds, and payment state.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer", "description": "The numeric ID of the lead"},
            },
            "required": ["lead_id"],
        },
    },
    "create_lead": {
        "fn": tool_create_lead,
        "description": "Ingest a new client lead into the system and trigger the autonomous qualification & architecture DAG pipeline.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Client or contact name"},
                "requirement": {"type": "string", "description": "Detailed description of software requested"},
                "company": {"type": "string", "description": "Company name (optional)"},
                "email": {"type": "string", "description": "Client email address (optional)"},
                "phone": {"type": "string", "description": "Client phone number (optional)"},
                "budget": {"type": "string", "description": "Allocated budget (default 'Flexible')"},
                "timeline": {"type": "string", "description": "Target delivery timeline (default 'Immediate')"},
            },
            "required": ["name", "requirement"],
        },
    },
    "approve_plan": {
        "fn": tool_approve_plan,
        "description": "Approve or reject a Stage 1 technical implementation plan for a lead.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer", "description": "Numeric ID of the lead"},
                "decision": {"type": "string", "enum": ["APPROVED", "REJECTED"], "description": "Decision: APPROVED or REJECTED"},
                "feedback": {"type": "string", "description": "Optional review feedback or architect notes"},
            },
            "required": ["lead_id", "decision"],
        },
    },
    "trigger_build": {
        "fn": tool_trigger_build,
        "description": "Authorize and trigger Stage 2 autonomous code synthesis and build verification for an approved lead.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer", "description": "Numeric ID of the lead to build"},
            },
            "required": ["lead_id"],
        },
    },
    "get_build_status": {
        "fn": tool_get_build_status,
        "description": "Inspect progress, verification gate status, and file deliverables of an active or completed build.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer", "description": "Lead numeric ID (optional)"},
                "build_id": {"type": "integer", "description": "Build numeric ID (optional)"},
            },
        },
    },
    "generate_invoice": {
        "fn": tool_generate_invoice,
        "description": "Generate commercial quote (QUOTE-2026-XXXXX) and invoice (INV-2026-XXXXX) with PDF creation.",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "integer", "description": "Numeric ID of the lead"},
                "advance_amount": {"type": "integer", "description": "Advance paid amount in INR (optional)"},
            },
            "required": ["lead_id"],
        },
    },
    "get_revenue_summary": {
        "fn": tool_get_revenue_summary,
        "description": "Fetch commercial financial metrics: total collected revenue, paid leads, and outstanding invoices.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_system_telemetry": {
        "fn": tool_get_system_telemetry,
        "description": "Get real-time system health: local Ollama status, GPU status, total leads, active builds, deliverables.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_daily_brief": {
        "fn": tool_get_daily_brief,
        "description": "Get executive daily intelligence summary across new leads, HOT deals, building projects, and deliverables.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def get_tools_prompt_description() -> str:
    """Format tool catalog for LLM system prompt."""
    lines = ["Available Tools:"]
    for name, meta in TOOLS.items():
        desc = meta["description"]
        params = meta["parameters"].get("properties", {})
        param_list = ", ".join(f"{k}: {v.get('type')}" for k, v in params.items())
        lines.append(f"- `{name}({param_list})`: {desc}")
    return "\n".join(lines)


async def execute_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a registered tool safely and return a JSON-serializable dictionary."""
    if tool_name not in TOOLS:
        return {"error": f"Tool '{tool_name}' is not recognized. Available tools: {list(TOOLS.keys())}"}

    tool_meta = TOOLS[tool_name]
    fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]] = tool_meta["fn"]
    arguments = args or {}

    try:
        logger.info("Executing tool: %s with args: %s", tool_name, arguments)
        result = await fn(**arguments)
        return result
    except Exception as e:
        logger.exception("Tool execution error in %s: %s", tool_name, e)
        return {"error": f"Execution of {tool_name} failed: {str(e)}"}
