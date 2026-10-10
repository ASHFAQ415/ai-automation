import pytest
import json
from httpx import AsyncClient, ASGITransport
from main import app
from app.database import AsyncSessionLocal, init_db
from app.models import Lead, PaymentRecord
from sqlalchemy import select

@pytest.mark.asyncio
async def test_payment_webhook_idempotency_and_validation():
    await init_db()
    
    # 1. Create a test lead
    async with AsyncSessionLocal() as session:
        lead = Lead(
            name="Test Payment Client",
            company="Acme Corp",
            email="payment_test@example.com",
            phone="919876543210",
            requirement="Need an autonomous POS system",
            budget="50,000",
            score=85,
            temperature="HOT",
            status="NEW",
            payment_status="UNPAID"
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        lead_id = lead.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Test create payment link
        link_resp = await client.post("/api/payments/create-link", json={
            "lead_id": lead_id,
            "amount": 1500000,
            "currency": "INR",
            "description": "30% Advance Software Deposit"
        })
        assert link_resp.status_code == 200
        link_data = link_resp.json()
        assert "payment_link" in link_data
        assert link_data["payment_status"] == "ADVANCE_PENDING"

        # 3. Test initial valid webhook
        webhook_payload = {
            "provider": "MOCK",
            "event_id": f"evt_test_{lead_id}_001",
            "order_id": f"order_{lead_id}",
            "payment_id": f"pay_{lead_id}_001",
            "lead_id": lead_id,
            "amount": 1500000,
            "currency": "INR"
        }
        res1 = await client.post("/api/payments/webhook", json=webhook_payload)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "payment_processed_successfully"
        assert data1["details"]["status"] == "ADVANCE_PAID"

        # Verify DB updated
        async with AsyncSessionLocal() as session:
            updated_lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
            assert updated_lead.payment_status == "ADVANCE_PAID"
            assert updated_lead.status == "PLAN_APPROVED"
            
            # Check PaymentRecord count
            records = (await session.execute(
                select(PaymentRecord).where(PaymentRecord.lead_id == lead_id)
            )).scalars().all()
            assert len(records) == 1
            assert records[0].provider_event_id == f"evt_test_{lead_id}_001"

        # 4. IDEMPOTENCY TEST: Submit the EXACT same webhook payload again
        res2 = await client.post("/api/payments/webhook", json=webhook_payload)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "duplicate_event_ignored"
        assert data2["details"]["duplicate"] is True

        # Verify no duplicate payment record was created
        async with AsyncSessionLocal() as session:
            records = (await session.execute(
                select(PaymentRecord).where(PaymentRecord.lead_id == lead_id)
            )).scalars().all()
            assert len(records) == 1, "Duplicate webhook must NOT create another PaymentRecord"

        # 5. Invalid amount test
        bad_payload = webhook_payload.copy()
        bad_payload["event_id"] = f"evt_bad_{lead_id}"
        bad_payload["amount"] = 0
        res_bad = await client.post("/api/payments/webhook", json=bad_payload)
        assert res_bad.status_code == 422
