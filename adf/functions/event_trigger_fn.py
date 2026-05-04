"""
=======================================================
  HttpTrigger1 — Azure Monitor Alert Handler
  
  Called by: Azure Monitor → Alert Rule → Action Group
             → Azure Function (this file)
  
  When ADF pipeline fails:
    1. Monitor detects failure
    2. Alert fires → Action Group calls this HTTP endpoint
    3. This code runs LLM detection
    4. Auto-resolves the error
    5. Restarts the failed pipeline
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
    """
    HTTP entry point called by Azure Monitor Action Group.
    Azure Monitor sends a POST request with the alert payload.
    """
    logging.info("=== HttpTrigger1 — Monitor Alert received ===")

    # ── Parse the request body ────────────────────────────────────────────────
    try:
        body = req.get_json()
    except Exception as exc:
        logging.error("Failed to parse request body: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    logging.info("Alert payload: %s", json.dumps(body, default=str)[:500])

    # ── Extract pipeline info from Monitor alert payload ──────────────────────
    pipeline_info = _extract_from_monitor_payload(body)

    if not pipeline_info:
        logging.info("Could not extract pipeline info — ignoring alert")
        return func.HttpResponse(
            json.dumps({"status": "ignored", "reason": "no pipeline info found"}),
            status_code=200,
            mimetype="application/json",
        )

    pipeline_name = pipeline_info["pipeline_name"]
    error_message = pipeline_info["error_message"]
    run_id        = pipeline_info.get("run_id", "unknown")

    logging.error(
        "ADF FAILURE | pipeline=%s | run_id=%s | error=%s",
        pipeline_name, run_id, error_message[:200],
    )

    # ── Step 1: LLM Error Detection ───────────────────────────────────────────
    logging.info("[%s] Running LLM detection ...", pipeline_name)
    try:
        detector  = LlmErrorDetector()
        detection = detector.detect(
            error_message=error_message,
            pipeline_name=pipeline_name,
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

    # ── Step 2: Auto-Resolution ───────────────────────────────────────────────
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

    # ── Step 3: Restart pipeline ──────────────────────────────────────────────
    restart_result = {}
    if detection.get("auto_resolvable", False) or resolution.get("success", False):
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
        except Exception as exc:
            restart_result = {"restarted": False, "error": str(exc)}
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        restart_result = {"restarted": False, "reason": "not_auto_resolvable"}
        logging.error(
            "[%s] Not auto-resolvable — manual intervention required",
            pipeline_name,
        )

    # ── Return result ─────────────────────────────────────────────────────────
    result = {
        "pipeline_name": pipeline_name,
        "detection":     detection.get("matched_error_code"),
        "confidence":    detection.get("confidence", 0),
        "resolved":      resolution.get("success"),
        "restart":       restart_result,
    }

    logging.info("[%s] Complete: %s", pipeline_name, json.dumps(result))

    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )


def _extract_from_monitor_payload(data: dict) -> dict | None:
    """
    Extract pipeline name and error from Azure Monitor alert payload.
    
    Azure Monitor Common Alert Schema:
    {
      "schemaId": "azureMonitorCommonAlertSchema",
      "data": {
        "essentials": {
          "alertRule": "adf-pipeline-failure-alert",
          "monitorCondition": "Fired",
          "affectedConfigurationItems": ["pf-observability-datafactory"]
        },
        "alertContext": {
          "properties": {
            "pipelineName": "pipeline22",
            "runId": "abc-123",
            "errorMessage": "Copy activity failed..."
          }
        }
      }
    }
    """
    # Handle wrapped {"data": {...}} format
    payload = data.get("data", data)

    essentials    = payload.get("essentials", {})
    alert_context = payload.get("alertContext", {})
    properties    = alert_context.get("properties", {})

    # Skip resolved alerts
    if essentials.get("monitorCondition") == "Resolved":
        logging.info("Alert resolved — ignoring")
        return None

    # Get pipeline name from properties or use default
    pipeline_name = (
        properties.get("pipelineName") or
        properties.get("PipelineName") or
        properties.get("pipeline_name") or
        AZURE_CONFIG.adf_pipeline_name or
        "unknown-pipeline"
    )

    # Get error message
    error_message = (
        properties.get("errorMessage") or
        properties.get("message") or
        properties.get("ErrorMessage") or
        f"ADF pipeline failure — alert: {essentials.get('alertRule', 'unknown')}"
    )

    return {
        "pipeline_name": pipeline_name,
        "error_message": error_message,
        "run_id":        properties.get("runId", "unknown"),
        "alert_rule":    essentials.get("alertRule", ""),
        "source":        "azure_monitor",
    }
