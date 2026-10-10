"""WhatsApp Webhook Router — Hermes Lead Ingestion via Baileys Bridge.
Receives POST webhooks from the WhatsApp Baileys bridge (hermes/scripts/whatsapp-bridge)
and auto-creates leads in the Graph Engine pipeline.
Authorized users only (configured in hermes/.env: WHATSAPP_ALLOWED_USERS).

H-2 Fix: WHATSAPP_BRIDGE_SECRET header validates the bridge is the legitimate sender.
M-3 Fix: Uses BackgroundTasks instead of asyncio.create_task for proper error handling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.config import settings
from app.database import AsyncSessionLocal
from app.graph.engine import workflow_graph
from app.models import Lead
from app.services.notifier import broadcast_ws, send_telegram

logger = logging.getLogger("whatsapp_webhook")
router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_phone(raw: str) -> str:
    """Normalize a phone number to digits only (strips +, spaces, dashes).

    WIRING FIX: This function was called on line 93 but was never defined,
    causing a NameError on every incoming WhatsApp webhook message.
    """
    return raw.replace("+", "").replace("-", "").replace(" ", "").strip()


def _get_allowed_numbers() -> list[str]:
    """Parse allowed WhatsApp numbers from hermes/.env WHATSAPP_ALLOWED_USERS."""
    raw = settings.WHATSAPP_ALLOWED_USERS or ""
    return [n.strip().replace("+", "").replace("-", "").replace(" ", "") for n in raw.split(",") if n.strip()]


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


@router.post("/whatsapp")
@rate_limit("30/minute")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_bridge_signature: str | None = Header(None),
):
    """Receive Baileys bridge webhook and convert inbound message to a lead.

    Expected Baileys webhook body:
    {
      "event": "message.received",
      "from": "916303862018@s.whatsapp.net",
      "name": "Rahul Sharma",
      "message": "I need a school timetable system with conflict detection.",
      "timestamp": 1234567890
    }

    Headers:
      X-Bridge-Signature: <WHATSAPP_BRIDGE_SECRET from hermes/.env>
    """
    if not settings.WHATSAPP_ENABLED:
        raise HTTPException(status_code=503, detail="WhatsApp integration is disabled")

    # H-2 Fix: Validate bridge shared secret to prevent forged lead injection
    bridge_secret = getattr(settings, "WHATSAPP_BRIDGE_SECRET", "")
    if bridge_secret:
        if not x_bridge_signature or x_bridge_signature != bridge_secret:
            logger.warning("[WhatsApp] Rejected webhook: invalid or missing X-Bridge-Signature")
            raise HTTPException(status_code=401, detail="Unauthorized bridge request")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = body.get("event", "")
    if event not in ("message.received", "message"):
        # Only process inbound messages — ignore status updates, receipts etc.
        return {"status": "ignored", "event": event}

    raw_from = body.get("from", "")
    sender_phone = _normalize_phone(raw_from.split("@")[0])
    sender_name = body.get("name") or body.get("pushName") or "WhatsApp Client"
    message_text = (body.get("message") or body.get("body") or "").strip()

    if not message_text:
        return {"status": "ignored", "reason": "empty message"}

    # Authorization check — only allowed phone numbers
    allowed = _get_allowed_numbers()
    if allowed and sender_phone not in allowed:
        logger.warning("[WhatsApp] Unauthorized sender: %s", sender_phone)
        return {"status": "ignored", "reason": "unauthorized"}

    # Filter out very short messages — greetings etc.
    if len(message_text) < 20:
        logger.info("[WhatsApp] Message too short to create lead: %r", message_text)
        return {"status": "ignored", "reason": "too short"}

    logger.info("[WhatsApp] Incoming lead from %s (%s): %s", sender_name, sender_phone, message_text[:100])

    # Create lead
    async with AsyncSessionLocal() as db:
        lead = Lead(
            name=sender_name,
            phone=f"+{sender_phone}",
            source="whatsapp",
            requirement=message_text[:2000],
            status="NEW",
        )
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        lead_id = lead.id

    # Notify Hermes owner via Telegram
    tg_msg = (
        f"📱 *WHATSAPP LEAD RECEIVED*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *From:* {sender_name}\n"
        f"📞 *Phone:* +{sender_phone}\n"
        f"💬 *Message:* {message_text[:300]}\n\n"
        f"🔄 Hermes pipeline running... Lead #{lead_id}"
    )
    await send_telegram(tg_msg)
    await broadcast_ws("WHATSAPP_LEAD_RECEIVED", {"lead_id": lead_id, "from": sender_name})

    # M-3 Fix: Use BackgroundTasks instead of asyncio.create_task
    # BackgroundTasks runs after response is sent, errors are captured by FastAPI
    background_tasks.add_task(workflow_graph.run_pipeline_for_lead_bg, lead_id)

    return {"status": "accepted", "lead_id": lead_id, "message": f"Lead #{lead_id} created and queued for processing"}


