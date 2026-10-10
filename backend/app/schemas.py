"""Pydantic schemas for request/response serialization."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_json_if_str(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v or []


class LeadCreate(BaseModel):
    name: str = Field(..., max_length=255, json_schema_extra={"example": "Rahul Sharma"})
    company: str | None = Field(None, max_length=255, json_schema_extra={"example": "Acme Realty"})
    email: str | None = Field(None, max_length=255, json_schema_extra={"example": "rahul@acme.com"})
    phone: str | None = Field(None, max_length=50, json_schema_extra={"example": "+91 9876543210"})
    source: str | None = Field("website", max_length=50, json_schema_extra={"example": "website"})
    requirement: str = Field(..., max_length=2000, json_schema_extra={"example": "Need an automated WhatsApp CRM."})
    budget: str | None = Field("Flexible", max_length=100, json_schema_extra={"example": "₹35,000 - ₹50,000"})
    timeline: str | None = Field("Immediate", max_length=100, json_schema_extra={"example": "Within 2 weeks"})


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    title: str
    target_deliverable: str
    core_features: list[str] | None = []
    tech_stack: list[str] | None = []
    architecture_overview: str | None = None
    implementation_steps: list[dict[str, Any]] | None = []
    approval_status: str
    approved_at: datetime | None = None
    created_at: datetime

    @field_validator("core_features", "tech_stack", mode="before")
    @classmethod
    def parse_str_list(cls, v):
        return _parse_json_if_str(v)

    @field_validator("implementation_steps", mode="before")
    @classmethod
    def parse_steps(cls, v):
        return _parse_json_if_str(v)


class BuildResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    project_name: str
    project_path: str
    status: str
    progress_percent: int
    generated_files: list[str] | None = []
    build_logs: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @field_validator("generated_files", mode="before")
    @classmethod
    def parse_files(cls, v):
        return _parse_json_if_str(v)


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = "website"
    requirement: str
    budget: str | None = "Flexible"
    timeline: str | None = "Immediate"
    score: int = 0
    temperature: str = "WARM"
    status: str = "NEW"
    payment_status: str = "UNPAID"
    payment_link: str | None = None
    advance_amount: int | None = 0
    ai_analysis: str | None = None
    created_at: datetime
    updated_at: datetime
    plan: PlanResponse | None = None
    builds: list[BuildResponse] | None = []


class PlanApprovalRequest(BaseModel):
    # Strict Literal — prevents invalid values from reaching the router
    decision: Literal["APPROVED", "REJECTED"] = Field(..., json_schema_extra={"example": "APPROVED"})
    feedback: str | None = Field(None, max_length=1000)


class BuildTriggerRequest(BaseModel):
    custom_instructions: str | None = Field(None, max_length=2000)


class StatsResponse(BaseModel):
    total_leads: int
    hot_deals: int
    warm_leads: int
    cold_leads: int
    pending_approvals: int
    built_projects: int
    estimated_pipeline_value: str


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int | None = None
    event_type: str
    title: str
    details: str | None = None
    channel: str
    created_at: datetime
