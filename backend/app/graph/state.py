"""Graph Engineering State Definitions for Autonomous Multi-Agent Workflows.
Includes new DUPLICATE and HEALTH_CHECK statuses for Hermes agent collaboration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NodeID(str, Enum):
    """Discrete agentic execution nodes in the DAG."""
    LEAD_INGESTION       = "lead_ingestion"
    LEAD_DEDUP           = "lead_dedup"           # NEW — deduplication gate
    AI_QUALIFIER         = "ai_qualifier"
    BUDGET_ESTIMATOR     = "budget_estimator"      # NEW — market rate enrichment
    PLAN_ARCHITECT       = "plan_architect"
    STAGE1_APPROVAL_GATE = "stage1_approval_gate"
    STAGE2_BUILD_GATE    = "stage2_build_gate"
    PROJECT_BUILDER      = "project_builder"
    CODE_VERIFIER        = "code_verifier"
    DELIVERY_DISPATCHER  = "delivery_dispatcher"
    ERROR_RECOVERY       = "error_recovery"
    HEALTH_MONITOR       = "health_monitor"        # NEW — system health agent


class WorkflowStatus(str, Enum):
    """Lifecycle status of the Hermes state machine."""
    IDLE             = "IDLE"
    QUALIFYING       = "QUALIFYING"
    DUPLICATE        = "DUPLICATE"                 # NEW — deduplicated lead
    AWAITING_STAGE1  = "AWAITING_STAGE1_APPROVAL"
    STAGE1_APPROVED  = "STAGE1_APPROVED"
    AWAITING_STAGE2  = "AWAITING_STAGE2_BUILD"
    BUILDING         = "BUILDING"
    COMPLETED        = "COMPLETED"
    REJECTED         = "REJECTED"
    CANCELLED        = "CANCELLED"
    FAILED           = "FAILED"


class NodeExecutionRecord(BaseModel):
    """Audit record for every node execution in the graph."""
    node_id: NodeID
    timestamp: datetime = Field(default_factory=_now)
    duration_ms: float = 0.0
    status: str = "SUCCESS"
    details: str | None = None
    output_summary: dict[str, Any] | None = None


class LeadGraphState(BaseModel):
    """Evolving global state passed across DAG nodes in the autonomous pipeline.

    BUG #13 FIX: validate_assignment=True ensures that field mutations performed
    by each node (e.g., state.score = ...) are validated by Pydantic at runtime,
    preventing silent type coercion bugs from propagating through the pipeline.
    """
    model_config = ConfigDict(validate_assignment=True)
    lead_id: int
    client_name: str
    requirement: str
    email: str | None = None
    phone: str | None = None
    budget: str | None = "Flexible"
    timeline: str | None = "Immediate"
    company: str | None = None
    source: str | None = "manual"

    # Deduplication
    is_duplicate: bool = False
    duplicate_of_lead_id: int | None = None

    # Qualification & AI Scoring
    score: int = 0
    temperature: str = "WARM"
    qualification_summary: dict[str, Any] | None = None
    market_rate_estimate: str | None = None     # NEW — from BudgetEstimatorAgent

    # Implementation Plan
    plan_id: int | None = None
    plan_title: str | None = None
    target_deliverable: str | None = None
    core_features: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    architecture_overview: str | None = None

    # Approvals
    stage1_approved: bool = False
    stage2_authorized: bool = False
    approval_feedback: str | None = None

    # Autonomous Build Execution
    build_id: int | None = None
    project_path: str | None = None
    generated_files: list[str] = Field(default_factory=list)
    progress_percent: int = 0
    build_logs: str | None = None

    # Custom build instructions (from Telegram /rebuild with notes)
    custom_instructions: str | None = None

    # State Machine Metadata
    current_node: NodeID = NodeID.LEAD_INGESTION
    workflow_status: WorkflowStatus = WorkflowStatus.IDLE
    execution_history: list[NodeExecutionRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
