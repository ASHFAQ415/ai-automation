"""ClientFollowUpAgent — Hermes autonomous warm lead nurturing.
Runs every 30 minutes. For WARM leads that have been sitting > 24 hours
with no conversion to PLAN_PENDING, sends a follow-up email asking for
clarification and cc's the owner via Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ActivityLog, Lead
from app.services.notifier import broadcast_ws, send_telegram

logger = logging.getLogger("followup_agent")

_followup_task: asyncio.Task | None = None
_INTERVAL_SECONDS = 1800  # 30 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _send_followup_email(lead_name: str, lead_email: str, requirement: str) -> bool:
    """Send follow-up email via Hermes Gmail SMTP (loaded from hermes/.env)."""
    if not settings.EMAIL_ADDRESS or not settings.EMAIL_PASSWORD:
        logger.warning("[FollowUpAgent] Email not configured — skipping.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Following up on your enquiry — {lead_name}"
        msg["From"] = f"Hermes AI Studio <{settings.EMAIL_ADDRESS}>"
        msg["To"] = lead_email

        plain_text = (
            f"Hi {lead_name},\n\n"
            f"Thank you for reaching out to us about your requirement:\n"
            f"\"{requirement[:300]}\"\n\n"
            f"We'd love to understand your project better and share a detailed proposal.\n"
            f"Could you let us know:\n"
            f"  1. Your preferred timeline to start?\n"
            f"  2. Any specific features or integrations you need?\n"
            f"  3. Your approximate budget range?\n\n"
            f"We're ready to build exactly what you need.\n\n"
            f"Best regards,\n"
            f"Mohd Ashfaq\n"
            f"AI Automation Studio"
        )
        html_text = f"""
        <html><body style="font-family: 'Segoe UI', Arial, sans-serif; background: #f8fafc; padding: 30px;">
        <div style="max-width: 580px; margin: 0 auto; background: white; border-radius: 12px; padding: 32px; border: 1px solid #e2e8f0;">
          <h2 style="color: #1e293b; margin-bottom: 6px;">Hi {lead_name} 👋</h2>
          <p style="color: #475569;">Thank you for reaching out about your project.</p>
          <div style="background: #f1f5f9; border-left: 4px solid #3b82f6; padding: 14px 18px; border-radius: 8px; margin: 20px 0;">
            <strong style="color: #1e293b;">Your Requirement:</strong>
            <p style="color: #475569; margin: 6px 0 0;">{requirement[:300]}</p>
          </div>
          <p style="color: #475569;">We'd love to send you a detailed proposal. Could you share:</p>
          <ul style="color: #475569;">
            <li>Your preferred timeline to start?</li>
            <li>Any specific integrations needed?</li>
            <li>Approximate budget range?</li>
          </ul>
          <a href="mailto:{settings.EMAIL_ADDRESS}?subject=Re: {lead_name} Project Details"
             style="display:inline-block; background:#3b82f6; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:600; margin-top:16px;">
            Reply with Details
          </a>
          <p style="color:#94a3b8; font-size:12px; margin-top:24px;">
            Sent by Hermes AI — Autonomous Project Builder | Mohd Ashfaq
          </p>
        </div>
        </body></html>
        """
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_text, "html"))

        with smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD)
            server.sendmail(settings.EMAIL_ADDRESS, lead_email, msg.as_string())

        logger.info("[FollowUpAgent] Follow-up email sent to %s (%s)", lead_name, lead_email)
        return True
    except Exception as e:
        logger.error("[FollowUpAgent] Email send failed for %s: %s", lead_email, e)
        return False


async def _run_followup_cycle() -> None:
    """Find WARM leads > 24 hours old and send follow-up emails."""
    cutoff = _now() - timedelta(hours=24)
    followed_up: list[str] = []

    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(Lead)
            .where(Lead.temperature == "WARM")
            .where(Lead.status == "SCORED")
            .where(Lead.email.isnot(None))
            .where(Lead.created_at < cutoff)
        )
        warm_leads = q.scalars().all()

        for lead in warm_leads:
            # Check if we already sent a follow-up for this lead
            log_q = await db.execute(
                select(ActivityLog)
                .where(ActivityLog.lead_id == lead.id)
                .where(ActivityLog.event_type == "FOLLOWUP_EMAIL_SENT")
            )
            already_sent = log_q.scalar_one_or_none()
            if already_sent:
                continue

            success = _send_followup_email(lead.name, lead.email, lead.requirement or "")
            if success:
                log = ActivityLog(
                    lead_id=lead.id,
                    event_type="FOLLOWUP_EMAIL_SENT",
                    title=f"Follow-up email sent to {lead.name}",
                    details=f"Sent to {lead.email} after 24h as WARM lead.",
                    channel="EMAIL",
                )
                db.add(log)
                followed_up.append(f"Lead #{lead.id} — {lead.name} ({lead.email})")

        if followed_up:
            await db.commit()

    if followed_up:
        tg_msg = (
            f"📧 *HERMES FOLLOW-UP AGENT*\n"
            f"Sent follow-up emails to {len(followed_up)} WARM lead(s):\n"
            + "\n".join(f"• {x}" for x in followed_up)
        )
        await send_telegram(tg_msg)
        await broadcast_ws("FOLLOWUP_SENT", {"count": len(followed_up), "leads": followed_up})
    else:
        logger.info("[FollowUpAgent] No WARM leads needing follow-up right now.")


async def _followup_loop() -> None:
    logger.info("[FollowUpAgent] Hermes Follow-Up Agent started (interval: %ds)", _INTERVAL_SECONDS)
    while True:
        try:
            await _run_followup_cycle()
        except Exception as e:
            logger.error("[FollowUpAgent] Error: %s", e)
        await asyncio.sleep(_INTERVAL_SECONDS)


def start_followup_agent() -> None:
    global _followup_task
    if _followup_task is None or _followup_task.done():
        _followup_task = asyncio.create_task(_followup_loop())
        logger.info("[FollowUpAgent] Background task launched.")


async def stop_followup_agent() -> None:
    global _followup_task
    if _followup_task:
        _followup_task.cancel()
        try:
            await _followup_task
        except asyncio.CancelledError:
            pass
        _followup_task = None
        logger.info("[FollowUpAgent] Stopped.")
