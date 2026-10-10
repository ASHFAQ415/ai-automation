"""SQLAlchemy database models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


def _now() -> datetime:
    """Timezone-aware UTC now — replaces deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True, index=True)   # indexed for dedup
    phone = Column(String(100), nullable=True, index=True)   # indexed for dedup
    source = Column(String(50), default="website")
    requirement = Column(Text, nullable=False)
    budget = Column(String(100), default="Flexible")
    timeline = Column(String(100), default="Immediate")

    score = Column(Integer, default=0)
    temperature = Column(String(20), default="WARM")  # HOT | WARM | COLD
    status = Column(String(50), default="NEW")
    # Status lifecycle: NEW → DUPLICATE | SCORED → PLAN_PENDING → PLAN_APPROVED → BUILDING → BUILT | REJECTED

    # Payment lifecycle: UNPAID → ADVANCE_PENDING → ADVANCE_PAID → FULLY_PAID
    payment_status = Column(String(50), default="UNPAID", index=True)
    payment_link = Column(String(500), nullable=True)
    advance_amount = Column(Integer, default=0)

    # Outreach & Compliance (Phase 5)
    contact_status = Column(String(50), default="OPTED_IN", index=True)  # OPTED_IN | OPTED_OUT | DO_NOT_CONTACT

    # Tenant & Commercial Maturity (Phase 6)
    tenant_id = Column(String(50), default="default_tenant", index=True)
    quote_number = Column(String(50), nullable=True, unique=True, index=True)
    invoice_number = Column(String(50), nullable=True, unique=True, index=True)

    ai_analysis = Column(Text, nullable=True)   # JSON breakdown from AI scoring
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    # BUG #8 FIX: Lead.plan is uselist=False (scalar) but the project_goal_plans
    # table allows multiple rows per lead (for /rebuild regeneration). Without
    # order_by, SQLAlchemy picks an arbitrary row. order_by(desc) returns latest.
    plan = relationship(
        "ProjectGoalPlan",
        back_populates="lead",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProjectGoalPlan.id.desc()",
    )
    builds = relationship(
        "ProjectBuild",
        back_populates="lead",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    payments = relationship(
        "PaymentRecord",
        back_populates="lead",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    logs = relationship(
        "ActivityLog",
        back_populates="lead",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class PaymentRecord(Base):
    __tablename__ = "payment_records"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(50), nullable=False, default="RAZORPAY")  # RAZORPAY | STRIPE | MOCK
    provider_event_id = Column(String(255), unique=True, index=True, nullable=False)
    provider_order_id = Column(String(255), index=True, nullable=True)
    provider_payment_id = Column(String(255), nullable=True)
    amount = Column(Integer, nullable=False)  # In smallest currency unit (paise/cents)
    currency = Column(String(10), default="INR")
    status = Column(String(50), default="ADVANCE_PAID")
    raw_payload = Column(Text, nullable=True)
    idempotency_key = Column(String(255), unique=True, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    lead = relationship("Lead", back_populates="payments", lazy="selectin")


class ProjectGoalPlan(Base):
    __tablename__ = "project_goal_plans"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(50), default="default_tenant", index=True)
    # Removed unique=True — a lead can have a plan replaced/regenerated after rejection
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    target_deliverable = Column(String(255), default="Full-Stack Application")
    core_features = Column(Text, nullable=True)         # JSON array
    tech_stack = Column(Text, nullable=True)            # JSON array
    architecture_overview = Column(Text, nullable=True)
    implementation_steps = Column(Text, nullable=True)  # JSON array of step dicts
    approval_status = Column(String(50), default="PENDING")  # PENDING | APPROVED | REJECTED
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # Risk Classification & Human Checkpoints (Phase 4)
    risk_level = Column(String(20), default="LOW")  # LOW | MEDIUM | HIGH
    risk_signals = Column(Text, nullable=True)     # JSON array of detected signals
    human_acceptance_required = Column(Boolean, default=False)
    human_accepted = Column(Boolean, default=False)
    human_reviewer_id = Column(String(100), nullable=True)
    human_accepted_at = Column(DateTime(timezone=True), nullable=True)
    human_review_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)

    lead = relationship("Lead", back_populates="plan", lazy="selectin")



class ProjectBuild(Base):
    __tablename__ = "project_builds"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(50), default="default_tenant", index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("project_goal_plans.id", ondelete="SET NULL"), nullable=True)
    project_name = Column(String(255), nullable=False)
    project_path = Column(String(500), nullable=False)
    # Normalized status set — "COMPILING" removed (was inconsistent with code)
    status = Column(String(50), default="QUEUED")
    # Status lifecycle: QUEUED → SCAFFOLDING → GENERATING_CODE → VERIFYING → COMPLETED | FAILED | CANCELLED
    progress_percent = Column(Integer, default=0)
    version = Column(Integer, default=1)
    artifact_checksum = Column(String(64), nullable=True)  # SHA-256 hex digest
    verification_report = Column(Text, nullable=True)     # JSON detailed gate results
    gate_passed = Column(Boolean, default=False)
    generated_files = Column(Text, default="[]")        # JSON list; default [] prevents None parse errors
    build_logs = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="builds", lazy="selectin")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=True)
    event_type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    details = Column(Text, nullable=True)
    channel = Column(String(50), default="SYSTEM")
    created_at = Column(DateTime(timezone=True), default=_now)

    lead = relationship("Lead", back_populates="logs", lazy="selectin")


