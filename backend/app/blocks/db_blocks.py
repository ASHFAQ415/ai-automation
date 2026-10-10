"""Database Blocks — Lead, Project, Invoice operations.

Migrates all agent_tools.py functions into typed AutoGPT-style Blocks.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import Field

from app.blocks._base import Block, BlockCategory, BlockSchema, BlockRegistry

logger = logging.getLogger("blocks.db")


# ---------------------------------------------------------------------------
# Shared output schemas
# ---------------------------------------------------------------------------

class StatusOutput(BlockSchema):
    success: bool = True
    message: str = ""


# ---------------------------------------------------------------------------
# Query Leads Block
# ---------------------------------------------------------------------------

class QueryLeadsInput(BlockSchema):
    status: Optional[str] = Field(None, description="Filter by lead status (e.g. NEW, QUALIFIED, CLOSED)")
    temperature: Optional[str] = Field(None, description="Filter by temperature (HOT, WARM, COLD)")
    search: Optional[str] = Field(None, description="Keyword search in name, company, requirement")
    limit: int = Field(5, description="Max number of leads to return (max 20)")


class QueryLeadsOutput(BlockSchema):
    count: int
    leads: list[dict[str, Any]]


class QueryLeadsBlock(Block):
    id = "query_leads"
    name = "Query Leads"
    description = "Search and filter inbound leads from the CRM database"
    category = BlockCategory.DATABASE
    input_schema = QueryLeadsInput
    output_schema = QueryLeadsOutput

    async def run(self, input_data: QueryLeadsInput) -> QueryLeadsOutput:
        from sqlalchemy import desc, or_, select
        from app.database import AsyncSessionLocal
        from app.models import Lead

        async with AsyncSessionLocal() as db:
            stmt = select(Lead)
            if input_data.status:
                stmt = stmt.where(Lead.status == input_data.status.upper())
            if input_data.temperature:
                stmt = stmt.where(Lead.temperature == input_data.temperature.upper())
            if input_data.search:
                kw = f"%{input_data.search}%"
                stmt = stmt.where(
                    or_(Lead.name.ilike(kw), Lead.requirement.ilike(kw), Lead.company.ilike(kw))
                )
            stmt = stmt.order_by(desc(Lead.created_at)).limit(min(input_data.limit, 20))
            result = await db.execute(stmt)
            leads = result.scalars().all()

        return QueryLeadsOutput(
            count=len(leads),
            leads=[
                {
                    "id": l.id,
                    "name": l.name,
                    "company": l.company or "N/A",
                    "status": l.status,
                    "temperature": l.temperature,
                    "requirement": (l.requirement or "")[:100],
                    "budget": l.budget,
                }
                for l in leads
            ],
        )

    def test_input(self) -> QueryLeadsInput:
        return QueryLeadsInput(limit=3)


# ---------------------------------------------------------------------------
# Get Lead Detail Block
# ---------------------------------------------------------------------------

class GetLeadDetailInput(BlockSchema):
    lead_id: int = Field(..., description="Unique lead ID")


class GetLeadDetailOutput(BlockSchema):
    found: bool
    lead: Optional[dict[str, Any]] = None


class GetLeadDetailBlock(Block):
    id = "get_lead_detail"
    name = "Get Lead Detail"
    description = "Retrieve full detail for a specific lead by ID"
    category = BlockCategory.DATABASE
    input_schema = GetLeadDetailInput
    output_schema = GetLeadDetailOutput

    async def run(self, input_data: GetLeadDetailInput) -> GetLeadDetailOutput:
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models import Lead

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Lead).where(Lead.id == input_data.lead_id))
            lead = result.scalar_one_or_none()

        if not lead:
            return GetLeadDetailOutput(found=False)

        return GetLeadDetailOutput(
            found=True,
            lead={
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "company": lead.company,
                "requirement": lead.requirement,
                "budget": lead.budget,
                "status": lead.status,
                "temperature": lead.temperature,
                "source": lead.source,
                "created_at": str(lead.created_at),
            },
        )


# ---------------------------------------------------------------------------
# Create Lead Block
# ---------------------------------------------------------------------------

class CreateLeadInput(BlockSchema):
    name: str = Field(..., description="Lead's full name")
    requirement: str = Field(..., description="What they need built")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    company: Optional[str] = Field(None, description="Company name")
    budget: Optional[float] = Field(None, description="Budget in USD")
    source: Optional[str] = Field("telegram", description="Lead source channel")


class CreateLeadOutput(BlockSchema):
    success: bool
    lead_id: int = 0
    message: str = ""


class CreateLeadBlock(Block):
    id = "create_lead"
    name = "Create Lead"
    description = "Add a new lead to the CRM database"
    category = BlockCategory.DATABASE
    input_schema = CreateLeadInput
    output_schema = CreateLeadOutput

    async def run(self, input_data: CreateLeadInput) -> CreateLeadOutput:
        from datetime import datetime, timezone
        from app.database import AsyncSessionLocal
        from app.models import Lead

        async with AsyncSessionLocal() as db:
            lead = Lead(
                name=input_data.name,
                requirement=input_data.requirement,
                email=input_data.email,
                phone=input_data.phone,
                company=input_data.company,
                budget=input_data.budget,
                source=input_data.source or "telegram",
                status="NEW",
                temperature="WARM",
                created_at=datetime.now(timezone.utc),
            )
            db.add(lead)
            await db.commit()
            await db.refresh(lead)

        return CreateLeadOutput(
            success=True,
            lead_id=lead.id,
            message=f"Lead #{lead.id} created for {input_data.name}",
        )


# ---------------------------------------------------------------------------
# Update Lead Status Block
# ---------------------------------------------------------------------------

class UpdateLeadStatusInput(BlockSchema):
    lead_id: int = Field(..., description="Lead ID to update")
    status: str = Field(..., description="New status (NEW, QUALIFIED, PROPOSAL, NEGOTIATION, CLOSED_WON, CLOSED_LOST)")
    temperature: Optional[str] = Field(None, description="New temperature (HOT, WARM, COLD)")
    notes: Optional[str] = Field(None, description="Notes to append to the lead")


class UpdateLeadStatusBlock(Block):
    id = "update_lead_status"
    name = "Update Lead Status"
    description = "Change the status or temperature of a lead in the CRM"
    category = BlockCategory.DATABASE
    input_schema = UpdateLeadStatusInput
    output_schema = StatusOutput

    async def run(self, input_data: UpdateLeadStatusInput) -> StatusOutput:
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models import Lead

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Lead).where(Lead.id == input_data.lead_id))
            lead = result.scalar_one_or_none()
            if not lead:
                return StatusOutput(success=False, message=f"Lead #{input_data.lead_id} not found")

            lead.status = input_data.status.upper()
            if input_data.temperature:
                lead.temperature = input_data.temperature.upper()
            if input_data.notes:
                existing = lead.requirement or ""
                lead.requirement = existing + f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {input_data.notes}"
            await db.commit()

        return StatusOutput(success=True, message=f"Lead #{input_data.lead_id} updated to {input_data.status}")


# ---------------------------------------------------------------------------
# Approve Plan Block
# ---------------------------------------------------------------------------

class ApprovePlanInput(BlockSchema):
    lead_id: int = Field(..., description="Lead ID to generate and approve a project plan for")


class ApprovePlanOutput(BlockSchema):
    success: bool
    plan_id: int = 0
    summary: str = ""


class ApprovePlanBlock(Block):
    id = "approve_plan"
    name = "Approve Project Plan"
    description = "Generate and approve a project goal plan for a lead"
    category = BlockCategory.DATABASE
    input_schema = ApprovePlanInput
    output_schema = ApprovePlanOutput

    async def run(self, input_data: ApprovePlanInput) -> ApprovePlanOutput:
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models import Lead, ProjectGoalPlan

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Lead).where(Lead.id == input_data.lead_id))
            lead = result.scalar_one_or_none()
            if not lead:
                return ApprovePlanOutput(success=False, summary=f"Lead #{input_data.lead_id} not found")

            plan = ProjectGoalPlan(
                lead_id=lead.id,
                status="APPROVED",
                summary=f"Project plan for {lead.name}: {(lead.requirement or '')[:200]}",
                created_at=datetime.now(timezone.utc),
            )
            db.add(plan)
            lead.status = "QUALIFIED"
            await db.commit()
            await db.refresh(plan)

        return ApprovePlanOutput(
            success=True,
            plan_id=plan.id,
            summary=plan.summary,
        )


# ---------------------------------------------------------------------------
# Trigger Build Block
# ---------------------------------------------------------------------------

class TriggerBuildInput(BlockSchema):
    lead_id: int = Field(..., description="Lead ID to trigger a project build for")
    project_type: Optional[str] = Field(None, description="Type of project (webapp, api, bot, etc.)")


class TriggerBuildOutput(BlockSchema):
    success: bool
    build_id: int = 0
    project_dir: str = ""
    message: str = ""


class TriggerBuildBlock(Block):
    id = "trigger_build"
    name = "Trigger Project Build"
    description = "Start an autonomous project build for an approved lead"
    category = BlockCategory.DATABASE
    input_schema = TriggerBuildInput
    output_schema = TriggerBuildOutput

    async def run(self, input_data: TriggerBuildInput) -> TriggerBuildOutput:
        try:
            from app.services.project_builder import ProjectBuilder
            builder = ProjectBuilder()
            result = await builder.build_for_lead(input_data.lead_id)
            return TriggerBuildOutput(
                success=True,
                build_id=result.get("build_id", 0),
                project_dir=result.get("project_dir", ""),
                message=result.get("message", "Build started"),
            )
        except Exception as exc:
            return TriggerBuildOutput(success=False, message=str(exc))


# ---------------------------------------------------------------------------
# Get Telemetry Block
# ---------------------------------------------------------------------------

class GetTelemetryInput(BlockSchema):
    period_days: int = Field(7, description="Number of days to include in telemetry report")


class GetTelemetryOutput(BlockSchema):
    total_leads: int = 0
    new_leads: int = 0
    hot_leads: int = 0
    builds_completed: int = 0
    revenue_total: float = 0.0
    period_days: int = 7


class GetTelemetryBlock(Block):
    id = "get_telemetry"
    name = "Get System Telemetry"
    description = "Retrieve key metrics: lead counts, builds, and revenue"
    category = BlockCategory.DATABASE
    input_schema = GetTelemetryInput
    output_schema = GetTelemetryOutput

    async def run(self, input_data: GetTelemetryInput) -> GetTelemetryOutput:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func, select
        from app.database import AsyncSessionLocal
        from app.models import Lead, ProjectBuild, PaymentRecord

        cutoff = datetime.now(timezone.utc) - timedelta(days=input_data.period_days)

        async with AsyncSessionLocal() as db:
            total_leads = (await db.execute(select(func.count(Lead.id)))).scalar_one()
            new_leads = (
                await db.execute(
                    select(func.count(Lead.id)).where(Lead.created_at >= cutoff)
                )
            ).scalar_one()
            hot_leads = (
                await db.execute(
                    select(func.count(Lead.id)).where(Lead.temperature == "HOT")
                )
            ).scalar_one()
            builds_completed = (
                await db.execute(
                    select(func.count(ProjectBuild.id)).where(
                        ProjectBuild.status == "COMPLETED"
                    )
                )
            ).scalar_one()
            revenue_total = (
                await db.execute(select(func.sum(PaymentRecord.amount)))
            ).scalar_one() or 0.0

        return GetTelemetryOutput(
            total_leads=total_leads,
            new_leads=new_leads,
            hot_leads=hot_leads,
            builds_completed=builds_completed,
            revenue_total=float(revenue_total),
            period_days=input_data.period_days,
        )


# ---------------------------------------------------------------------------
# Generate Invoice Block
# ---------------------------------------------------------------------------

class GenerateInvoiceInput(BlockSchema):
    lead_id: int = Field(..., description="Lead ID to generate invoice for")
    amount: float = Field(..., description="Invoice amount in USD")
    description: Optional[str] = Field(None, description="Invoice line-item description")


class GenerateInvoiceOutput(BlockSchema):
    success: bool
    invoice_number: str = ""
    pdf_path: str = ""
    message: str = ""


class GenerateInvoiceBlock(Block):
    id = "generate_invoice"
    name = "Generate Invoice"
    description = "Create a professional PDF invoice for a lead/project"
    category = BlockCategory.DATABASE
    input_schema = GenerateInvoiceInput
    output_schema = GenerateInvoiceOutput

    async def run(self, input_data: GenerateInvoiceInput) -> GenerateInvoiceOutput:
        try:
            from app.services.invoice_service import (
                create_invoice_pdf,
                generate_invoice_number,
            )
            from sqlalchemy import select
            from app.database import AsyncSessionLocal
            from app.models import Lead

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Lead).where(Lead.id == input_data.lead_id))
                lead = result.scalar_one_or_none()

            if not lead:
                return GenerateInvoiceOutput(success=False, message=f"Lead #{input_data.lead_id} not found")

            inv_num = generate_invoice_number()
            pdf_path = await create_invoice_pdf(
                invoice_number=inv_num,
                client_name=lead.name,
                client_email=lead.email or "",
                amount=input_data.amount,
                description=input_data.description or lead.requirement or "Consulting Services",
            )
            return GenerateInvoiceOutput(
                success=True,
                invoice_number=inv_num,
                pdf_path=pdf_path,
                message=f"Invoice {inv_num} generated for {lead.name}",
            )
        except Exception as exc:
            return GenerateInvoiceOutput(success=False, message=str(exc))


# ---------------------------------------------------------------------------
# Auto-register all DB blocks
# ---------------------------------------------------------------------------

_DB_BLOCKS = [
    QueryLeadsBlock(),
    GetLeadDetailBlock(),
    CreateLeadBlock(),
    UpdateLeadStatusBlock(),
    ApprovePlanBlock(),
    TriggerBuildBlock(),
    GetTelemetryBlock(),
    GenerateInvoiceBlock(),
]

for _block in _DB_BLOCKS:
    BlockRegistry.register(_block)
