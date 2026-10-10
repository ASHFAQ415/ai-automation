"""Risk Classification & Human Checkpoints Engine.

Implements Phase 4 of Master Specification:
- Automatic risk classification (LOW, MEDIUM, HIGH) based on multi-factor signals
- Gated workflow: LOW risk auto-clears delivery; MEDIUM/HIGH requires human acceptance signoff
- Audit trail for human reviewer signoffs
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectGoalPlan

logger = logging.getLogger("risk_assessor")

# Signal definitions with risk weights
RISK_RULES = {
    "SENSITIVE_DATA": {
        "weight": 2,
        "keywords": ["pii", "password", "ssn", "credentials", "confidential", "medical", "patient", "biometric"],
    },
    "PAYMENTS_FINANCE": {
        "weight": 2,
        "keywords": ["payment", "stripe", "razorpay", "bank", "invoice", "checkout", "credit card", "finance", "billing"],
    },
    "COMPLEX_AUTH": {
        "weight": 1,
        "keywords": ["oauth", "rbac", "multi-tenant", "role", "permissions", "sso", "jwt", "authorization"],
    },
    "EXTERNAL_INTEGRATIONS": {
        "weight": 1,
        "keywords": ["webhook", "third-party", "integration", "crawler", "scraping", "sync", "tunnel"],
    },
    "REGULATED_DOMAIN": {
        "weight": 3,
        "keywords": ["legal", "court", "compliance", "hipaa", "gdpr", "insurance", "statutory", "audit"],
    },
    "MINORS_EDUCATION": {
        "weight": 2,
        "keywords": ["minor", "children", "child", "elementary", "k-12", "kindergarten", "parental"],
    },
}


def assess_project_risk(requirement: str, title: str = "", core_features: list[str] | None = None) -> dict[str, Any]:
    """Analyze project specifications and calculate risk classification."""
    features_text = " ".join(core_features or [])
    combined_text = f"{requirement} {title} {features_text}".lower()

    detected_signals = []
    total_score = 0

    for signal_name, rule in RISK_RULES.items():
        matched_keywords = [kw for kw in rule["keywords"] if kw in combined_text]
        if matched_keywords:
            detected_signals.append({
                "signal": signal_name,
                "weight": rule["weight"],
                "matched_keywords": matched_keywords,
            })
            total_score += rule["weight"]

    # Determine risk level
    if total_score >= 3 or any(s["signal"] in ["REGULATED_DOMAIN", "PAYMENTS_FINANCE"] for s in detected_signals):
        risk_level = "HIGH" if total_score >= 4 or any(s["signal"] == "REGULATED_DOMAIN" for s in detected_signals) else "MEDIUM"
    elif total_score >= 1:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    human_acceptance_required = risk_level in ["MEDIUM", "HIGH"]

    return {
        "risk_level": risk_level,
        "risk_score": total_score,
        "signals": detected_signals,
        "human_acceptance_required": human_acceptance_required,
    }


async def apply_risk_assessment_to_plan(db: AsyncSession, plan: ProjectGoalPlan, requirement: str) -> None:
    """Evaluate and persist risk assessment onto a ProjectGoalPlan."""
    core_features = json.loads(plan.core_features) if plan.core_features else []
    assessment = assess_project_risk(requirement, plan.title, core_features)

    plan.risk_level = assessment["risk_level"]
    plan.risk_signals = json.dumps(assessment["signals"])
    plan.human_acceptance_required = assessment["human_acceptance_required"]
    plan.human_accepted = False  # Reset acceptance if re-assessed

    await db.commit()
    await db.refresh(plan)
    logger.info("Assessed plan #%d: Risk Level %s (Human required: %s)", plan.id, plan.risk_level, plan.human_acceptance_required)


def verify_delivery_clearance(plan: ProjectGoalPlan) -> tuple[bool, str]:
    """Verify if a project has clearance for final settlement and delivery.

    Returns:
        (cleared: bool, reason: str)
    """
    if plan.risk_level == "LOW":
        return True, "LOW risk deliverable: auto-cleared for delivery."

    if plan.human_accepted:
        return True, f"{plan.risk_level} risk deliverable: human acceptance signoff confirmed by {plan.human_reviewer_id}."

    return False, f"Delivery BLOCKED: {plan.risk_level} risk deliverable requires manual human acceptance signoff."


async def record_human_acceptance(
    db: AsyncSession,
    plan_id: int,
    reviewer_id: str,
    notes: str | None = None,
) -> ProjectGoalPlan:
    """Record human reviewer signoff for a MEDIUM or HIGH risk plan."""
    query = select(ProjectGoalPlan).where(ProjectGoalPlan.id == plan_id)
    result = await db.execute(query)
    plan = result.scalar_one_or_none()
    if not plan:
        raise ValueError(f"Plan #{plan_id} not found")

    plan.human_accepted = True
    plan.human_reviewer_id = reviewer_id
    plan.human_accepted_at = datetime.now(timezone.utc)
    plan.human_review_notes = notes or "Accepted after human review of live demo."

    await db.commit()
    await db.refresh(plan)
    logger.info("Human acceptance recorded for plan #%d by %s", plan_id, reviewer_id)
    return plan
