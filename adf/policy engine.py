"""
=======================================================
  ADF Self-Healing Policy Engine
  Munich RE x Capgemini DataRend Program

  Flow:
    Error Detection
      → Classify (known/unknown)
      → LLM generates fix
      → Safety / Blast Radius check
      → Notify if HIGH risk
      → Execute fix
      → Rerun pipeline
      → Check status
=======================================================
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum

from src.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
#  Enums
# =============================================================================

class ErrorClass(str, Enum):
    KNOWN   = "KNOWN"    # matched to catalog ADF-001 to ADF-006
    UNKNOWN = "UNKNOWN"  # no catalog match


class BlastRadius(str, Enum):
    LOW    = "LOW"    # safe — only restarts one pipeline
    MEDIUM = "MEDIUM" # moderate — touches ADF config or storage
    HIGH   = "HIGH"   # risky — schema change, mapping, data contract


class ExecutionMode(str, Enum):
    AUTO        = "AUTO"        # execute immediately, no approval needed
    CONDITIONAL = "CONDITIONAL" # execute only if validator passes
    MANUAL      = "MANUAL"      # block restart, notify team, wait for human


class PolicyOutcome(str, Enum):
    EXECUTE  = "EXECUTE"  # run the fix and restart
    BLOCKED  = "BLOCKED"  # do not restart, human must fix
    NOTIFY   = "NOTIFY"   # notify team and wait
    SKIPPED  = "SKIPPED"  # duplicate, already handled


# =============================================================================
#  Policy Rules — one entry per error code
# =============================================================================

POLICY_RULES: Dict[str, Dict[str, Any]] = {

    "ADF-001": {
        "description":   "Linked Service Connection Failure",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.LOW,
        "execution_mode": ExecutionMode.CONDITIONAL,
        "notify_team":   False,
        "safe_to_retry": True,
        "validator":     "validate_linked_service_connection",
        "rationale":     "Transient connection issue — safe to retry after validation",
    },

    "ADF-002": {
        "description":   "Schema Mismatch",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.MEDIUM,
        "execution_mode": ExecutionMode.CONDITIONAL,
        "notify_team":   True,
        "safe_to_retry": False,
        "validator":     "validate_schema_alignment",
        "rationale":     "Schema change may affect downstream — notify team, validate before restart",
    },

    "ADF-003": {
        "description":   "File Not Found",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.LOW,
        "execution_mode": ExecutionMode.AUTO,
        "notify_team":   False,
        "safe_to_retry": True,
        "validator":     "validate_file_exists_in_blob",
        "rationale":     "File may have arrived late — poll blob and restart automatically",
    },

    "ADF-004": {
        "description":   "Pipeline Timeout",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.LOW,
        "execution_mode": ExecutionMode.AUTO,
        "notify_team":   False,
        "safe_to_retry": True,
        "validator":     None,
        "rationale":     "Transient timeout — safe to restart with higher DIU",
    },

    "ADF-005": {
        "description":   "Stored Procedure Failure",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.LOW,
        "execution_mode": ExecutionMode.AUTO,
        "notify_team":   False,
        "safe_to_retry": True,
        "validator":     None,
        "rationale":     "Deadlock or transient SP failure — safe to retry",
    },

    "ADF-006": {
        "description":   "Parquet Invalid Column Name",
        "error_class":   ErrorClass.KNOWN,
        "blast_radius":  BlastRadius.HIGH,
        "execution_mode": ExecutionMode.MANUAL,
        "notify_team":   True,
        "safe_to_retry": False,
        "validator":     "validate_parquet_column_mapping",
        "rationale":     "Data contract issue — restarting without fixing mapping will loop. Human must fix ADF column mapping first.",
    },

    "UNKNOWN": {
        "description":   "Unclassified Error",
        "error_class":   ErrorClass.UNKNOWN,
        "blast_radius":  BlastRadius.HIGH,
        "execution_mode": ExecutionMode.MANUAL,
        "notify_team":   True,
        "safe_to_retry": False,
        "validator":     None,
        "rationale":     "Unknown error — never auto-restart. Notify team for investigation.",
    },
}


# =============================================================================
#  Policy Decision dataclass
# =============================================================================

@dataclass
class PolicyDecision:
    error_code:      str
    error_class:     ErrorClass
    blast_radius:    BlastRadius
    execution_mode:  ExecutionMode
    outcome:         PolicyOutcome
    notify_team:     bool
    safe_to_retry:   bool
    rationale:       str
    validator:       Optional[str]
    llm_fix:         Optional[str] = None
    validator_passed: bool = False


# =============================================================================
#  Main Policy Engine
# =============================================================================

class PolicyEngine:
    """
    Full policy flow:
      1. Classify error (known/unknown)
      2. LLM generates fix suggestion
      3. Safety / blast radius check
      4. Decide outcome (EXECUTE / BLOCKED / NOTIFY / SKIPPED)
      5. Return PolicyDecision to caller
    """

    def evaluate(
        self,
        detection: Dict[str, Any],
        resolution: Dict[str, Any],
        pipeline_name: str,
        already_handled: bool = False,
    ) -> PolicyDecision:
        """
        Run full policy evaluation for a detected failure.
        Returns PolicyDecision with outcome and all context.
        """
        error_code = detection.get("matched_error_code", "UNKNOWN")
        confidence = detection.get("confidence", 0.0)

        logger.info(
            "PolicyEngine.evaluate | pipeline=%s | error=%s | confidence=%.0f%%",
            pipeline_name, error_code, confidence * 100,
        )

        # ── Step 1: Classify ──────────────────────────────────────────────
        rule       = POLICY_RULES.get(error_code, POLICY_RULES["UNKNOWN"])
        error_class = self._classify(error_code, confidence)
        logger.info("Step 1 Classify: %s → %s", error_code, error_class)

        # ── Step 2: LLM fix suggestion ────────────────────────────────────
        llm_fix = self._get_llm_fix(detection)
        logger.info("Step 2 LLM fix: %s", llm_fix[:100] if llm_fix else "None")

        # ── Step 3: Safety / Blast Radius check ───────────────────────────
        blast_radius = BlastRadius(rule["blast_radius"])
        logger.info("Step 3 Blast radius: %s", blast_radius)

        # ── Step 4: Determine outcome ─────────────────────────────────────
        outcome = self._decide_outcome(
            rule=rule,
            error_class=error_class,
            blast_radius=blast_radius,
            resolution=resolution,
            already_handled=already_handled,
        )
        logger.info("Step 4 Outcome: %s | rationale: %s", outcome, rule["rationale"])

        # ── Step 5: Notify if HIGH risk ───────────────────────────────────
        notify_team = rule["notify_team"] or blast_radius == BlastRadius.HIGH
        if notify_team:
            self._notify(pipeline_name, error_code, outcome, rule["rationale"], llm_fix)

        decision = PolicyDecision(
            error_code=error_code,
            error_class=error_class,
            blast_radius=blast_radius,
            execution_mode=ExecutionMode(rule["execution_mode"]),
            outcome=outcome,
            notify_team=notify_team,
            safe_to_retry=rule["safe_to_retry"],
            rationale=rule["rationale"],
            validator=rule["validator"],
            llm_fix=llm_fix,
        )

        logger.info(
            "PolicyDecision | pipeline=%s | outcome=%s | safe_to_retry=%s | notify=%s",
            pipeline_name, outcome, decision.safe_to_retry, notify_team,
        )

        return decision

    # ── Step 1: Classify ──────────────────────────────────────────────────

    def _classify(self, error_code: str, confidence: float) -> ErrorClass:
        """
        KNOWN   = matched to catalog with confidence > 0.5
        UNKNOWN = no match or low confidence
        """
        if error_code == "UNKNOWN" or confidence < 0.5:
            return ErrorClass.UNKNOWN
        return ErrorClass.KNOWN

    # ── Step 2: LLM fix suggestion ────────────────────────────────────────

    def _get_llm_fix(self, detection: Dict[str, Any]) -> Optional[str]:
        """
        Extract the LLM-generated fix suggestion from detection result.
        Falls back to catalog troubleshooting steps if no LLM suggestion.
        """
        # Try LLM suggested steps first
        suggested = detection.get("suggested_steps", [])
        if suggested:
            return " | ".join(suggested[:3])

        # Try catalog entry
        catalog = detection.get("catalog_entry", {})
        steps   = catalog.get("troubleshooting_steps", [])
        if steps:
            return " | ".join(steps[:3])

        return detection.get("detected_root_cause", None)

    # ── Step 4: Decide outcome ────────────────────────────────────────────

    def _decide_outcome(
        self,
        rule: Dict[str, Any],
        error_class: ErrorClass,
        blast_radius: BlastRadius,
        resolution: Dict[str, Any],
        already_handled: bool,
    ) -> PolicyOutcome:
        """
        Decision logic:
          SKIPPED  → duplicate, already handled recently
          BLOCKED  → MANUAL mode OR UNKNOWN error OR HIGH blast without validation
          NOTIFY   → HIGH blast radius with CONDITIONAL mode
          EXECUTE  → AUTO mode OR CONDITIONAL with resolution success
        """

        # Duplicate check
        if already_handled:
            return PolicyOutcome.SKIPPED

        mode = ExecutionMode(rule["execution_mode"])

        # MANUAL always blocks
        if mode == ExecutionMode.MANUAL:
            return PolicyOutcome.BLOCKED

        # Unknown errors always block
        if error_class == ErrorClass.UNKNOWN:
            return PolicyOutcome.BLOCKED

        # HIGH blast radius — notify and block unless validator passed
        if blast_radius == BlastRadius.HIGH:
            validator_passed = resolution.get("validator_passed", False)
            if not validator_passed:
                return PolicyOutcome.NOTIFY

        # CONDITIONAL — only execute if resolution succeeded
        if mode == ExecutionMode.CONDITIONAL:
            if resolution.get("success", False):
                return PolicyOutcome.EXECUTE
            else:
                return PolicyOutcome.NOTIFY

        # AUTO — safe to execute
        if mode == ExecutionMode.AUTO:
            return PolicyOutcome.EXECUTE

        return PolicyOutcome.BLOCKED

    # ── Step 5: Notify ────────────────────────────────────────────────────

    def _notify(
        self,
        pipeline_name: str,
        error_code: str,
        outcome: PolicyOutcome,
        rationale: str,
        llm_fix: Optional[str],
    ) -> None:
        """
        Send notification when HIGH risk or MANUAL error detected.
        Currently logs — extend with Teams/email webhook here.
        """
        message = (
            f"ADF SELF-HEALING ALERT\n"
            f"Pipeline   : {pipeline_name}\n"
            f"Error Code : {error_code}\n"
            f"Outcome    : {outcome}\n"
            f"Rationale  : {rationale}\n"
            f"LLM Fix    : {llm_fix or 'No suggestion'}\n"
            f"Action     : {'Manual fix required — check ADF Studio' if outcome in [PolicyOutcome.BLOCKED, PolicyOutcome.NOTIFY] else 'Auto-fix executing'}"
        )

        logger.error("NOTIFICATION:\n%s", message)

        # TODO: Add Teams webhook here
        # teams_url = os.environ.get("TEAMS_WEBHOOK_URL", "")
        # if teams_url:
        #     import requests
        #     requests.post(teams_url, json={"text": message})

        # TODO: Add email/SMTP here
        # send_email(subject=f"ADF Alert: {error_code} on {pipeline_name}", body=message)


# =============================================================================
#  Convenience function — used by HttpTrigger1 and TimerTrigger1
# =============================================================================

def evaluate_policy(
    detection: Dict[str, Any],
    resolution: Dict[str, Any],
    pipeline_name: str,
    already_handled: bool = False,
) -> PolicyDecision:
    """
    Single entry point for policy evaluation.
    Called from HttpTrigger1 and TimerTrigger1.

    Returns PolicyDecision with:
      .outcome        → EXECUTE / BLOCKED / NOTIFY / SKIPPED
      .safe_to_retry  → bool — whether to call restart_pipeline()
      .notify_team    → bool — whether notification was sent
      .rationale      → human-readable reason for the decision
      .llm_fix        → LLM suggested fix steps
    """
    engine = PolicyEngine()
    return engine.evaluate(
        detection=detection,
        resolution=resolution,
        pipeline_name=pipeline_name,
        already_handled=already_handled,
    )
