"""Workflow Durability, State Machine, and Transactional Integrity Engine.

Implements Phase 3 of Master Specification:
- Explicit State Transition Matrix for Lead, Payment, and Build states
- Durable Node Execution Ledger with SHA-256 input hashing for idempotency
- Dead-Letter Queue (failed_events) for high-impact notification & trigger retries
- Transaction ordering: DB commit before external enqueuing
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FailedEvent, Lead, NodeExecution

logger = logging.getLogger("workflow_engine")


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state change is attempted."""
    pass


# ==============================================================================
# EXPLICIT STATE TRANSITION MATRICES
# ==============================================================================

ALLOWED_LEAD_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"SCORED", "DUPLICATE", "REJECTED"},
    "SCORED": {"PLAN_PENDING", "REJECTED"},
    "PLAN_PENDING": {"PLAN_APPROVED", "REJECTED"},
    "PLAN_APPROVED": {"BUILDING", "CANCELLED"},
    "BUILDING": {"BUILT", "BUILD_FAILED", "CANCELLED"},
    "BUILT": {"VERIFYING", "DEMO_READY", "BUILD_FAILED"},
    "VERIFYING": {"DEMO_READY", "BUILD_FAILED"},
    "DEMO_READY": {"CLIENT_ACCEPTED", "REJECTED", "BUILDING"},  # Rebuild permitted
    "CLIENT_ACCEPTED": {"DELIVERED", "REFUNDED"},
    "DELIVERED": set(),  # Terminal state
    "BUILD_FAILED": {"BUILDING", "REJECTED"},
    "DUPLICATE": set(),  # Terminal state
    "REJECTED": set(),   # Terminal state
    "CANCELLED": {"PLAN_APPROVED", "BUILDING"},
}

ALLOWED_PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "UNPAID": {"ADVANCE_PENDING", "ADVANCE_PAID", "PAYMENT_FAILED"},
    "ADVANCE_PENDING": {"ADVANCE_PAID", "PAYMENT_FAILED", "EXPIRED"},
    "ADVANCE_PAID": {"FULLY_PAID", "REFUND_PENDING"},
    "PAYMENT_FAILED": {"ADVANCE_PENDING", "UNPAID"},
    "REFUND_PENDING": {"REFUNDED"},
    "REFUNDED": set(),  # Terminal state
    "DISPUTED": {"REFUNDED", "FULLY_PAID"},
    "EXPIRED": {"ADVANCE_PENDING"},
    "FULLY_PAID": {"REFUND_PENDING", "DISPUTED"},
}

ALLOWED_BUILD_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"SCAFFOLDING", "CANCELLED"},
    "SCAFFOLDING": {"GENERATING_CODE", "FAILED", "CANCELLED"},
    "GENERATING_CODE": {"VERIFYING", "FAILED", "CANCELLED"},
    "VERIFYING": {"VERIFIED", "VERIFICATION_FAILED", "FAILED"},
    "VERIFIED": {"COMPLETED", "DEMO_READY"},
    "COMPLETED": set(),
    "DEMO_READY": set(),
    "VERIFICATION_FAILED": {"SCAFFOLDING", "FAILED"},
    "FAILED": {"SCAFFOLDING", "QUEUED"},
    "CANCELLED": {"QUEUED"},
}


def validate_lead_transition(current_state: str, new_state: str) -> None:
    """Enforce strict Lead state transitions."""
    if current_state == new_state:
        return  # Idempotent no-op

    allowed = ALLOWED_LEAD_TRANSITIONS.get(current_state, set())
    if new_state not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal Lead state transition: '{current_state}' -> '{new_state}'. Allowed: {sorted(allowed)}"
        )


def validate_payment_transition(current_state: str, new_state: str) -> None:
    """Enforce strict Payment state transitions."""
    if current_state == new_state:
        return

    allowed = ALLOWED_PAYMENT_TRANSITIONS.get(current_state, set())
    if new_state not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal Payment state transition: '{current_state}' -> '{new_state}'. Allowed: {sorted(allowed)}"
        )


def compute_payload_hash(payload: Any) -> str:
    """Compute deterministic SHA-256 hash of payload."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ==============================================================================
# DURABLE EXECUTION LEDGER & IDEMPOTENCY
# ==============================================================================

class NodeLedger:
    """Records each graph node execution and enforces idempotency."""

    @staticmethod
    async def start_node(
        db: AsyncSession,
        lead_id: int,
        node_id: str,
        input_payload: dict[str, Any],
    ) -> tuple[NodeExecution, bool]:
        """Start or recover a node execution.

        Returns:
            (NodeExecution, is_cached: bool)
            If is_cached is True, previous COMPLETED output can be safely returned without re-running.
        """
        input_hash = compute_payload_hash(input_payload)

        # Check for previous successful run with same input hash
        query = select(NodeExecution).where(
            NodeExecution.lead_id == lead_id,
            NodeExecution.node_id == node_id,
            NodeExecution.input_hash == input_hash,
            NodeExecution.status == "COMPLETED",
        ).order_by(desc(NodeExecution.id))
        result = await db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            logger.info("Idempotent hit for node '%s' on lead %d. Reusing execution %s", node_id, lead_id, existing.execution_id)
            return existing, True

        # Calculate attempt number
        attempt_query = select(NodeExecution).where(
            NodeExecution.lead_id == lead_id,
            NodeExecution.node_id == node_id,
        )
        attempt_res = await db.execute(attempt_query)
        attempt_count = len(attempt_res.scalars().all()) + 1

        execution = NodeExecution(
            execution_id=str(uuid.uuid4()),
            lead_id=lead_id,
            node_id=node_id,
            attempt=attempt_count,
            status="RUNNING",
            input_hash=input_hash,
            input_data=json.dumps(input_payload, default=str),
            started_at=datetime.now(timezone.utc),
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)
        return execution, False

    @staticmethod
    async def finish_node(
        db: AsyncSession,
        execution: NodeExecution,
        output_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Mark a node execution as COMPLETED or FAILED and persist outputs."""
        execution.completed_at = datetime.now(timezone.utc)
        if error:
            execution.status = "FAILED"
            execution.error = error
        else:
            execution.status = "COMPLETED"
            execution.output_data = json.dumps(output_payload or {}, default=str)

        await db.commit()
        await db.refresh(execution)


# ==============================================================================
# DEAD-LETTER QUEUE FOR FAILED EVENTS
# ==============================================================================

class DeadLetterQueue:
    """Stores failed notifications, triggers, and outbound calls for resilient background retries."""

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        event_type: str,
        payload: dict[str, Any],
        error: str,
        max_retries: int = 3,
    ) -> FailedEvent:
        """Record an unhandled failure for retry."""
        event = FailedEvent(
            event_type=event_type,
            payload=json.dumps(payload, default=str),
            retry_count=0,
            max_retries=max_retries,
            status="PENDING",
            last_error=error,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        logger.warning("Enqueued failed event '%s' (ID %d) to Dead-Letter Queue: %s", event_type, event.id, error)
        return event

    @staticmethod
    async def get_pending_events(db: AsyncSession) -> list[FailedEvent]:
        """Fetch all events eligible for retry."""
        res = await db.execute(
            select(FailedEvent).where(FailedEvent.status.in_(["PENDING", "RETRYING"]))
        )
        return list(res.scalars().all())
