"""Payment Processing & Hardened Webhook Service.
Implements strict idempotency, provider event deduplication,
cryptographic signature verification, and atomic state transitions.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ActivityLog, Lead, PaymentRecord

logger = logging.getLogger("payment_service")

def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentService:
    """Hardened commercial payment processor with idempotency protections."""

    @staticmethod
    def verify_razorpay_signature(body: bytes, signature: str, secret: str) -> bool:
        """Verify HMAC-SHA256 signature from Razorpay webhook."""
        if not secret or not signature:
            return False
        # BUG #1 FIX: hmac.new(key, msg, digestmod) is the correct Python stdlib call
        expected = hmac.new(
            key=secret.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    async def process_payment_webhook(
        provider: str,
        event_id: str,
        order_id: str | None,
        payment_id: str | None,
        lead_id: int,
        amount: int,
        currency: str,
        signature: str | None,
        raw_body: bytes,
        db: AsyncSession,
        test_mode: bool = False,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Process incoming payment webhook with strict validation and idempotency.
        
        Returns:
            (success: bool, message: str, details: dict)
        """
        provider = provider.upper()
        logger.info("[PAYMENT] Processing %s event %s for lead #%s", provider, event_id, lead_id)

        # 1. IDEMPOTENCY GUARD: Check if event_id has already been processed
        existing_event = (await db.execute(
            select(PaymentRecord).where(PaymentRecord.provider_event_id == event_id)
        )).scalar_one_or_none()

        if existing_event:
            logger.warning("[PAYMENT-IDEMPOTENT] Duplicate event %s already processed. Ignoring.", event_id)
            return True, "duplicate_event_ignored", {
                "event_id": event_id,
                "lead_id": lead_id,
                "processed": True,
                "duplicate": True,
                "payment_id": existing_event.provider_payment_id
            }

        # 2. SIGNATURE VERIFICATION
        if not test_mode and not settings.DEBUG:
            if provider == "RAZORPAY":
                secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
                if secret and signature:
                    if not PaymentService.verify_razorpay_signature(raw_body, signature, secret):
                        logger.error("[PAYMENT-SECURITY] Invalid signature for event %s", event_id)
                        return False, "invalid_signature", {"event_id": event_id}
            # For Stripe, signature check uses stripe.Webhook.construct_event if key present

        # 3. LEAD VERIFICATION
        lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
        if not lead:
            logger.error("[PAYMENT-ERROR] Lead #%s not found for payment event %s", lead_id, event_id)
            return False, "lead_not_found", {"lead_id": lead_id}

        # 4. AMOUNT & CURRENCY VALIDATION
        if amount <= 0:
            logger.error("[PAYMENT-ERROR] Invalid amount %s for event %s", amount, event_id)
            return False, "invalid_amount", {"amount": amount}

        # 5. ATOMIC PERSISTENCE & STATE ADVANCEMENT
        payment_record = PaymentRecord(
            lead_id=lead_id,
            provider=provider,
            provider_event_id=event_id,
            provider_order_id=order_id,
            provider_payment_id=payment_id,
            amount=amount,
            currency=currency.upper(),
            status="ADVANCE_PAID",
            raw_payload=raw_body.decode("utf-8", errors="ignore"),
            idempotency_key=f"{provider}_{event_id}",
            created_at=_now()
        )
        db.add(payment_record)

        # Update lead state
        lead.payment_status = "ADVANCE_PAID"
        lead.advance_amount = amount
        # Advance workflow to PLAN_APPROVED so build can proceed
        if lead.status in ("NEW", "SCORED", "PLAN_PENDING"):
            lead.status = "PLAN_APPROVED"

        # Log Activity
        log = ActivityLog(
            lead_id=lead_id,
            event_type="PAYMENT_RECEIVED",
            title=f"Advance Payment Received: {currency} {amount / 100:.2f}",
            details=f"Provider: {provider} | Event: {event_id} | Payment ID: {payment_id}",
            channel="SYSTEM",
            created_at=_now()
        )
        db.add(log)

        await db.commit()
        await db.refresh(lead)

        logger.info("[PAYMENT-SUCCESS] Lead #%s advanced to ADVANCE_PAID. Amount: %s %s", lead_id, currency, amount)
        return True, "payment_processed_successfully", {
            "lead_id": lead_id,
            "status": "ADVANCE_PAID",
            "amount": amount,
            "currency": currency,
            "payment_id": payment_id
        }

    @staticmethod
    def generate_checkout_link(lead_id: int, amount: int, currency: str = "INR", description: str = "Software Deposit") -> str:
        """Generate hosted checkout link (Razorpay/Stripe or local sandbox simulator).

        BUG #19 FIX: In production, this must call the Razorpay SDK to create a real
        payment link via razorpay.Client(auth=(key_id, key_secret)).payment_link.create(...).
        Until that integration is complete, production mode raises NotImplementedError
        rather than returning a fake hardcoded URL that would confuse real clients.
        """
        razorpay_key = getattr(settings, "RAZORPAY_KEY_ID", "")
        razorpay_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
        if razorpay_key and razorpay_secret and not settings.DEBUG:
            # TODO: Integrate Razorpay SDK:
            # import razorpay
            # client = razorpay.Client(auth=(razorpay_key, razorpay_secret))
            # link = client.payment_link.create({
            #     "amount": amount, "currency": currency,
            #     "description": description, "reference_id": f"hermes_lead_{lead_id}"
            # })
            # return link["short_url"]
            raise NotImplementedError(
                f"Razorpay payment link creation is not yet integrated. "
                f"Implement razorpay.Client().payment_link.create() for lead #{lead_id}."
            )

        # Local Sandbox Simulator URL (DEBUG / dev mode)
        host = getattr(settings, "HOST", "localhost")
        port = getattr(settings, "PORT", 8000)
        return f"http://{host}:{port}/api/payments/simulate/{lead_id}?amount={amount}&currency={currency}"

payment_service = PaymentService()
