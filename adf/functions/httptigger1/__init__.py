"""
=======================================================
  HttpTrigger1 — ADF Self-Healing Alert Handler

  COMPLETE FLOW:
    1. ADF pipeline fails
    2. Azure Monitor fires alert → calls this function
    3. LLM identifies the error type (ADF-001 … ADF-006)
    4. Resolver ACTUALLY FIXES the issue:
         ADF-001 → validates linked service
         ADF-002 → refreshes dataset schema
         ADF-003 → waits / polls for file
         ADF-004 → logs timeout guidance
         ADF-005 → logs SP deadlock guidance
         ADF-006 → downloads CSV, fixes column names,
                   re-uploads to blob BEFORE restart
    5. Policy engine decides if restart is safe
    6. Circuit breaker prevents infinite restart loops
    7. Pipeline restarts ONLY after fix is confirmed
=======================================================
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import azure.functions as func

from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.adf_pipeline_manager import AdfPipelineManager
from src.policy_engine import should_restart
from src.incident_store import is_already_handled, mark_as_handled
from config.settings import AZURE_CONFIG


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("=" * 60)
    logging.info("ADF Self-Healing — Alert Received")
    logging.info("=" * 60)

    # ── 1. Parse alert payload ────────────────────────────────────
    try:
        body = req.get_json()
    except Exception as exc:
        logging.error("Failed to parse request body: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    pipeline_info = _extract_pipeline_info(body)

    if not pipeline_info:
        logging.info("Alert ignored (resolved or unparseable)")
        return func.HttpResponse(
            json.dumps({"status": "ignored"}),
            status_code=200,
            mimetype="application/json",
        )

    pipeline_name = pipeline_info["pipeline_name"]
    run_id        = pipeline_info.get("run_id", "unknown")
    activity_name = pipeline_info.get("activity_name", "unknown")
    error_message = pipeline_info.get("error_message", "")

    logging.error(
        "FAILURE | pipeline=%s | run_id=%s | activity=%s | error=%s",
        pipeline_name, run_id, activity_name, str(error_message)[:300],
    )

    # ── 2. Detect error type via LLM ─────────────────────────────
    logging.info("[%s] STEP 1/4 — Detecting error with LLM ...", pipeline_name)
    try:
        detector  = LlmErrorDetector()
        detection = detector.detect(
            error_message=str(error_message),
            pipeline_name=pipeline_name,
            activity_name=activity_name,
        )
        logging.info(
            "[%s] Detected: %s (confidence=%.0f%%)",
            pipeline_name,
            detection.get("matched_error_code"),
            detection.get("confidence", 0) * 100,
        )
    except Exception as exc:
        logging.error("[%s] LLM detection failed: %s", pipeline_name, exc)
        detection = {
            "matched_error_code": "UNKNOWN",
            "matched_error_name": "Unknown Error",
            "confidence": 0.0,
            "auto_resolvable": False,
            "resolution_action": "manual_investigation_required",
        }

    error_code = detection.get("matched_error_code", "UNKNOWN")

    # ── 3. Circuit breaker — skip if same error handled recently ─
    logging.info("[%s] STEP 2/4 — Checking circuit breaker ...", pipeline_name)
    if is_already_handled(pipeline_name, error_code):
        logging.warning(
            "[%s] [%s] Same error already handled in cooldown window — "
            "STOPPING to prevent restart loop",
            pipeline_name, error_code,
        )
        return func.HttpResponse(
            json.dumps({
                "status":     "circuit_breaker_triggered",
                "pipeline":   pipeline_name,
                "error_code": error_code,
                "message":    "Same error handled within 30 min — no restart to prevent loop",
            }),
            status_code=200,
            mimetype="application/json",
        )

    # ── 4. Fix the issue before restarting ───────────────────────
    logging.info("[%s] STEP 3/4 — Running fix ...", pipeline_name)
    try:
        resolver   = AdfErrorResolver()
        resolution = resolver.resolve(detection)
        logging.info(
            "[%s] Fix result: success=%s | %s",
            pipeline_name,
            resolution.get("success"),
            resolution.get("message", "")[:200],
        )
    except Exception as exc:
        logging.error("[%s] Fix failed: %s", pipeline_name, exc)
        resolution = {"success": False, "validator_passed": False,
                      "message": str(exc)}

    # ── 5. Policy check — is restart safe after the fix? ─────────
    logging.info("[%s] STEP 4/4 — Checking restart policy ...", pipeline_name)
    ok_to_restart, reason = should_restart(detection, resolution)
    logging.info("[%s] Policy decision: restart=%s — %s",
                 pipeline_name, ok_to_restart, reason)

    # ── 6. Restart pipeline (only if fix worked + policy allows) ─
    restart_result = {"restarted": False, "reason": reason}

    if ok_to_restart:
        try:
            mgr        = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=10,   # short pause so ADF settles
            )
            restart_result = {"restarted": True, "new_run_id": new_run_id}
            logging.info(
                "[%s] ✅ RESTARTED SUCCESSFULLY | new_run_id=%s",
                pipeline_name, new_run_id,
            )
        except Exception as exc:
            restart_result = {"restarted": False, "error": str(exc)}
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        logging.error(
            "[%s] ❌ NOT restarting — %s\n"
            "    → Fix the issue manually, then re-trigger the pipeline.",
            pipeline_name, reason,
        )

    # Mark as handled to start the 30-min cooldown window
    mark_as_handled(pipeline_name, error_code, run_id)

    result = {
        "pipeline":   pipeline_name,
        "error_code": error_code,
        "confidence": round(detection.get("confidence", 0), 2),
        "fix":        resolution.get("message", ""),
        "policy":     reason,
        "restart":    restart_result,
    }

    logging.info("[%s] Done: %s", pipeline_name, json.dumps(result, default=str))
    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )


# ── Alert payload parser ─────────────────────────────────────────────────────

def _extract_pipeline_info(data: dict) -> dict | None:
    """
    Extract pipeline name, run_id, activity, and error from the
    Azure Monitor Common Alert Schema payload.

    Priority 1: searchResults table (Log Analytics alert)
    Priority 2: alertContext.properties (metric/activity alert)
    """
    payload   = data.get("data", data)
    alert_ctx = payload.get("alertContext", {})

    # Priority 1 — Log Analytics search results row
    tables = alert_ctx.get("searchResults", {}).get("tables", [])
    if tables and tables[0].get("rows"):
        columns = [c["name"] for c in tables[0]["columns"]]
        row     = dict(zip(columns, tables[0]["rows"][0]))

        pipeline_name = (
            row.get("pipelineName") or row.get("PipelineName") or ""
        )
        error_message = (
            row.get("errorMessage") or row.get("ErrorMessage") or
            row.get("errorMessageRaw") or row.get("ErrorMessageRaw") or ""
        )

        if pipeline_name:
            return {
                "pipeline_name": pipeline_name,
                "run_id":        row.get("runId") or row.get("RunId"),
                "activity_name": row.get("activityName") or row.get("ActivityName"),
                "error_message": error_message,
                "source":        "log_analytics",
            }

    # Priority 2 — properties fallback
    essentials = payload.get("essentials", {})
    properties = alert_ctx.get("properties", {})

    if essentials.get("monitorCondition") == "Resolved":
        return None

    pipeline_name = (
        properties.get("pipelineName") or
        properties.get("pipeline_name") or
        AZURE_CONFIG.adf_pipeline_name or
        "unknown-pipeline"
    )
    error_message = (
        properties.get("errorMessage") or
        properties.get("message") or
        f"ADF pipeline failure — alert: {essentials.get('alertRule', '')}"
    )

    return {
        "pipeline_name": pipeline_name,
        "run_id":        properties.get("runId"),
        "activity_name": properties.get("activityName"),
        "error_message": error_message,
        "source":        "properties_fallback",
    }
