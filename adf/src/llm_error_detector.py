"""
=======================================================
  LLM Error Detector
  Calls Azure AI Foundry (Azure OpenAI GPT-4o) to:
    1. Classify an ADF error message against the catalog
    2. Return structured JSON with matched error code,
       confidence, root cause, and recommended action
=======================================================
"""

import json
import re
from typing import Optional, Dict, Any

from openai import AzureOpenAI

from src.logger import get_logger
from src.error_catalog import ADF_ERROR_CATALOG, get_error_catalog_as_dict, get_error_by_code
from config.settings import AZURE_CONFIG

logger = get_logger(__name__)

# ── System prompt injected into every LLM call ──────────────────────────────
_SYSTEM_PROMPT = """
You are an expert Azure Data Factory (ADF) pipeline diagnostics assistant.
Your job is to analyze ADF pipeline error messages and match them to known error types.

You will be given:
1. An error message from a failed ADF pipeline run
2. A catalog of known ADF errors with their codes, names, causes, and resolution actions

Your response MUST be a valid JSON object with NO additional text, following this exact schema:
{
  "matched_error_code": "<ADF-XXX or UNKNOWN>",
  "matched_error_name": "<string>",
  "confidence": <0.0 to 1.0>,
  "detected_root_cause": "<concise root cause explanation>",
  "resolution_action": "<the resolution_action field from the catalog>",
  "suggested_steps": ["<step 1>", "<step 2>", ...],
  "auto_resolvable": <true|false>,
  "severity": "<HIGH|MEDIUM|LOW>",
  "explanation": "<1-2 sentence human-readable explanation of why you matched this error>"
}

Rules:
- If no catalog entry matches with confidence > 0.5, set matched_error_code to "UNKNOWN"
- suggested_steps must be the troubleshooting_steps from the matched error
- Be precise — do not hallucinate error codes that are not in the catalog
"""


class LlmErrorDetector:
    """
    Uses Azure AI Foundry (Azure OpenAI) to detect and classify
    ADF pipeline errors against the defined error catalog.
    """

    def __init__(self):
        self._client = AzureOpenAI(
            azure_endpoint=AZURE_CONFIG.azure_openai_endpoint,
            api_key=AZURE_CONFIG.azure_openai_api_key,
            api_version=AZURE_CONFIG.azure_openai_api_version,
        )
        self._deployment = AZURE_CONFIG.llm_deployment_name
        self._catalog = get_error_catalog_as_dict()

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(
        self,
        error_message: str,
        pipeline_name: Optional[str] = None,
        activity_name: Optional[str] = None,
        additional_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyse an ADF error message and return a structured detection result.

        Args:
            error_message: The raw error string from ADF pipeline run
            pipeline_name: (optional) ADF pipeline name for context
            activity_name: (optional) Activity where the error occurred
            additional_context: (optional) Extra context (stack trace, run ID, etc.)

        Returns:
            dict with matched error code, confidence, resolution action, etc.
        """
        logger.info("🔍 LLM Error Detection started")
        logger.info("   Error: %s", error_message[:200])

        user_message = self._build_user_message(
            error_message, pipeline_name, activity_name, additional_context
        )

        try:
            response = self._call_llm(user_message)
            result = self._parse_response(response)
            self._enrich_result(result)
            logger.info(
                " Detection complete — Code: %s | Confidence: %.2f | Auto-resolvable: %s",
                result.get("matched_error_code"),
                result.get("confidence", 0),
                result.get("auto_resolvable"),
            )
            return result

        except Exception as exc:
            logger.error(" LLM detection failed: %s", str(exc))
            return self._fallback_detection(error_message)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_user_message(
        self,
        error_message: str,
        pipeline_name: Optional[str],
        activity_name: Optional[str],
        additional_context: Optional[str],
    ) -> str:
        parts = ["## ADF Pipeline Error Report\n"]
        if pipeline_name:
            parts.append(f"**Pipeline:** {pipeline_name}")
        if activity_name:
            parts.append(f"**Activity:** {activity_name}")
        parts.append(f"\n**Error Message:**\n```\n{error_message}\n```")
        if additional_context:
            parts.append(f"\n**Additional Context:**\n{additional_context}")

        # Inject catalog so the LLM can reason against it
        parts.append("\n## Known ADF Error Catalog\n```json")
        parts.append(json.dumps(self._catalog, indent=2))
        parts.append("```")
        parts.append(
            "\nMatch the error message above to the most relevant entry in the catalog "
            "and return the JSON result as specified."
        )
        return "\n".join(parts)

    def _call_llm(self, user_message: str) -> str:
        """Send request to Azure AI Foundry and return raw content string."""
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.1,   # Low temperature for deterministic classification
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """Parse the LLM JSON response safely."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON block from response
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"Could not parse LLM response as JSON: {raw[:300]}")

    def _enrich_result(self, result: Dict[str, Any]) -> None:
        """Add the full AdfError object details to the result if code is known."""
        code = result.get("matched_error_code", "UNKNOWN")
        if code != "UNKNOWN":
            catalog_entry = get_error_by_code(code)
            if catalog_entry:
                result["catalog_entry"] = {
                    "error_code": catalog_entry.error_code,
                    "error_name": catalog_entry.error_name,
                    "main_cause": catalog_entry.main_cause,
                    "business_impact": catalog_entry.business_impact,
                    "resolution_action": catalog_entry.resolution_action,
                    "troubleshooting_steps": catalog_entry.troubleshooting_steps,
                }

    def _fallback_detection(self, error_message: str) -> Dict[str, Any]:
        """
        Keyword-based fallback when LLM call fails.
        Scores each catalog entry by keyword overlap.
        """
        logger.warning("  Using keyword fallback detection")
        error_lower = error_message.lower()
        best_score = 0
        best_entry = None

        for entry in ADF_ERROR_CATALOG:
            score = sum(1 for kw in entry.keywords if kw in error_lower)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry and best_score > 0:
            confidence = min(best_score / len(best_entry.keywords), 0.75)
            return {
                "matched_error_code": best_entry.error_code,
                "matched_error_name": best_entry.error_name,
                "confidence": confidence,
                "detected_root_cause": best_entry.main_cause,
                "resolution_action": best_entry.resolution_action,
                "suggested_steps": best_entry.troubleshooting_steps,
                "auto_resolvable": best_entry.auto_resolvable,
                "severity": best_entry.severity,
                "explanation": f"Keyword-based fallback match (score={best_score})",
                "detection_method": "keyword_fallback",
            }

        return {
            "matched_error_code": "UNKNOWN",
            "matched_error_name": "Unknown Error",
            "confidence": 0.0,
            "detected_root_cause": "Could not determine root cause automatically",
            "resolution_action": "manual_investigation_required",
            "suggested_steps": ["Review ADF activity run details manually", "Check Azure Monitor logs"],
            "auto_resolvable": False,
            "severity": "HIGH",
            "explanation": "No catalog match found via keyword fallback",
            "detection_method": "keyword_fallback",
        }
