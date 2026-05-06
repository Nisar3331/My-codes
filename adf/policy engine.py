"""
=======================================================
  Policy Engine — decides if auto-restart is safe

  AUTO        → safe, deterministic, low risk
                restart immediately
  CONDITIONAL → only restart if resolver validator passed
                (resolver must set validator_passed=True)
  MANUAL      → risky, unknown, data-contract issues
                never auto-restart, alert operator

  ADF-006 is CONDITIONAL:
    The resolver downloads the source CSV, sanitises
    column names, and re-uploads it. If that succeeds,
    validator_passed=True and the restart is allowed.
    If it fails, validator_passed=False and we block.
=======================================================
"""

from typing import Dict, Any, Tuple
from src.logger import get_logger

logger = get_logger(__name__)

# Policy per error code
POLICY: Dict[str, str] = {
    "ADF-001": "CONDITIONAL",  # linked service — test connection first
    "ADF-002": "CONDITIONAL",  # schema mismatch — validate schema first
    "ADF-003": "AUTO",         # file not found — check if file arrived
    "ADF-004": "AUTO",         # timeout — safe to retry with higher DIU
    "ADF-005": "AUTO",         # stored proc deadlock — safe to retry
    "ADF-006": "CONDITIONAL",  # parquet column — resolver must fix CSV first
    "UNKNOWN": "MANUAL",       # unknown — never auto-restart
}


def should_restart(
    detection: Dict[str, Any],
    resolution: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Returns (should_restart: bool, reason: str)

    AUTO       → always restart
    CONDITIONAL → restart only if resolution.validator_passed == True
    MANUAL     → never restart, alert operator
    """
    error_code = detection.get("matched_error_code", "UNKNOWN")
    policy     = POLICY.get(error_code, "MANUAL")

    logger.info("Policy for [%s]: %s", error_code, policy)

    if policy == "MANUAL":
        return False, (
            f"[{error_code}] Policy=MANUAL — manual fix required before restart"
        )

    if policy == "AUTO":
        return True, f"[{error_code}] Policy=AUTO — safe to restart"

    if policy == "CONDITIONAL":
        validator_passed = resolution.get("validator_passed", False)
        if validator_passed:
            return True, (
                f"[{error_code}] Policy=CONDITIONAL — validator passed, restart allowed"
            )
        else:
            return False, (
                f"[{error_code}] Policy=CONDITIONAL — validator did not pass, "
                "manual fix required"
            )

    return False, f"[{error_code}] Unknown policy — defaulting to MANUAL"
