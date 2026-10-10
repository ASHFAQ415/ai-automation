"""Phase 4 Verification Test Suite: Risk Classification & Human Checkpoints.

Per Section 7 of Master Specification:
- Risk signal scoring (LOW, MEDIUM, HIGH)
- Human acceptance gating for delivery
- Reviewer audit trail
"""
from __future__ import annotations

import pytest

from app.database import AsyncSessionLocal, init_db
from app.models import Lead, ProjectGoalPlan
from app.services.risk_assessor import (
    apply_risk_assessment_to_plan,
    assess_project_risk,
    record_human_acceptance,
    verify_delivery_clearance,
)


def test_risk_classification_scoring():
    # 1. Low Risk
    low_res = assess_project_risk(
        requirement="Simple static company landing page with portfolio showcase",
        title="Modern Portfolio",
    )
    assert low_res["risk_level"] == "LOW"
    assert low_res["human_acceptance_required"] is False

    # 2. Medium Risk (Payment & Webhooks)
    med_res = assess_project_risk(
        requirement="Subscription billing system with Stripe checkout and automated invoices",
        title="SaaS Billing Suite",
    )
    assert med_res["risk_level"] in ["MEDIUM", "HIGH"]
    assert med_res["human_acceptance_required"] is True

    # 3. High Risk (Regulated HIPAA Health Records)
    high_res = assess_project_risk(
        requirement="Patient medical records portal with HIPAA compliance and clinical doctor notes",
        title="HealthPortal Pro",
    )
    assert high_res["risk_level"] == "HIGH"
    assert high_res["human_acceptance_required"] is True


@pytest.mark.asyncio
async def test_human_acceptance_delivery_gate():
    await init_db()

    async with AsyncSessionLocal() as session:
        # Create a lead & high-risk plan
        lead = Lead(
            name="FinTech Corp",
            requirement="Enterprise financial ledger with automated banking payouts and audits",
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)

        plan = ProjectGoalPlan(
            lead_id=lead.id,
            title="Banking Ledger Suite",
            risk_level="HIGH",
            human_acceptance_required=True,
            human_accepted=False,
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)

        # 1. Delivery should be BLOCKED without human signoff
        cleared, reason = verify_delivery_clearance(plan)
        assert cleared is False
        assert "BLOCKED" in reason

        # 2. Record human reviewer signoff
        updated_plan = await record_human_acceptance(
            session,
            plan_id=plan.id,
            reviewer_id="reviewer_lead_ashfaq",
            notes="Passed financial precision checks and penetration smoke test.",
        )
        assert updated_plan.human_accepted is True
        assert updated_plan.human_reviewer_id == "reviewer_lead_ashfaq"
        assert updated_plan.human_accepted_at is not None

        # 3. Delivery should now be CLEARED
        cleared_after, reason_after = verify_delivery_clearance(updated_plan)
        assert cleared_after is True
        assert "signoff confirmed" in reason_after
