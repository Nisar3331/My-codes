from __future__ import annotations
from typing import Dict, Any

class StubLLM:
    """Deterministic placeholder. Replace with Bedrock/Azure OpenAI/Vertex/Anthropic."""

    def classify(self, context: Dict[str, Any]) -> Dict[str, Any]:
        logs = (context.get("logs") or "").lower()
        if "accessdenied" in logs or "permission" in logs:
            return {"error_type": "PERMISSION_DENIED", "confidence": 0.75, "summary": "Permission-related failure"}
        if "timeout" in logs or "timed out" in logs:
            return {"error_type": "TIMEOUT", "confidence": 0.70, "summary": "Transient timeout"}
        return {"error_type": "UNKNOWN", "confidence": 0.50, "summary": "Unknown error"}

    def propose(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "plan_id": "LLM-0001",
            "description": "Fallback: retry once and escalate",
            "steps": [{"action": "rerun_job", "parameters": {"attempts": 1}}],
            "risk": "LOW",
            "requires_approval": True,
            "confidence": 0.55,
        }
