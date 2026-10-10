"""Unified Multi-Channel Notification Engine: Telegram, Discord, WebSocket.
Fully integrated with Hermes credentials loaded from hermes/.env.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("notifier")

# ── WebSocket client registry ─────────────────────────────────────────────────
_active_ws_clients: set = set()
_ws_lock = asyncio.Lock()


def register_ws_client(websocket) -> None:
    _active_ws_clients.add(websocket)


def unregister_ws_client(websocket) -> None:
    _active_ws_clients.discard(websocket)


async def broadcast_ws(event_type: str, data: dict[str, Any]) -> None:
    """Push real-time event to all connected dashboard WebSocket clients.
    Automatically prunes stale/disconnected clients on send failure.
    """
    payload = {"event": event_type, "data": data}
    disconnected: set = set()
    async with _ws_lock:
        clients = list(_active_ws_clients)
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            disconnected.add(ws)
    if disconnected:
        async with _ws_lock:
            for ws in disconnected:
                _active_ws_clients.discard(ws)


# ── Telegram ──────────────────────────────────────────────────────────────────

async def send_telegram(
    text: str,
    parse_mode: str | None = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Send message via Telegram Bot with Markdown retry fallback.
    On HTTP 400 (parse error), retries as plain text preserving reply_markup.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # Retry without parse_mode on Markdown parse failures (HTTP 400)
            # ← Preserves reply_markup in the retry (was lost before)
            if resp.status_code == 400 and parse_mode:
                retry_payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                resp2 = await client.post(url, json=retry_payload)
                return resp2.status_code == 200
            logger.error("Telegram notification failed %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("Telegram notification error: %s", e)
        return False


# ── Discord ───────────────────────────────────────────────────────────────────

async def send_discord(
    title: str,
    description: str,
    fields: list[dict[str, Any]] | None = None,
    color: int = 0x38BDF8,
) -> bool:
    """Send rich embed to Discord channel via Bot token.
    Discord returns 200 for GET and 201 Created for successful POST messages.
    """
    if not settings.DISCORD_BOT_TOKEN or not settings.DISCORD_CHANNEL_ID:
        logger.warning("Discord not configured — skipping notification.")
        return False

    url = f"https://discord.com/api/v10/channels/{settings.DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    embed: dict[str, Any] = {"title": title, "description": description, "color": color}
    if fields:
        embed["fields"] = fields
    payload = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            # FIX: Discord returns 201 Created on successful message POST (was checking only 200)
            if resp.status_code in (200, 201):
                return True
            logger.warning("Discord notification failed %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.error("Discord notification error: %s", e)
        return False


# ── Composed Hermes Notification Events ──────────────────────────────────────

async def notify_hot_deal(lead: dict[str, Any], plan: dict[str, Any] | None = None) -> None:
    """Notify Hermes channels when a deal is scored as HOT."""
    title = f"🔥 HOT DEAL DETECTED: {lead.get('name')}"
    score = lead.get("score", 85)
    budget = lead.get("budget", "Flexible")
    req = (lead.get("requirement") or "")[:200]
    lead_id = lead.get("id")

    tg_text = (
        f"🔥 *HOT DEAL DETECTED* (Score: {score}/100)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Client:* {lead.get('name')}\n"
        f"🏢 *Company:* {lead.get('company') or 'N/A'}\n"
        f"💰 *Budget:* {budget}\n"
        f"⏱ *Timeline:* {lead.get('timeline') or 'Immediate'}\n"
        f"📝 *Requirement:* {req}\n\n"
        f"⚡ *Action:* Implementation plan generated and pending your approval!"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": f"✓ Approve Plan #{lead_id}", "callback_data": f"approve:{lead_id}"},
                {"text": f"🚀 Build #{lead_id}", "callback_data": f"build:{lead_id}"},
            ],
            [{"text": "📋 View Full Pipeline", "callback_data": "pipeline"}],
        ]
    }
    await send_telegram(tg_text, reply_markup=keyboard)

    discord_fields = [
        {"name": "Client", "value": lead.get("name", "Unknown"), "inline": True},
        {"name": "Budget", "value": budget, "inline": True},
        {"name": "Score", "value": f"{score}/100", "inline": True},
        {"name": "Requirement", "value": req, "inline": False},
    ]
    if plan:
        discord_fields.append({
            "name": "Target Deliverable",
            "value": plan.get("target_deliverable", "Custom Solution"),
            "inline": False,
        })
    await send_discord(title=title, description="High-value lead qualified. Review and approve plan.", fields=discord_fields, color=0xFF4757)
    await broadcast_ws("HOT_DEAL_DETECTED", {"lead": lead, "plan": plan})


async def notify_plan_approval_request(lead: dict[str, Any], plan: dict[str, Any]) -> None:
    """Send Stage 1 approval request with plan summary to Hermes channels."""
    lead_id = lead.get("id")
    tg_text = (
        f"📋 *PROJECT GOALS & IMPLEMENTATION PLAN READY*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Project:* {plan.get('title')}\n"
        f"📦 *Deliverable:* {plan.get('target_deliverable')}\n\n"
        f"*Core Features:*\n"
        + "\n".join(f"• {feat}" for feat in plan.get("core_features", [])[:4])
        + f"\n\n🛠 *Tech Stack:* {', '.join(plan.get('tech_stack', []))}\n\n"
        f"👉 Tap a button to approve or launch build:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": f"✓ Approve Plan #{lead_id}", "callback_data": f"approve:{lead_id}"},
                {"text": "✕ Reject", "callback_data": f"reject:{lead_id}"},
            ],
            [{"text": f"🚀 Authorize & Launch Build #{lead_id}", "callback_data": f"build:{lead_id}"}],
        ]
    }
    await send_telegram(tg_text, reply_markup=keyboard)
    await broadcast_ws("PLAN_APPROVAL_PENDING", {"lead_id": lead_id, "plan": plan})


async def notify_build_started(lead: dict[str, Any], project_name: str) -> None:
    tg_text = (
        f"🚀 *BUILD STARTED*\n"
        f"Hermes Autonomous Engine building: *{project_name}* for {lead.get('name')}.\n"
        f"Scaffolding full-stack application and generating production code..."
    )
    await send_telegram(tg_text)
    await send_discord(
        title=f"🚀 Build Started: {project_name}",
        description=f"Autonomous code generation active for {lead.get('name')}.",
        color=0x2ED573,
    )
    await broadcast_ws("BUILD_STARTED", {"lead_id": lead.get("id"), "project_name": project_name})


async def notify_build_progress(lead: dict[str, Any], project_name: str, step_title: str, percent: int) -> None:
    """Broadcast real-time build progress to WebSocket dashboard only (no Telegram spam)."""
    await broadcast_ws("BUILD_PROGRESS", {
        "lead_id": lead.get("id"),
        "project_name": project_name,
        "step": step_title,
        "progress": percent,
    })


async def notify_build_completed(lead: dict[str, Any], project_name: str, project_path: str, files: list[str]) -> None:
    file_list = "\n".join(f"  ✓ `{f}`" for f in files[:8])
    if len(files) > 8:
        file_list += f"\n  ...and {len(files) - 8} more files"

    tg_text = (
        f"✅ *PROJECT BUILD COMPLETE!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Project:* {project_name}\n"
        f"👤 *Client:* {lead.get('name')}\n"
        f"📁 *Local Path:* `{project_path}`\n\n"
        f"*Generated Artifacts ({len(files)} files):*\n{file_list}\n\n"
        f"🎉 Ready to demonstrate and deliver to client!"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📁 View Projects", "callback_data": "projects"}],
        ]
    }
    await send_telegram(tg_text, reply_markup=keyboard)
    await send_discord(
        title=f"✅ Build Complete: {project_name}",
        description=f"Successfully built {len(files)} files for {lead.get('name')}.",
        fields=[
            {"name": "Location", "value": f"`{project_path}`", "inline": False},
            {"name": "Files Generated", "value": str(len(files)), "inline": True},
            {"name": "Status", "value": "Ready to Demo / Deliver", "inline": True},
        ],
        color=0x2ED573,
    )
    await broadcast_ws("BUILD_COMPLETED", {
        "lead_id": lead.get("id"),
        "project_name": project_name,
        "project_path": project_path,
        "files": files,
    })


async def notify_build_failed(lead: dict[str, Any], project_name: str, error: str) -> None:
    """Alert Hermes owner when an autonomous build fails — was previously silent."""
    tg_text = (
        f"❌ *BUILD FAILED*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Project:* {project_name}\n"
        f"👤 *Client:* {lead.get('name')}\n"
        f"🔴 *Error:* `{str(error)[:300]}`\n\n"
        f"Use `/rebuild {lead.get('id')}` to retry."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": f"🔄 Retry Build #{lead.get('id')}", "callback_data": f"build:{lead.get('id')}"}]
        ]
    }
    await send_telegram(tg_text, reply_markup=keyboard)
    await broadcast_ws("BUILD_FAILED", {"lead_id": lead.get("id"), "error": str(error)[:300]})


async def notify_duplicate_lead(lead_id: int, existing_id: int, name: str) -> None:
    """Notify when LeadDeduplicationAgent detects a duplicate submission."""
    tg_text = (
        f"⚠️ *DUPLICATE LEAD DETECTED*\n"
        f"New Lead #{lead_id} ({name}) matches existing Lead #{existing_id}.\n"
        f"Marked as DUPLICATE. Use `/leads` to review."
    )
    await send_telegram(tg_text)
    await broadcast_ws("LEAD_DUPLICATE", {"lead_id": lead_id, "existing_id": existing_id})


async def notify_health_alert(issue: str, details: str) -> None:
    """Send system health alert from SystemHealthMonitorAgent to Hermes."""
    tg_text = (
        f"🚨 *HERMES HEALTH ALERT*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Issue:* {issue}\n"
        f"📋 *Details:* {details}\n\n"
        f"Check `/status` for full telemetry."
    )
    await send_telegram(tg_text)
    await broadcast_ws("HEALTH_ALERT", {"issue": issue, "details": details})
