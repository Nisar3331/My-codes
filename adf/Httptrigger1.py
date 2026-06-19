"""
=======================================================
  HttpTrigger1 — Azure Monitor Alert Handler
  FIXED VERSION with:
  - Policy Engine (no blind restarts)
  - Incident Store (no duplicate restarts)
  - Real validation before restart
=======================================================
"""

import json
import logging
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import azure.functions as func

from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.adf_pipeline_manager import AdfPipelineManager
from src.policy_engine import should_restart
from src.incident_store import is_already_handled, mark_as_handled
from config.settings import AZURE_CONFIG


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("=== HttpTrigger1 — Monitor Alert received ===")

    try:
        body = req.get_json()
    except Exception as exc:
        logging.error("Failed to parse request body: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    logging.info("Alert payload: %s", json.dumps(body, default=str)[:800])

    pipeline_info = _extract_from_monitor_payload(body)

    if not pipeline_info:
        return func.HttpResponse(
            json.dumps({"status": "ignored"}),
            status_code=200,
            mimetype="application/json",
        )

    pipeline_name = pipeline_info["pipeline_name"]
    run_id        = pipeline_info.get("run_id", "unknown")
    activity_name = pipeline_info.get("activity_name")
    error_message = pipeline_info.get("error_message", "")

    logging.error(
        "ADF FAILURE | pipeline=%s | run_id=%s | activity=%s | error=%s",
        pipeline_name, run_id, activity_name, str(error_message)[:300],
    )

    # ── Step 1: LLM Detection ─────────────────────────────────────────────
    logging.info("[%s] Running LLM detection ...", pipeline_name)
    try:
        detector  = LlmErrorDetector()
        detection = detector.detect(
            error_message=str(error_message),
            pipeline_name=pipeline_name,
            activity_name=activity_name,
        )
        logging.info(
            "[%s] Detected: [%s] confidence=%.0f%%",
            pipeline_name,
            detection.get("matched_error_code"),
            detection.get("confidence", 0) * 100,
        )
    except Exception as exc:
        logging.error("[%s] LLM detection failed: %s", pipeline_name, exc)
        detection = {
            "matched_error_code": "UNKNOWN",
            "confidence": 0.0,
            "auto_resolvable": False,
            "resolution_action": "manual_investigation_required",
        }

    error_code = detection.get("matched_error_code", "UNKNOWN")

    # ── Step 2: Dedupe check — skip if already handled ────────────────────
    if is_already_handled(pipeline_name, error_code):
        logging.warning(
            "[%s] [%s] already handled recently — skipping to prevent restart loop",
            pipeline_name, error_code,
        )
        return func.HttpResponse(
            json.dumps({"status": "skipped_duplicate", "pipeline": pipeline_name, "error_code": error_code}),
            status_code=200,
            mimetype="application/json",
        )

    # ── Step 3: Resolution ────────────────────────────────────────────────
    logging.info("[%s] Running resolution ...", pipeline_name)
    try:
        resolver   = AdfErrorResolver()
        resolution = resolver.resolve(detection)
        logging.info(
            "[%s] Resolution: success=%s | %s",
            pipeline_name,
            resolution.get("success"),
            resolution.get("message", "")[:150],
        )
    except Exception as exc:
        logging.error("[%s] Resolution failed: %s", pipeline_name, exc)
        resolution = {"success": False}

    # ── Step 4: Policy check — should we restart? ─────────────────────────
    ok_to_restart, reason = should_restart(detection, resolution)
    logging.info("[%s] Policy decision: %s | reason: %s", pipeline_name, ok_to_restart, reason)

    # ── Step 5: Restart if safe ───────────────────────────────────────────
    restart_result = {"restarted": False, "reason": reason}

    if ok_to_restart:
        logging.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr        = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=30,
            )
            restart_result = {"restarted": True, "new_run_id": new_run_id}
            logging.info(
                "[%s] RESTARTED SUCCESSFULLY | new_run_id=%s",
                pipeline_name, new_run_id,
            )
            # Mark as handled to prevent duplicate restarts
            mark_as_handled(pipeline_name, error_code, run_id)

        except Exception as exc:
            restart_result = {"restarted": False, "error": str(exc)}
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        logging.error(
            "[%s] [%s] NOT restarting — %s",
            pipeline_name, error_code, reason,
        )
        # Still mark as handled to prevent notification spam
        mark_as_handled(pipeline_name, error_code, run_id)

    result = {
        "pipeline_name": pipeline_name,
        "error_code":    error_code,
        "detection":     detection.get("matched_error_code"),
        "confidence":    detection.get("confidence", 0),
        "resolved":      resolution.get("success"),
        "policy":        reason,
        "restart":       restart_result,
    }

    logging.info("[%s] Complete: %s", pipeline_name, json.dumps(result, default=str))

    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )


def _extract_from_monitor_payload(data: dict) -> dict | None:
    payload   = data.get("data", data)
    alert_ctx = payload.get("alertContext", {})

    # Priority 1 — Log Analytics searchResults
    search = alert_ctx.get("searchResults", {})
    tables = search.get("tables", [])

    logging.info("DEBUG searchResults tables: %d", len(tables))

    if tables and tables[0].get("rows"):
        columns = [c["name"] for c in tables[0]["columns"]]
        values  = tables[0]["rows"][0]
        row     = dict(zip(columns, values))

        logging.info("DEBUG row from searchResults: %s", json.dumps(row, default=str))

        pipeline_name = (
            row.get("pipelineName") or
            row.get("PipelineName") or
            ""
        )
        error_message = (
            row.get("errorMessage") or
            row.get("ErrorMessage") or
            row.get("errorMessageRaw") or
            row.get("ErrorMessageRaw") or
            ""
        )

        if pipeline_name:
            return {
                "pipeline_name": pipeline_name,
                "run_id":        row.get("runId") or row.get("RunId"),
                "activity_name": row.get("activityName") or row.get("ActivityName"),
                "error_code":    row.get("errorCode") or row.get("ErrorCode"),
                "error_message": error_message,
                "source":        "log_analytics",
            }

    logging.warning("searchResults empty — using fallback")

    # Priority 2 — properties fallback
    essentials = payload.get("essentials", {})
    properties = alert_ctx.get("properties", {})

    if essentials.get("monitorCondition") == "Resolved":
        return None

    return {
        "pipeline_name": (
            properties.get("pipelineName") or
            AZURE_CONFIG.adf_pipeline_name or
            "unknown-pipeline"
        ),
        "run_id":        properties.get("runId"),
        "activity_name": properties.get("activityName"),
        "error_code":    properties.get("errorCode"),
        "error_message": (
            properties.get("errorMessage") or
            f"ADF pipeline failure — alert: {essentials.get('alertRule')}"
        ),
        "source": "properties_fallback",
    }
