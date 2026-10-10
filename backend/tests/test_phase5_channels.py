"""Phase 5 Verification Test Suite: WhatsApp Abstraction, Telegram RBAC, Outreach Control, LLM Context Safety.

Per Section 8 of Master Specification:
- WhatsAppProvider interface with Mock/Cloud API support
- AdminUser authorization and RBAC
- Outreach contact_status compliance and rate limiting
- LLMService context pre-flight and structured generation
"""
from __future__ import annotations

import pytest

from app.database import AsyncSessionLocal, init_db
from app.models import AdminUser, Lead
from app.services.admin_auth import is_authorized_admin
from app.services.llm_service import LLMService, truncate_context_safely
from app.services.outreach_controller import (
    OutreachRateLimiter,
    can_contact_lead,
)
from app.services.whatsapp_provider import (
    MockWhatsAppProvider,
    get_whatsapp_provider,
)


@pytest.mark.asyncio
async def test_whatsapp_provider_abstraction():
    provider = get_whatsapp_provider()
    assert isinstance(provider, MockWhatsAppProvider)

    res = await provider.send_message(to="+919876543210", message="Your demo is ready!")
    assert res["success"] is True
    assert res["provider"] == "MOCK"
    assert len(provider.sent_messages) == 1
    assert provider.sent_messages[0]["to"] == "+919876543210"


@pytest.mark.asyncio
async def test_telegram_admin_authorization():
    import uuid
    await init_db()

    test_uid = f"tg_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        # 1. Unregistered user: should be rejected
        auth1, reason1 = await is_authorized_admin(session, telegram_user_id="unknown_user_999")
        assert auth1 is False
        assert "not registered" in reason1

        # 2. Add an AdminUser to DB
        admin = AdminUser(
            telegram_user_id=test_uid,
            username="ops_lead",
            role="OPERATOR",
            enabled=True,
        )
        session.add(admin)
        await session.commit()

        # 3. Registered user should now be authorized as OPERATOR
        auth2, reason2 = await is_authorized_admin(session, telegram_user_id=test_uid, required_role="OPERATOR")
        assert auth2 is True
        assert "Authorized" in reason2

        # 4. Registered user should NOT have SUPERADMIN permissions
        auth3, reason3 = await is_authorized_admin(session, telegram_user_id=test_uid, required_role="SUPERADMIN")
        assert auth3 is False
        assert "Insufficient role" in reason3

        # 5. Disable admin
        admin.enabled = False
        await session.commit()
        auth4, reason4 = await is_authorized_admin(session, telegram_user_id=test_uid)
        assert auth4 is False
        assert "disabled" in reason4


def test_outreach_control_and_compliance():
    # 1. OPTED_IN lead
    lead_in = Lead(name="E-commerce Store", contact_status="OPTED_IN")
    allowed, reason = can_contact_lead(lead_in)
    assert allowed is True

    # 2. OPTED_OUT lead
    lead_out = Lead(name="Declined Client", contact_status="OPTED_OUT")
    allowed, reason = can_contact_lead(lead_out)
    assert allowed is False
    assert "opted out" in reason

    # 3. DO_NOT_CONTACT lead
    lead_dnc = Lead(name="Sensitive Contact", contact_status="DO_NOT_CONTACT")
    allowed, reason = can_contact_lead(lead_dnc)
    assert allowed is False
    assert "DO_NOT_CONTACT" in reason

    # 4. Rate Limiting enforcement
    limiter = OutreachRateLimiter(max_per_hour=2, max_per_day=5)
    assert limiter.check_rate_limits()[0] is True
    limiter.record_attempt()
    limiter.record_attempt()
    # 3rd attempt in same hour should fail
    allowed, reason = limiter.check_rate_limits()
    assert allowed is False
    assert "Hourly outreach rate limit reached" in reason


@pytest.mark.asyncio
async def test_llm_service_context_safety():
    # 1. Truncate oversized context safely
    huge_text = "This is a prompt line.\n" * 5000  # 115,000+ chars
    truncated = truncate_context_safely(huge_text, max_tokens=1000)
    assert len(truncated) < len(huge_text)
    assert "[Context truncated" in truncated

    # 2. Test generate_structured returns valid dict
    res = await LLMService.generate_structured("Analyze this lead requirement: E-commerce app")
    assert isinstance(res, dict)
    assert "score" in res or "plan" in res or "title" in res or len(res) > 0
