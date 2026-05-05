"""
=======================================================
  HttpTrigger1 — Azure Monitor Alert Handler
 
  Called by: Azure Monitor → Alert Rule → Action Group
             → Azure Function (this file)
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
from config.settings import AZURE_CONFIG
 
 
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("=== HttpTrigger1 — Monitor Alert received ===")
 
    # ── Parse request body ────────────────────────────────────────────────
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
 
    # ── Extract pipeline context ───────────────────────────────────────────
    pipeline_info = _extract_from_monitor_payload(body)
 
    if not pipeline_info:
        logging.warning("No pipeline info found — ignoring alert")
        return func.HttpResponse(
            json.dumps({"status": "ignored"}),
            status_code=200,
            mimetype="application/json",
        )
 
    pipeline_name  = pipeline_info["pipeline_name"]
    run_id         = pipeline_info.get("run_id", "unknown")
    activity_name  = pipeline_info.get("activity_name")
    error_code     = pipeline_info.get("error_code")
    error_message  = pipeline_info.get("error_message")
 
    logging.error(
        "ADF FAILURE | pipeline=%s | run_id=%s | activity=%s | error=%s",
        pipeline_name, run_id, activity_name, error_message[:300],
    )
 
    # ── Step 1: LLM Detection ───────────────────────────────────────────────
    logging.info("[%s] Running LLM detection ...", pipeline_name)
    try:
        detector = LlmErrorDetector()
        detection = detector.detect(
            error_message=error_message,
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
            "matched_error_code": error_code or "UNKNOWN",
            "confidence": 0.0,
            "auto_resolvable": False,
            "resolution_action": "manual_investigation_required",
        }
 
    # ── Step 2: Resolution ─────────────────────────────────────────────────
    logging.info("[%s] Running resolution ...", pipeline_name)
    try:
        resolver = AdfErrorResolver()
        resolution = resolver.resolve(detection)
        logging.info(
            "[%s] Resolution: success=%s",
            pipeline_name, resolution.get("success"),
        )
    except Exception as exc:
        logging.error("[%s] Resolution failed: %s", pipeline_name, exc)
        resolution = {"success": False}
 
    # ── Step 3: Restart pipeline ───────────────────────────────────────────
    restart_result = {"restarted": False}
 
    if detection.get("auto_resolvable") or resolution.get("success"):
        logging.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=30,
            )
            restart_result = {
                "restarted": True,
                "new_run_id": new_run_id,
            }
            logging.info(
                "[%s] RESTARTED SUCCESSFULLY | new_run_id=%s",
                pipeline_name, new_run_id,
            )
        except Exception as exc:
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
            restart_result["error"] = str(exc)
    else:
        logging.warning("[%s] Not auto-resolvable — manual intervention required", pipeline_name)
 
    # ── Return response ─────────────────────────────────────────────────────
    result = {
        "pipeline_name": pipeline_name,
        "activity_name": activity_name,
        "error_code": error_code,
        "detection": detection.get("matched_error_code"),
        "confidence": detection.get("confidence", 0),
        "resolved": resolution.get("success"),
        "restart": restart_result,
    }
 
    logging.info("[%s] Complete: %s", pipeline_name, json.dumps(result))
 
    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )
 
 
def _extract_from_monitor_payload(data: dict) -> dict | None:
    """
    Extract pipeline context from Azure Monitor *Log Alert* payload.
    Priority:
      1️⃣ searchResults.tables (real Log Analytics output)
      2️⃣ alertContext.properties (manual tests / legacy alerts)
    """
 
    payload = data.get("data", data)
    alert_ctx = payload.get("alertContext", {})
 
    # ── 1. Log Analytics result rows (REAL ALERT DATA) ──────────────────────
    search = alert_ctx.get("searchResults", {})
    tables = search.get("tables", [])
 
    if tables and tables[0].get("rows"):
        columns = [c["name"] for c in tables[0]["columns"]]
        values  = tables[0]["rows"][0]
        row     = dict(zip(columns, values))
 
        return {
            "pipeline_name": row.get("pipelineName"),
            "run_id":        row.get("runId"),
            "activity_name": row.get("activityName"),
            "error_code":    row.get("errorCode"),
            "error_message": row.get("errorMessage"),
            "source":        "log_analytics",
        }
 
    # ── 2. Fallback: properties (manual tests / non-log alerts) ─────────────
    essentials    = payload.get("essentials", {})
    properties    = alert_ctx.get("properties", {})
 
    if essentials.get("monitorCondition") == "Resolved":
        return None
 
    return {
        "pipeline_name": (
            properties.get("pipelineName")
            or AZURE_CONFIG.adf_pipeline_name
            or "unknown-pipeline"
        ),
        "run_id":        properties.get("runId"),
        "activity_name": properties.get("activityName"),
        "error_code":    properties.get("errorCode"),
        "error_message": (
            properties.get("errorMessage")
            or f"ADF pipeline failure — alert: {essentials.get('alertRule')}"
        ),
        "source": "properties_fallback",
    }
