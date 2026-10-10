"""Payment API Router.
Handles payment link creation, provider webhooks (Razorpay/Stripe),
and local sandbox payment simulation.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import require_api_key
from app.models import Lead
from app.services.payment_service import payment_service

logger = logging.getLogger("payments_router")
router = APIRouter(prefix="/api/payments", tags=["payments"])


class PaymentLinkRequest(BaseModel):
    lead_id: int
    amount: int = Field(gt=0, description="Amount in smallest currency unit (e.g. paise / cents)")
    currency: str = "INR"
    description: str | None = "30% Advance Software Deposit"


@router.post("/create-link", dependencies=[Depends(require_api_key)])
async def create_payment_link(req: PaymentLinkRequest, db: AsyncSession = Depends(get_db)):
    """Generate a payment link for a qualified lead."""
    lead = (await db.execute(select(Lead).where(Lead.id == req.lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    link = payment_service.generate_checkout_link(
        lead_id=req.lead_id,
        amount=req.amount,
        currency=req.currency,
        description=req.description or "Software Deposit"
    )

    lead.payment_link = link
    lead.payment_status = "ADVANCE_PENDING"
    lead.advance_amount = req.amount
    await db.commit()

    return {
        "status": "success",
        "lead_id": req.lead_id,
        "payment_link": link,
        "amount": req.amount,
        "currency": req.currency,
        "payment_status": lead.payment_status
    }


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_razorpay_signature: str | None = Header(None),
    stripe_signature: str | None = Header(None),
):
    """Unified webhook endpoint for Razorpay and Stripe with signature & idempotency checks."""
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Detect provider and extract parameters
    if x_razorpay_signature or "razorpay_payment_id" in str(payload):
        provider = "RAZORPAY"
        event_id = payload.get("event_id") or payload.get("id") or payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id", "evt_unknown")
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment_entity.get("order_id") or payload.get("order_id")
        payment_id = payment_entity.get("id") or payload.get("payment_id")
        amount = int(payment_entity.get("amount", payload.get("amount", 0)))
        currency = payment_entity.get("currency", payload.get("currency", "INR"))
        notes = payment_entity.get("notes", payload.get("notes", {}))
        lead_id = int(notes.get("lead_id", payload.get("lead_id", 0)))
        signature = x_razorpay_signature
    else:
        # Generic / Stripe / Mock event
        provider = payload.get("provider", "MOCK").upper()
        event_id = payload.get("event_id") or payload.get("id", "evt_mock_001")
        order_id = payload.get("order_id")
        payment_id = payload.get("payment_id", f"pay_{event_id}")
        amount = int(payload.get("amount", 0))
        currency = payload.get("currency", "INR")
        lead_id = int(payload.get("lead_id", 0))
        signature = stripe_signature

    if not lead_id or amount <= 0:
        raise HTTPException(status_code=422, detail="Missing lead_id or positive amount in payment payload")

    success, msg, details = await payment_service.process_payment_webhook(
        provider=provider,
        event_id=event_id,
        order_id=order_id,
        payment_id=payment_id,
        lead_id=lead_id,
        amount=amount,
        currency=currency,
        signature=signature,
        raw_body=body,
        db=db,
        test_mode=getattr(settings, "TEST_MODE", False) or getattr(settings, "DEBUG", False)
    )

    if not success:
        raise HTTPException(status_code=400, detail=msg)

    return JSONResponse(status_code=200, content={"status": msg, "details": details})


@router.get("/simulate/{lead_id}", response_class=HTMLResponse)
async def payment_simulation_page(lead_id: int, amount: int = 100000, currency: str = "INR", db: AsyncSession = Depends(get_db)):
    """Interactive Sandbox Payment Simulator. Only available in DEBUG or TEST_MODE."""
    # C-4 Fix: restrict sandbox to dev/test environments only
    if not settings.DEBUG and not settings.TEST_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    amount_formatted = f"{currency} {amount / 100:,.2f}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hermes Payment Gateway ? Sandbox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }}
            .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 32px; max-width: 480px; width: 100%; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }}
            .badge {{ display: inline-block; background: #065f46; color: #34d399; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase; margin-bottom: 16px; }}
            h1 {{ margin: 0 0 8px 0; font-size: 24px; font-weight: 700; }}
            p {{ color: #94a3b8; font-size: 14px; line-height: 1.5; margin: 0 0 24px 0; }}
            .details {{ background: #0f172a; border-radius: 8px; padding: 16px; margin-bottom: 24px; font-size: 14px; }}
            .row {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
            .row:last-child {{ margin-bottom: 0; font-weight: 700; color: #38bdf8; font-size: 16px; border-top: 1px solid #334155; padding-top: 8px; }}
            .btn {{ display: block; width: 100%; background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 14px; font-size: 16px; font-weight: 600; cursor: pointer; transition: background 0.2s; }}
            .btn:hover {{ background: #1d4ed8; }}
            .note {{ font-size: 12px; color: #64748b; text-align: center; margin-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <span class="badge">Sandbox Payment Gateway</span>
            <h1>30% Advance Deposit</h1>
            <p>Authorize autonomous compilation for your custom software system.</p>
            <div class="details">
                <div class="row"><span>Client:</span><span>{lead.name}</span></div>
                <div class="row"><span>Company:</span><span>{lead.company or 'Enterprise Client'}</span></div>
                <div class="row"><span>Project:</span><span>{lead.requirement[:40]}...</span></div>
                <div class="row"><span>Deposit Due:</span><span>{amount_formatted}</span></div>
            </div>
            <button class="btn" onclick="payNow()">Simulate Instant UPI / Card Payment</button>
            <div class="note">Hermes Autonomous Payment Engine ? Idempotency Protected</div>
        </div>
        <script>
            async function payNow() {{
                const btn = document.querySelector('.btn');
                btn.disabled = true;
                btn.innerText = 'Processing simulated payment...';
                try {{
                    const payload = {{
                        provider: 'MOCK',
                        event_id: 'sim_' + Date.now(),
                        lead_id: {lead_id},
                        amount: {amount},
                        currency: '{currency}',
                        payment_id: 'pay_sim_' + Math.floor(Math.random() * 1000000)
                    }};
                    const res = await fetch('/api/payments/webhook', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});
                    const data = await res.json();
                    if (res.ok) {{
                        btn.style.background = '#059669';
                        btn.innerText = '? Payment Successful! Build Authorized.';
                        setTimeout(() => {{ window.location.href = '/'; }}, 2000);
                    }} else {{
                        alert('Payment error: ' + (data.detail || 'Unknown error'));
                        btn.disabled = false;
                        btn.innerText = 'Retry Payment';
                    }}
                }} catch (e) {{
                    alert('Network error: ' + e);
                    btn.disabled = false;
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
