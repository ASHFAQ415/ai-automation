"""Hermes Telegram Interactive Bot: Command router, inline approvals, and AI work chat."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import desc, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ActivityLog, Lead, ProjectBuild, ProjectGoalPlan
from app.services.ai_engine import call_llm
from app.services.notifier import broadcast_ws
from app.services.project_builder import execute_autonomous_build
from app.services.react_agent import run_react_agent

logger = logging.getLogger("telegram_bot")

_polling_task: asyncio.Task | None = None
_stop_event = asyncio.Event()


def get_telegram_api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


async def send_tg_message(
    chat_id: str | int,
    text: str,
    parse_mode: str | None = "Markdown",
    reply_markup: dict[str, Any] | None = None
) -> bool:
    """Send a message to a specific chat with markdown fallback."""
    url = get_telegram_api_url("sendMessage")
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # If markdown parse error (HTTP 400), retry as raw plain text
            if resp.status_code == 400 and parse_mode:
                payload.pop("parse_mode", None)
                resp2 = await client.post(url, json=payload)
                return resp2.status_code == 200
            logger.warning("Telegram sendMessage error: %s", resp.text)
            return False
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False


async def send_typing_action(chat_id: str | int):
    """Send 'typing' action so user sees Hermes thinking."""
    url = get_telegram_api_url("sendChatAction")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"chat_id": chat_id, "action": "typing"})
    except Exception:
        pass


async def answer_callback_query(callback_query_id: str, text: str | None = None):
    """Acknowledge a Telegram button callback."""
    url = get_telegram_api_url("answerCallbackQuery")
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning("Failed to answer callback query: %s", e)


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def handle_start_or_help(chat_id: int):
    text = (
        "👋 *Greetings, Ashfaq! I am Hermes.*\n"
        "Your autonomous AI Engineering Director & Lead Project Architect.\n\n"
        "I monitor high-value business leads, architect technical implementation plans, "
        "and build production-grade applications autonomously using local *Qwen 3 (4B)*.\n\n"
        "⚡ *Executive Command Center:*\n"
        "• `/pipeline` — Review pending plans & trigger builds\n"
        "• `/approve <lead_id>` — Approve Stage 1 architecture plan\n"
        "• `/reject <lead_id>` — Reject implementation plan\n"
        "• `/build <lead_id>` — Authorize Stage 2 autonomous build\n"
        "• `/rebuild <lead_id>` — Force clean domain-faithful rebuild of a project\n"
        "• `/cancel [build_id]` — Cancel an active project build\n"
        "• `/audit <lead_id>` — Detailed AI scoring breakdown for a lead\n"
        "• `/daily` — Today's business intelligence digest\n"
        "• `/leads` — List qualified enterprise leads\n"
        "• `/projects` — List generated software projects\n"
        "• `/status` — System telemetry & local Ollama health\n\n"
        "💬 *Chat with me:* You can also send me any message to discuss code, "
        "review architecture, or check pipeline status!"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 View Pipeline", "callback_data": "pipeline"}, {"text": "📊 System Status", "callback_data": "status"}],
            [{"text": "📁 Built Projects", "callback_data": "projects"}, {"text": "👥 Active Leads", "callback_data": "leads"}]
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


async def handle_status(chat_id: int):
    from sqlalchemy import func
    async with AsyncSessionLocal() as db:
        # BUG #16 FIX: Use scalar COUNT queries instead of loading all rows into memory.
        # Original code did len(rows.all()) which is O(N) memory at scale.
        lead_count = (await db.execute(select(func.count(Lead.id)))).scalar() or 0
        approved_plans = (await db.execute(
            select(func.count(ProjectGoalPlan.id)).where(ProjectGoalPlan.approval_status == "APPROVED")
        )).scalar() or 0
        active_builds = (await db.execute(
            select(func.count(ProjectBuild.id)).where(
                ProjectBuild.status.in_(["SCAFFOLDING", "GENERATING_CODE", "COMPILING", "BUILDING"])
            )
        )).scalar() or 0
        completed_builds = (await db.execute(
            select(func.count(ProjectBuild.id)).where(ProjectBuild.status == "COMPLETED")
        )).scalar() or 0

    text = (
        "📊 *HERMES SYSTEM STATUS & TELEMETRY*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Active Model:* `{settings.OLLAMA_MODEL}` (Local Ollama)\n"
        f"🚀 *Inference Engine:* NVIDIA RTX 3050 (Port 11434)\n"
        f"👥 *Total Leads:* {lead_count}\n"
        f"📝 *Approved Plans:* {approved_plans}\n"
        f"⚙️ *Active Builds:* {active_builds}\n"
        f"✅ *Completed Deliverables:* {completed_builds}\n"
        f"🌐 *Dashboard:* `http://localhost:8000`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "All autonomous agents operating normally."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 View Pipeline", "callback_data": "pipeline"}],
            [{"text": "📁 Explore Projects", "callback_data": "projects"}]
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


async def handle_leads(chat_id: int):
    async with AsyncSessionLocal() as db:
        leads = (await db.execute(select(Lead).order_by(desc(Lead.created_at)).limit(10))).scalars().all()

    if not leads:
        await send_tg_message(chat_id, "ℹ️ No leads currently in the database.")
        return

    lines = ["👥 *RECENT ENTERPRISE LEADS*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    inline_buttons = []

    for l in leads:
        company_tag = f" ({l.company})" if l.company else ""
        temp_emoji = "🔥" if l.temperature == "HOT" else ("⚡" if l.temperature == "WARM" else "❄️")
        lines.append(
            f"• *#{l.id}* {temp_emoji} *{l.name}*{company_tag}\n"
            f"  Score: *{l.score}/100* | Status: `{l.status}`\n"
            f"  Budget: {l.budget or 'Flexible'} | Req: _{l.requirement[:80]}..._\n"
        )
        if l.status in ["PLAN_PENDING", "SCORED", "NEW"]:
            inline_buttons.append([
                {"text": f"✓ Approve #{l.id}", "callback_data": f"approve:{l.id}"},
                {"text": f"🚀 Build #{l.id}", "callback_data": f"build:{l.id}"}
            ])
        elif l.status == "PLAN_APPROVED":
            inline_buttons.append([
                {"text": f"🚀 Start Build for Lead #{l.id}", "callback_data": f"build:{l.id}"}
            ])

    reply_markup = {"inline_keyboard": inline_buttons} if inline_buttons else None
    await send_tg_message(chat_id, "\n".join(lines), reply_markup=reply_markup)


async def handle_pipeline(chat_id: int):
    """List leads awaiting Stage 1 plan approval or Stage 2 build trigger."""
    async with AsyncSessionLocal() as db:
        leads = (await db.execute(select(Lead).order_by(Lead.id))).scalars().all()
        plans = (await db.execute(select(ProjectGoalPlan))).scalars().all()
        plan_by_lead = {p.lead_id: p for p in plans}

    pending_approval = []
    ready_to_build = []
    building = []

    for l in leads:
        p = plan_by_lead.get(l.id)
        if l.status in ["NEW", "SCORED", "PLAN_PENDING"] or (p and p.approval_status == "PENDING"):
            pending_approval.append((l, p))
        elif l.status == "PLAN_APPROVED" or (p and p.approval_status == "APPROVED" and l.status != "BUILDING" and l.status != "BUILT"):
            ready_to_build.append((l, p))
        elif l.status == "BUILDING":
            building.append((l, p))

    if not pending_approval and not ready_to_build and not building:
        await send_tg_message(
            chat_id,
            "✨ *Pipeline is clear!* No pending plans or pending builds.\n"
            "Submit a new lead on the dashboard (`http://localhost:8000`) or ask me to generate one."
        )
        return

    msg = ["📋 *HERMES APPROVAL & BUILD PIPELINE*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    inline_buttons = []

    if pending_approval:
        msg.append("\n⏳ *Stage 1: Pending Plan Approval*")
        for l, p in pending_approval:
            title = p.title if p else "Implementation Plan"
            msg.append(f"• *Lead #{l.id}:* {title}\n  Client: {l.name} | Score: {l.score}/100")
            inline_buttons.append([
                {"text": f"✓ Approve #{l.id}", "callback_data": f"approve:{l.id}"},
                {"text": f"✕ Reject #{l.id}", "callback_data": f"reject:{l.id}"}
            ])

    if ready_to_build:
        msg.append("\n🚀 *Stage 2: Approved & Ready to Build*")
        for l, p in ready_to_build:
            title = p.title if p else "Project Plan"
            msg.append(f"• *Lead #{l.id}:* {title}\n  Ready for autonomous generation.")
            inline_buttons.append([
                {"text": f"🚀 Launch Autonomous Build #{l.id}", "callback_data": f"build:{l.id}"}
            ])

    if building:
        msg.append("\n⚙️ *Active Builds in Progress*")
        for l, p in building:
            title = p.title if p else "Project"
            msg.append(f"• *Lead #{l.id}:* {title} (Compiling...)")
            inline_buttons.append([
                {"text": f"⏹ Cancel Build #{l.id}", "callback_data": f"cancel:{l.id}"}
            ])

    reply_markup = {"inline_keyboard": inline_buttons} if inline_buttons else None
    await send_tg_message(chat_id, "\n".join(msg), reply_markup=reply_markup)


async def handle_projects(chat_id: int):
    async with AsyncSessionLocal() as db:
        builds = (await db.execute(select(ProjectBuild).order_by(desc(ProjectBuild.created_at)).limit(10))).scalars().all()

    if not builds:
        await send_tg_message(chat_id, "ℹ️ No projects built yet. Use `/build <lead_id>` to generate one!")
        return

    msg = ["📁 *AUTONOMOUS PROJECTS & BUILDS*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    inline_buttons = []

    for b in builds:
        status_emoji = "✅" if b.status == "COMPLETED" else ("⚙️" if "BUILD" in b.status or "GEN" in b.status or "SCAFFOLD" in b.status else "❌")
        files_count = len(json.loads(b.generated_files)) if b.generated_files else 0
        msg.append(
            f"{status_emoji} *Project #{b.id}:* {b.project_name}\n"
            f"  Status: `{b.status}` ({b.progress_percent}%)\n"
            f"  Files: {files_count} generated\n"
            f"  Path: `{b.project_path}`\n"
        )
        if b.status in ["SCAFFOLDING", "GENERATING_CODE", "COMPILING", "BUILDING"]:
            inline_buttons.append([{"text": f"⏹ Cancel Build #{b.id}", "callback_data": f"cancel:{b.id}"}])

    reply_markup = {"inline_keyboard": inline_buttons} if inline_buttons else None
    await send_tg_message(chat_id, "\n".join(msg), reply_markup=reply_markup)


async def execute_approve_lead(lead_id: int, chat_id: int):
    """Execute Stage 1 plan approval."""
    async with AsyncSessionLocal() as db:
        lead_query = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_query.scalar_one_or_none()
        if not lead:
            await send_tg_message(chat_id, f"❌ Lead #{lead_id} not found.")
            return

        # BUG #4 FIX: Use .scalars().first() with ORDER BY — scalar_one_or_none() raises
        # MultipleResultsFound when multiple plans exist after a /rebuild.
        plan_query = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_query.scalars().first()
        if not plan:
            await send_tg_message(chat_id, f"❌ No implementation plan found for Lead #{lead_id}.")
            return

        plan.approval_status = "APPROVED"
        plan.approved_at = _now()
        lead.status = "PLAN_APPROVED"

        log = ActivityLog(
            lead_id=lead.id,
            event_type="PLAN_REVIEWED",
            title=f"Plan for {lead.name} APPROVED via Telegram",
            details="Approved by Ashfaq via @Preetikaa_bot",
            channel="TELEGRAM"
        )
        db.add(log)
        await db.commit()

    await broadcast_ws("PLAN_REVIEWED", {"lead_id": lead_id, "decision": "APPROVED"})

    text = (
        f"✅ *STAGE 1 PLAN APPROVED!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Project:* {plan.title}\n"
        f"👤 *Client:* {lead.name}\n"
        f"📦 *Deliverable:* {plan.target_deliverable}\n\n"
        f"Ready for Stage 2 autonomous code generation!"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": f"🚀 Launch Autonomous Build #{lead_id}", "callback_data": f"build:{lead_id}"}]
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


async def execute_reject_lead(lead_id: int, chat_id: int):
    """Execute Stage 1 plan rejection."""
    async with AsyncSessionLocal() as db:
        lead_query = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_query.scalar_one_or_none()
        if not lead:
            await send_tg_message(chat_id, f"❌ Lead #{lead_id} not found.")
            return

        # BUG #4 FIX (reject): Use .scalars().first() with ORDER BY
        plan_query = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_query.scalars().first()
        if plan:
            plan.approval_status = "REJECTED"
        lead.status = "REJECTED"

        log = ActivityLog(
            lead_id=lead.id,
            event_type="PLAN_REVIEWED",
            title=f"Plan for {lead.name} REJECTED via Telegram",
            details="Rejected by Ashfaq via @Preetikaa_bot",
            channel="TELEGRAM"
        )
        db.add(log)
        await db.commit()

    await broadcast_ws("PLAN_REVIEWED", {"lead_id": lead_id, "decision": "REJECTED"})
    await send_tg_message(chat_id, f"❌ Project plan for Lead #{lead_id} marked as REJECTED.")


async def execute_build_lead(lead_id: int, chat_id: int):
    """Execute Stage 2 autonomous build."""
    from app.services.project_builder import execute_autonomous_build

    async with AsyncSessionLocal() as db:
        lead_query = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_query.scalar_one_or_none()
        if not lead:
            await send_tg_message(chat_id, f"❌ Lead #{lead_id} not found.")
            return

        # BUG #4 FIX (build): Use .scalars().first() with ORDER BY
        plan_query = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_query.scalars().first()
        if not plan:
            await send_tg_message(chat_id, f"❌ Cannot build: no plan found for Lead #{lead_id}.")
            return

        # Auto-approve plan if pending
        if plan.approval_status != "APPROVED":
            plan.approval_status = "APPROVED"
            plan.approved_at = datetime.now(timezone.utc)

        lead.status = "BUILDING"
        await db.commit()

    # Launch build in background task
    async def _run():
        async with AsyncSessionLocal() as db_session:
            try:
                await execute_autonomous_build(lead_id, db_session)
            except Exception as e:
                logger.error("Build failed in background: %s", e)
                await send_tg_message(chat_id, f"⚠️ Build error for Lead #{lead_id}: {str(e)[:200]}")

    asyncio.create_task(_run())

    text = (
        f"🚀 *AUTONOMOUS BUILD INITIATED!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Project:* {plan.title}\n"
        f"👤 *Client:* {lead.name}\n"
        f"🤖 *Compiler:* Hermes Local `{settings.OLLAMA_MODEL}`\n\n"
        f"Hermes is scaffolding files, writing production code, and running compilation checks.\n"
        f"You will receive live milestone updates as code is created."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "⏹ Cancel Build", "callback_data": f"cancel:{lead_id}"}],
            [{"text": "📊 Live Status", "callback_data": "status"}]
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


async def execute_rebuild_lead(lead_id: int, chat_id: int):
    """Regenerate domain-faithful implementation plan and execute clean autonomous rebuild."""
    from app.services.ai_engine import generate_project_plan
    async with AsyncSessionLocal() as db:
        lead_query = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_query.scalar_one_or_none()
        if not lead:
            await send_tg_message(chat_id, f"❌ Lead #{lead_id} not found.")
            return

        lead_data = {
            "id": lead.id,
            "name": lead.name,
            "company": lead.company,
            "requirement": lead.requirement,
            "budget": lead.budget,
            "timeline": lead.timeline,
        }
        analysis = json.loads(lead.ai_analysis) if lead.ai_analysis else {}
        new_plan = await generate_project_plan(lead_data, analysis)

        # BUG #4 FIX (rebuild): Use .scalars().first() with ORDER BY
        plan_query = await db.execute(
            select(ProjectGoalPlan)
            .where(ProjectGoalPlan.lead_id == lead_id)
            .order_by(ProjectGoalPlan.id.desc())
        )
        plan = plan_query.scalars().first()
        if not plan:
            plan = ProjectGoalPlan(lead_id=lead_id)
            db.add(plan)

        plan.title = new_plan.get("title", f"{lead.name} Application")
        plan.target_deliverable = new_plan.get("target_deliverable", "Full-Stack System")
        plan.core_features = json.dumps(new_plan.get("core_features", []))
        plan.tech_stack = json.dumps(new_plan.get("tech_stack", []))
        plan.architecture_overview = new_plan.get("architecture_overview", "")
        plan.implementation_steps = json.dumps(new_plan.get("implementation_steps", []))
        plan.approval_status = "APPROVED"
        plan.approved_at = _now()
        lead.status = "BUILDING"
        await db.commit()

        plan_title = plan.title
        target_del = plan.target_deliverable
        client_name = lead.name

    async def _run():
        async with AsyncSessionLocal() as db_session:
            try:
                await execute_autonomous_build(lead_id, db_session)
            except Exception as e:
                logger.error("Rebuild failed in background: %s", e)
                await send_tg_message(chat_id, f"⚠️ Rebuild error for Lead #{lead_id}: {str(e)[:200]}")

    # BUG #10 FIX: asyncio.get_event_loop() is deprecated in Python 3.10+ inside
    # async contexts and raises RuntimeError in Python 3.12+. Use asyncio.create_task() directly.
    asyncio.create_task(_run())

    text = (
        f"⚡ *AUTONOMOUS REBUILD INITIATED!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Project:* {plan_title}\n"
        f"👤 *Client:* {client_name}\n"
        f"🎯 *Deliverable:* {target_del}\n"
        f"🤖 *Compiler:* Hermes Local `{settings.OLLAMA_MODEL}` on NVIDIA RTX 3050\n\n"
        f"Hermes is executing a clean domain-accurate rebuild. All data structures and UI components are locked to the specific client requirement."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "⏹ Cancel Rebuild", "callback_data": f"cancel:{lead_id}"}],
            [{"text": "📊 Live Status", "callback_data": "status"}]
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


async def execute_cancel_build(identifier: int | None, chat_id: int):
    """Cancel an active project build by build_id or lead_id."""
    async with AsyncSessionLocal() as db:
        build = None
        if identifier:
            # Check by build ID first
            b_query = await db.execute(select(ProjectBuild).where(ProjectBuild.id == identifier))
            build = b_query.scalar_one_or_none()
            if not build:
                # Check by lead ID
                b_query2 = await db.execute(select(ProjectBuild).where(ProjectBuild.lead_id == identifier).order_by(desc(ProjectBuild.id)))
                build = b_query2.scalar_one_or_none()
        else:
            # Find latest active build
            b_query3 = await db.execute(
                select(ProjectBuild).where(ProjectBuild.status.in_(["SCAFFOLDING", "GENERATING_CODE", "COMPILING", "BUILDING"]))
                .order_by(desc(ProjectBuild.id))
            )
            build = b_query3.scalar_one_or_none()

        if not build:
            await send_tg_message(chat_id, "ℹ️ No active builds found to cancel.")
            return

        build.status = "CANCELLED"
        build.build_logs = (build.build_logs or "") + f"[{_now().isoformat()}] ✕ Cancelled via Telegram by Ashfaq.\n"

        lead_query = await db.execute(select(Lead).where(Lead.id == build.lead_id))
        lead = lead_query.scalar_one_or_none()
        if lead:
            lead.status = "PLAN_APPROVED"

        await db.commit()
        await broadcast_ws("BUILD_CANCELLED", {"build_id": build.id})

    text = (
        f"⏹ *BUILD CANCELLED*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Project: *{build.project_name}* (Build #{build.id})\n"
        f"Lead status reverted to `PLAN_APPROVED`. You can re-run with `/build {build.lead_id}`."
    )
    await send_tg_message(chat_id, text)


# ---------------------------------------------------------------------------
# Lead Scoring Audit Agent (/audit <lead_id>)
# ---------------------------------------------------------------------------

async def handle_audit(lead_id: int, chat_id: int):
    """Show detailed AI scoring breakdown for a lead — LeadScoringAuditAgent."""
    import json
    async with AsyncSessionLocal() as db:
        q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = q.scalar_one_or_none()
        if not lead:
            await send_tg_message(chat_id, f"❌ Lead #{lead_id} not found.")
            return

        analysis = json.loads(lead.ai_analysis) if lead.ai_analysis else {}

    score = lead.score or analysis.get("score", 0)
    temperature = lead.temperature or analysis.get("temperature", "WARM")
    reasons = analysis.get("qualification_reasons", [])
    risks = analysis.get("risks", [])
    strengths = analysis.get("strengths", [])
    budget_quality = analysis.get("budget_quality", "Unknown")

    reasons_text = "\n".join(f"  • {r}" for r in reasons[:5]) or "  • No detailed reasoning available"
    strengths_text = "\n".join(f"  ✅ {s}" for s in strengths[:3]) or "  • N/A"
    risks_text = "\n".join(f"  ⚠️ {r}" for r in risks[:3]) or "  • None identified"

    temp_emoji = {"HOT": "🔥", "WARM": "☀️", "COLD": "🧊"}.get(temperature, "📊")
    text = (
        f"🔍 *LEAD #{lead_id} — AI SCORING AUDIT*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Client:* {lead.name}\n"
        f"🏢 *Company:* {lead.company or 'N/A'}\n"
        f"📧 *Email:* {lead.email or 'N/A'}\n"
        f"📞 *Phone:* {lead.phone or 'N/A'}\n\n"
        f"📊 *Score:* {score}/100 {temp_emoji} `{temperature}`\n"
        f"💰 *Budget Quality:* {budget_quality}\n"
        f"⏱ *Timeline:* {lead.timeline or 'N/A'}\n\n"
        f"*Why this score:*\n{reasons_text}\n\n"
        f"*Strengths detected:*\n{strengths_text}\n\n"
        f"*Risks identified:*\n{risks_text}\n\n"
        f"📝 *Requirement:* {(lead.requirement or '')[:200]}"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": f"✓ Approve Plan #{lead_id}", "callback_data": f"approve:{lead_id}"},
             {"text": f"🚀 Build #{lead_id}", "callback_data": f"build:{lead_id}"}],
            [{"text": "📋 All Leads", "callback_data": "leads"}],
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Daily Digest Agent (/daily)
# ---------------------------------------------------------------------------

async def handle_daily_digest(chat_id: int):
    """Send today's business intelligence digest — DailyDigestAgent."""
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as db:
        all_leads_q = await db.execute(select(Lead))
        all_leads = all_leads_q.scalars().all()
        today_leads = [l for l in all_leads if l.created_at and l.created_at >= today]
        hot_count = sum(1 for l in all_leads if l.temperature == "HOT")
        warm_count = sum(1 for l in all_leads if l.temperature == "WARM")
        pending = sum(1 for l in all_leads if l.status in ("PLAN_PENDING", "PLAN_APPROVED"))
        building = sum(1 for l in all_leads if l.status == "BUILDING")

        builds_q = await db.execute(
            select(ProjectBuild).where(ProjectBuild.status == "COMPLETED").order_by(desc(ProjectBuild.created_at)).limit(3)
        )
        recent_builds = builds_q.scalars().all()

    builds_text = "\n".join(
        f"  ✅ `{b.project_name}` (Lead #{b.lead_id})" for b in recent_builds
    ) or "  None today"

    text = (
        f"📅 *HERMES DAILY INTELLIGENCE DIGEST*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗓 {_now().strftime('%A, %d %B %Y %H:%M')} IST\n\n"
        f"*📊 Lead Pipeline:*\n"
        f"  🔥 HOT Deals: `{hot_count}`\n"
        f"  ☀️ WARM Leads: `{warm_count}`\n"
        f"  🆕 New Today: `{len(today_leads)}`\n"
        f"  ⏳ Pending Approval: `{pending}`\n"
        f"  🔨 Building: `{building}`\n\n"
        f"*🏗 Recent Builds:*\n{builds_text}\n\n"
        f"*🤖 Engine Status:*\n"
        f"  Local AI: `{settings.OLLAMA_MODEL}` on RTX 3050\n"
        f"  Fallback: Groq Cloud Active\n\n"
        f"All systems nominal. Type any message to discuss work with Hermes."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 Pipeline", "callback_data": "pipeline"},
             {"text": "📦 Projects", "callback_data": "projects"}],
            [{"text": "📊 Leads", "callback_data": "leads"}],
        ]
    }
    await send_tg_message(chat_id, text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Conversational Work Chat Engine (Autonomous ReAct Agent)
# ---------------------------------------------------------------------------

async def handle_work_chat(user_msg: str, chat_id: int):
    """Handle natural language conversation via the autonomous ReAct agent."""
    await send_typing_action(chat_id)

    # Check for direct rebuild intent shortcut
    lower_msg = user_msg.lower().strip()
    if "rebuild" in lower_msg:
        import re
        m = re.search(r"rebuild\s+(?:lead\s+|project\s+)?(\d+)", lower_msg)
        if m:
            lead_id = int(m.group(1))
            await execute_rebuild_lead(lead_id, chat_id)
            return

    async def _on_status(status_msg: str):
        await send_typing_action(chat_id)
        logger.info("Bot ReAct Status [%s]: %s", chat_id, status_msg)

    session_id = f"tg_{chat_id}"
    try:
        reply = await run_react_agent(session_id=session_id, user_message=user_msg, on_status=_on_status)
    except Exception as e:
        logger.exception("Hermes ReAct work chat failed: %s", e)
        reply = f"❌ Encountered an agent execution error: {str(e)[:200]}"

    await send_tg_message(chat_id, reply)



# ---------------------------------------------------------------------------
# Message Dispatcher & Update Processor
# ---------------------------------------------------------------------------

def is_authorized(user_id: int | str) -> bool:
    """Verify update is from configured authorized user."""
    auth_id = str(settings.TELEGRAM_CHAT_ID).strip()
    return str(user_id).strip() == auth_id


async def process_update(update: dict[str, Any]):
    """Process an incoming Telegram update (message or callback_query)."""
    # 1. Handle Inline Keyboard Button clicks
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        data = cb.get("data", "")
        sender_id = cb.get("from", {}).get("id")
        chat_id = cb.get("message", {}).get("chat", {}).get("id", sender_id)

        if not is_authorized(sender_id):
            await answer_callback_query(cb_id, "Access Denied: Private Hermes Instance")
            return

        await answer_callback_query(cb_id, "Processing...")

        if data.startswith("approve:"):
            lead_id = int(data.split(":")[1])
            await execute_approve_lead(lead_id, chat_id)
        elif data.startswith("build:"):
            lead_id = int(data.split(":")[1])
            await execute_build_lead(lead_id, chat_id)
        elif data.startswith("reject:"):
            lead_id = int(data.split(":")[1])
            await execute_reject_lead(lead_id, chat_id)
        elif data.startswith("cancel:"):
            bid = int(data.split(":")[1])
            await execute_cancel_build(bid, chat_id)
        elif data == "pipeline":
            await handle_pipeline(chat_id)
        elif data == "projects":
            await handle_projects(chat_id)
        elif data == "leads":
            await handle_leads(chat_id)
        elif data == "status":
            await handle_status(chat_id)
        return

    # 2. Handle Text Messages
    if "message" in update:
        msg = update["message"]
        sender_id = msg.get("from", {}).get("id")
        chat_id = msg.get("chat", {}).get("id", sender_id)
        text = msg.get("text", "").strip()

        if not text:
            return

        if not is_authorized(sender_id):
            logger.warning("Unauthorized access attempt from Telegram ID: %s", sender_id)
            await send_tg_message(chat_id, "🔒 *Access Denied.*\nThis Hermes Bot instance is private to Ashfaq.")
            return

        # Slash Command Routing
        lower_text = text.lower()
        if lower_text in ["/start", "/help"]:
            await handle_start_or_help(chat_id)
        elif lower_text == "/pipeline":
            await handle_pipeline(chat_id)
        elif lower_text == "/leads":
            await handle_leads(chat_id)
        elif lower_text == "/projects":
            await handle_projects(chat_id)
        elif lower_text == "/status":
            await handle_status(chat_id)
        elif lower_text.startswith("/approve"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                await execute_approve_lead(int(parts[1]), chat_id)
            else:
                await send_tg_message(chat_id, "Usage: `/approve <lead_id>` (e.g., `/approve 1`)")
        elif lower_text.startswith("/reject"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                await execute_reject_lead(int(parts[1]), chat_id)
            else:
                await send_tg_message(chat_id, "Usage: `/reject <lead_id>` (e.g., `/reject 1`)")
        elif lower_text.startswith("/build"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                await execute_build_lead(int(parts[1]), chat_id)
            else:
                await send_tg_message(chat_id, "Usage: `/build <lead_id>` (e.g., `/build 1`)")
        elif lower_text.startswith("/rebuild"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                await execute_rebuild_lead(int(parts[1]), chat_id)
            else:
                await send_tg_message(chat_id, "Usage: `/rebuild <lead_id>` (e.g., `/rebuild 2`)")
        elif lower_text.startswith("/cancel"):
            parts = text.split()
            target_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            await execute_cancel_build(target_id, chat_id)
        elif lower_text.startswith("/audit"):
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                await handle_audit(int(parts[1]), chat_id)
            else:
                await send_tg_message(chat_id, "Usage: `/audit <lead_id>` — shows AI scoring breakdown")
        elif lower_text == "/daily":
            await handle_daily_digest(chat_id)
        else:
            # Conversational Chat about work with Hermes
            await handle_work_chat(text, chat_id)


# ---------------------------------------------------------------------------
# Long Polling Loop
# ---------------------------------------------------------------------------

async def _poll_loop():
    """Continuous long-polling loop for Telegram Bot updates."""
    offset = 0
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.warning("Telegram polling disabled: No TELEGRAM_BOT_TOKEN provided.")
        return

    logger.info("Hermes Telegram Bot Polling started for token: %s...", token[:12])
    
    # Send a startup notification to Ashfaq
    try:
        if settings.TELEGRAM_CHAT_ID:
            await send_tg_message(
                settings.TELEGRAM_CHAT_ID,
                "⚡ *HERMES BOT ONLINE & CONNECTED*\n"
                f"Local Engine: `{settings.OLLAMA_MODEL}` on RTX 3050.\n"
                "I am listening for your commands and work inquiries!\n"
                "Type `/pipeline`, `/status`, or send any message to chat."
            )
    except Exception as e:
        logger.warning("Could not send startup greeting: %s", e)

    while not _stop_event.is_set():
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params = {"offset": offset, "timeout": 20}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        update_id = update["update_id"]
                        offset = max(offset, update_id + 1)
                        try:
                            await process_update(update)
                        except Exception as ex:
                            logger.error("Error processing Telegram update: %s", ex, exc_info=True)
                elif resp.status_code == 409:
                    logger.warning("Telegram conflict (another instance polling). Waiting 10s...")
                    await asyncio.sleep(10)
                else:
                    await asyncio.sleep(2)
        except (httpx.RequestError, httpx.TimeoutException):
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Telegram polling error: %s", e)
            await asyncio.sleep(3)


def start_telegram_bot():
    """Launch the Telegram bot poller task."""
    global _polling_task
    _stop_event.clear()
    if _polling_task is None or _polling_task.done():
        _polling_task = asyncio.create_task(_poll_loop())
        logger.info("Hermes Telegram Bot background task launched.")


async def stop_telegram_bot():
    """Stop the Telegram bot poller gracefully."""
    global _polling_task
    _stop_event.set()
    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None
        logger.info("Hermes Telegram Bot stopped.")
