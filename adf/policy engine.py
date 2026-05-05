"""
=======================================================
  Policy Engine — decides if auto-restart is safe
  
  AUTO       → safe, deterministic, low risk
  CONDITIONAL → only if validator passes
  MANUAL     → risky, unknown, data-contract issues
=======================================================
"""

from typing import Dict, Any
from src.logger import get_logger

logger = get_logger(__name__)

# Policy per error code
POLICY = {
    "ADF-001": "CONDITIONAL",  # linked service — test connection first
    "ADF-002": "CONDITIONAL",  # schema mismatch — validate schema first
    "ADF-003": "AUTO",         # file not found — check if file arrived
    "ADF-004": "AUTO",         # timeout — safe to retry with higher DIU
    "ADF-005": "AUTO",         # stored proc deadlock — safe to retry
    "ADF-006": "MANUAL",       # parquet column — must fix ADF mapping first
    "UNKNOWN": "MANUAL",       # unknown — never auto-restart
}


def should_restart(detection: Dict[str, Any], resolution: Dict[str, Any]) -> tuple[bool, str]:
    """
    Returns (should_restart: bool, reason: str)
    
    Only returns True if:
    1. Policy says AUTO
    2. Policy says CONDITIONAL and validator passed
    """
    error_code = detection.get("matched_error_code", "UNKNOWN")
    policy     = POLICY.get(error_code, "MANUAL")

    logger.info("Policy for [%s]: %s", error_code, policy)

    if policy == "MANUAL":
        return False, f"[{error_code}] Policy=MANUAL — manual fix required before restart"

    if policy == "AUTO":
        return True, f"[{error_code}] Policy=AUTO — safe to restart"

    if policy == "CONDITIONAL":
        # Only restart if resolver actually validated something
        validator_passed = resolution.get("validator_passed", False)
        if validator_passed:
            return True, f"[{error_code}] Policy=CONDITIONAL — validator passed"
        else:
            return False, f"[{error_code}] Policy=CONDITIONAL — validator did not pass, manual fix needed"

    return False, f"[{error_code}] Unknown policy — defaulting to MANUAL"
