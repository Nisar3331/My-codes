from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal

Platform = Literal["aws_glue", "azure_data_factory", "azure_databricks", "gcp_composer", "airflow"]

class FailureEvent(BaseModel):
    platform: Platform
    instance_name: str
    run_id: str
    occurred_at: str
    payload: Dict[str, Any] = Field(default_factory=dict)

class ErrorClassification(BaseModel):
    error_type: str
    confidence: float = Field(ge=0, le=1)
    summary: str
    evidence: Dict[str, Any] = Field(default_factory=dict)

class RemediationStep(BaseModel):
    action: str
    parameters: Dict[str, Any] = Field(default_factory=dict)

class RemediationPlan(BaseModel):
    source: Literal["SOP", "LLM"]
    plan_id: str
    description: str
    steps: List[RemediationStep]
    risk: Literal["LOW", "MED", "HIGH"] = "MED"
    requires_approval: bool = True
    confidence: float = Field(default=0.5, ge=0, le=1)

class SelfHealResult(BaseModel):
    event: FailureEvent
    classification: ErrorClassification
    plan: RemediationPlan
    safety: Dict[str, Any]
    executed: bool
    execution_result: Optional[Dict[str, Any]] = None