class NodeExecution(Base):
    """Durable execution ledger recording each graph node attempt and input hash for idempotency."""
    __tablename__ = "node_executions"

    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(String(64), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String(100), nullable=False, index=True)
    attempt = Column(Integer, default=1)
    status = Column(String(50), default="PENDING")  # PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
    input_hash = Column(String(64), nullable=False, index=True)
    input_data = Column(Text, nullable=True)
    output_data = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class FailedEvent(Base):
    """Dead-letter event queue for high-impact notifications, triggers, and outreach retries."""
    __tablename__ = "failed_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    status = Column(String(50), default="PENDING")  # PENDING, RETRYING, DEAD_LETTER, RESOLVED
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AdminUser(Base):
    """Authorized operators and administrators for Telegram and control plane operations."""
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String(50), unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=True)
    role = Column(String(50), default="OPERATOR")  # SUPERADMIN | OPERATOR | VIEWER
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class AuditEntry(Base):
    """Cryptographically chained immutable audit entry for compliance and business traceability."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=_now, nullable=False)
    actor_type = Column(String(50), nullable=False)   # SYSTEM, OPERATOR, CLIENT, GATEWAY
    actor_id = Column(String(100), nullable=False)    # admin username, telegram ID, webhook event ID
    action = Column(String(100), nullable=False, index=True)  # QUOTE_ISSUED, PAYMENT_RECEIVED, BUILD_STARTED, etc.
    target_type = Column(String(50), nullable=False)  # LEAD, PAYMENT, BUILD, GOAL_PLAN
    target_id = Column(String(100), nullable=False)
    before_state = Column(Text, nullable=True)
    after_state = Column(Text, nullable=True)
    previous_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False, unique=True, index=True)


class ConversationMemory(Base):
    """Persistent multi-turn conversation memory for the autonomous agent."""
    __tablename__ = "conversation_memory"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), nullable=False, index=True)  # e.g. "tg_123456", "lead_42", "dashboard"
    role = Column(String(20), nullable=False)  # "user" | "assistant" | "system" | "tool"
    content = Column(Text, nullable=False)
    tool_calls = Column(Text, nullable=True)     # JSON string of tool calls
    tool_results = Column(Text, nullable=True)   # JSON string of tool execution results
    metadata_json = Column(Text, nullable=True)  # JSON metadata (token counts, tags, etc.)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)


