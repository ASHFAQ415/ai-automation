"""Phase 3 Verification Test Suite: State Transition Matrix, Execution Ledger, Dead-Letter Queue.

Per Section 6 of Master Specification:
- Strict state transition validation (rejects illegal transitions)
- Node execution ledger with SHA-256 input hashing and idempotency
- Dead-letter event queue for high-impact notifications
- Workflow persistence across restarts
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models import FailedEvent, Lead, NodeExecution
from app.services.workflow_engine import (
    DeadLetterQueue,
    InvalidStateTransitionError,
    NodeLedger,
    compute_payload_hash,
    validate_lead_transition,
    validate_payment_transition,
)


@pytest.mark.asyncio
async def test_state_transition_matrix():
    # 1. Valid Lead Transitions
    validate_lead_transition("NEW", "SCORED")
    validate_lead_transition("SCORED", "PLAN_PENDING")
    validate_lead_transition("PLAN_PENDING", "PLAN_APPROVED")
    validate_lead_transition("PLAN_APPROVED", "BUILDING")
    validate_lead_transition("BUILDING", "BUILT")
    validate_lead_transition("BUILT", "VERIFYING")
    validate_lead_transition("VERIFYING", "DEMO_READY")
    validate_lead_transition("DEMO_READY", "CLIENT_ACCEPTED")
    validate_lead_transition("CLIENT_ACCEPTED", "DELIVERED")

    # 2. Invalid Lead Transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_lead_transition("NEW", "DELIVERED")

    with pytest.raises(InvalidStateTransitionError):
        validate_lead_transition("DELIVERED", "BUILDING")

    with pytest.raises(InvalidStateTransitionError):
        validate_lead_transition("REJECTED", "PLAN_APPROVED")

    # 3. Valid Payment Transitions
    validate_payment_transition("UNPAID", "ADVANCE_PENDING")
    validate_payment_transition("ADVANCE_PENDING", "ADVANCE_PAID")
    validate_payment_transition("ADVANCE_PAID", "FULLY_PAID")

    # 4. Invalid Payment Transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_payment_transition("REFUNDED", "ADVANCE_PAID")

    with pytest.raises(InvalidStateTransitionError):
        validate_payment_transition("UNPAID", "FULLY_PAID")


@pytest.mark.asyncio
async def test_node_execution_ledger_and_idempotency():
    await init_db()

    async with AsyncSessionLocal() as session:
        # Create a test lead
        lead = Lead(
            name="Durability Test Client",
            requirement="Workflow durability verification",
            status="NEW",
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)

        payload = {"step": "qualify", "model": "qwen3:4b", "score_target": 80}

        # 1. First run: should start a new execution (not cached)
        exec1, is_cached1 = await NodeLedger.start_node(session, lead.id, "qualifier_node", payload)
        assert is_cached1 is False
        assert exec1.status == "RUNNING"
        assert exec1.attempt == 1

        # Finish execution
        await NodeLedger.finish_node(session, exec1, output_payload={"score": 85, "tier": "HOT"})

        # 2. Second run with EXACT SAME payload: should hit idempotency cache!
        exec2, is_cached2 = await NodeLedger.start_node(session, lead.id, "qualifier_node", payload)
        assert is_cached2 is True
        assert exec2.id == exec1.id
        assert exec2.status == "COMPLETED"

        # 3. Third run with MODIFIED payload: should trigger attempt #2
        modified_payload = {"step": "qualify", "model": "qwen3:4b", "score_target": 90}
        exec3, is_cached3 = await NodeLedger.start_node(session, lead.id, "qualifier_node", modified_payload)
        assert is_cached3 is False
        assert exec3.id != exec1.id
        assert exec3.attempt == 2


@pytest.mark.asyncio
async def test_dead_letter_queue():
    await init_db()

    async with AsyncSessionLocal() as session:
        payload = {"chat_id": "12345", "message": "High-priority notification"}
        error_msg = "Connection timed out to Telegram API"

        # 1. Enqueue failed event
        failed = await DeadLetterQueue.enqueue(
            session,
            event_type="TELEGRAM_NOTIFICATION",
            payload=payload,
            error=error_msg,
        )
        assert failed.id is not None
        assert failed.status == "PENDING"
        assert failed.retry_count == 0

        # 2. Fetch pending events
        pending = await DeadLetterQueue.get_pending_events(session)
        assert any(e.id == failed.id for e in pending)
