"""
=======================================================
  Azure Function — Dual Trigger Handler
  Handles BOTH trigger sources:

  SOURCE 1 — ADF Event Grid (recommended):
    ADF → Events → Event Subscription → this function
    Payload has pipelineName, runId, status, error directly

  SOURCE 2 — Azure Monitor Alert Rule:
    Azure Monitor → Alert Rule → Action Group
    → Webhook/Azure Function → this function
    Payload is wrapped in Azure Monitor alert schema

  The function auto-detects which source fired it
  and extracts pipeline name + error from either format.

  IMPORTANT — Why Alert Rule was not triggering:
  ─────────────────────────────────────────────
  Azure Monitor Alert Rules require an Action Group
  with an Azure Function action type to reach this
  function. Alert rules alone do not call functions.
  See WHAT_TO_DO.txt Section 5 for setup steps.
=======================================================
"""

import json
import logging
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.adf_pipeline_manager import AdfPipelineManager
from src.logger import get_logger
from config.settings import AZURE_CONFIG

logger = get_logger("event_trigger")
app = func.FunctionApp()


# =============================================================================
#  TRIGGER 1 — ADF Event Grid (recommended path)
#  Set up: ADF → Events → + Event Subscription → endpoint = this function
# =============================================================================

@app.event_grid_trigger(arg_name="event")
def adf_failure_handler(event: func.EventGridEvent) -> None:
    """
    Fires when ADF sends a PipelineRunStatusChanged event via Event Grid.
    This is the recommended trigger — most reliable, lowest latency.
    """
    logger.info("=== Event Grid trigger received: %s ===", event.event_type)

    try:
        data = event.get_json()
    except Exception as exc:
        logger.error("Failed to parse Event Grid payload: %s", exc)
        return

    logger.debug("Raw Event Grid payload: %s", json.dumps(data, default=str)[:1000])

    # ── Extract from Event Grid ADF payload ───────────────────────────────────
    pipeline_info = _extract_from_event_grid_payload(data)

    if not pipeline_info:
        logger.error(
            "Could not extract pipeline info from Event Grid payload. "
            "Full payload: %s", json.dumps(data, default=str)[:500]
        )
        return

    _run_detection_and_heal(pipeline_info)


# =============================================================================
#  TRIGGER 2 — Azure Monitor Alert Rule via HTTP Webhook
#  Set up: Monitor → Action Group → Azure Function action → this function
#          OR Action Group → Webhook → POST to /api/monitor-alert
# =============================================================================

@app.route(route="monitor-alert", methods=["POST"])
def monitor_alert_handler(req: func.HttpRequest) -> func.HttpResponse:
    """
    Fires when Azure Monitor Alert Rule triggers via Action Group.
    Sri Harini's setup: Alert rules in Monitor → this endpoint.

    Action Group setup in Azure Portal:
      Monitor → Action Groups → + Create
        Actions tab → Add action
        Action type: Azure Function  OR  Webhook
        If Webhook: URL = https://<func>.azurewebsites.net/api/monitor-alert
    """
    logger.info("=== Azure Monitor Alert trigger received ===")

    try:
        body = req.get_json()
    except Exception as exc:
        logger.error("Failed to parse Monitor alert payload: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON payload"}),
            status_code=400,
            mimetype="application/json",
        )

    logger.debug("Raw Monitor alert payload: %s", json.dumps(body, default=str)[:1000])

    # ── Extract from Azure Monitor Alert payload ──────────────────────────────
    pipeline_info = _extract_from_monitor_payload(body)

    if not pipeline_info:
        logger.error(
            "Could not extract pipeline info from Monitor alert payload. "
            "Full payload: %s", json.dumps(body, default=str)[:500]
        )
        return func.HttpResponse(
            json.dumps({"error": "Could not extract pipeline info"}),
            status_code=422,
            mimetype="application/json",
        )

    result = _run_detection_and_heal(pipeline_info)

    return func.HttpResponse(
        json.dumps(result, default=str),
        status_code=200,
        mimetype="application/json",
    )


# =============================================================================
#  Payload Parsers — one per trigger source
# =============================================================================

def _extract_from_event_grid_payload(data: dict) -> dict | None:
    """
    Parse ADF Event Grid payload.

    ADF sends this structure:
    {
      "pipelineName": "pipeline3",
      "runId": "abc-123-...",
      "factoryName": "pf-observability-datafactory",
      "groupId": "...",
      "status": "Failed",
      "activityName": "Copy data1",
      "error": {
        "errorCode": "ParquetInvalidColumnName",
        "message": "The column name is invalid...",
        "failureType": "UserError",
        "target": "Copy data1"
      }
    }
    """
    # ── Status check ──────────────────────────────────────────────────────────
    status = data.get("status", "")
    if status != "Failed":
        logger.info("Event Grid: ignoring status=%s (only handling Failed)", status)
        return None

    # ── Pipeline name — try multiple paths ADF uses ───────────────────────────
    pipeline_name = (
        data.get("pipelineName") or
        data.get("payload", {}).get("pipelineName") or
        data.get("properties", {}).get("pipelineName") or
        ""
    )

    if not pipeline_name:
        logger.warning("Event Grid payload missing pipelineName — using factory default")
        pipeline_name = AZURE_CONFIG.adf_pipeline_name or "unknown-pipeline"

    # ── Error details ─────────────────────────────────────────────────────────
    error_obj    = data.get("error", {})
    error_message = (
        error_obj.get("message") or
        error_obj.get("errorCode") or
        data.get("message") or
        "Unknown ADF error from Event Grid"
    )

    return {
        "pipeline_name":  pipeline_name,
        "run_id":         data.get("runId", "unknown"),
        "factory_name":   data.get("factoryName", AZURE_CONFIG.adf_name),
        "activity_name":  data.get("activityName") or error_obj.get("target"),
        "error_message":  error_message,
        "error_code":     error_obj.get("errorCode", ""),
        "source":         "event_grid",
    }


def _extract_from_monitor_payload(data: dict) -> dict | None:
    """
    Parse Azure Monitor Common Alert Schema payload.

    Azure Monitor sends this structure:
    {
      "schemaId": "azureMonitorCommonAlertSchema",
      "data": {
        "essentials": {
          "alertId": "...",
          "alertRule": "adf-pipeline-failure-alert",
          "severity": "Sev2",
          "signalType": "Metric",
          "monitorCondition": "Fired",
          "monitoringService": "Platform",
          "affectedConfigurationItems": ["pf-observability-datafactory"],
          "firedDateTime": "2026-05-04T13:37:00Z"
        },
        "alertContext": {
          "properties": {
            "pipelineName": "pipeline3",
            "runId": "abc-123",
            "errorMessage": "Column name is invalid..."
          }
        }
      }
    }
    """
    # Support both wrapped {"data": {...}} and unwrapped formats
    payload = data.get("data", data)

    essentials    = payload.get("essentials", {})
    alert_context = payload.get("alertContext", {})
    properties    = alert_context.get("properties", {})

    # ── Monitor condition check ───────────────────────────────────────────────
    condition = essentials.get("monitorCondition", "")
    if condition == "Resolved":
        logger.info("Monitor alert: condition=Resolved — ignoring")
        return None

    # ── Pipeline name — Azure Monitor puts it in alertContext.properties ───────
    pipeline_name = (
        properties.get("pipelineName") or
        properties.get("pipeline_name") or
        properties.get("PipelineName") or
        ""
    )

    # Fallback: try to extract from alert rule name
    if not pipeline_name:
        alert_rule = essentials.get("alertRule", "")
        logger.warning(
            "Monitor payload missing pipelineName in properties. "
            "Alert rule: %s — using factory default. "
            "To fix: add Custom Properties to your Alert Rule with key=pipelineName",
            alert_rule,
        )
        pipeline_name = AZURE_CONFIG.adf_pipeline_name or "unknown-pipeline"

    # ── Error message — Azure Monitor puts it in alertContext.properties ───────
    error_message = (
        properties.get("errorMessage") or
        properties.get("error_message") or
        properties.get("ErrorMessage") or
        alert_context.get("condition", {}).get("allOf", [{}])[0].get("metricName", "") or
        "ADF pipeline failure detected via Azure Monitor alert"
    )

    return {
        "pipeline_name": pipeline_name,
        "run_id":        properties.get("runId", essentials.get("alertId", "unknown")),
        "factory_name":  (
            essentials.get("affectedConfigurationItems", [AZURE_CONFIG.adf_name])[0]
        ),
        "activity_name": properties.get("activityName"),
        "error_message": error_message,
        "error_code":    properties.get("errorCode", ""),
        "source":        "azure_monitor",
        "alert_rule":    essentials.get("alertRule", ""),
        "fired_at":      essentials.get("firedDateTime", ""),
    }


# =============================================================================
#  Core logic — shared by both trigger paths
# =============================================================================

def _run_detection_and_heal(pipeline_info: dict) -> dict:
    """
    Shared logic called by both Event Grid and Monitor triggers.
    Runs: LLM detection → resolution → pipeline restart.
    """
    pipeline_name = pipeline_info["pipeline_name"]
    error_message = pipeline_info["error_message"]
    run_id        = pipeline_info["run_id"]
    factory_name  = pipeline_info["factory_name"]
    source        = pipeline_info.get("source", "unknown")

    logger.error(
        "ADF FAILURE | source=%s | factory=%s | pipeline=%s | run_id=%s | error=%s",
        source, factory_name, pipeline_name, run_id, error_message[:300],
    )

    # ── Step 1: LLM Error Detection ───────────────────────────────────────────
    logger.info("[%s] Starting LLM error detection ...", pipeline_name)
    detector  = LlmErrorDetector()
    detection = detector.detect(
        error_message=error_message,
        pipeline_name=pipeline_name,
        activity_name=pipeline_info.get("activity_name"),
        additional_context=(
            f"Run ID: {run_id} | "
            f"Factory: {factory_name} | "
            f"Error code: {pipeline_info.get('error_code', '')} | "
            f"Trigger source: {source}"
        ),
    )

    error_code        = detection.get("matched_error_code", "UNKNOWN")
    confidence        = detection.get("confidence", 0)
    resolution_action = detection.get("resolution_action", "")

    logger.info(
        "[%s] Detection complete | code=%s | confidence=%.0f%% | action=%s",
        pipeline_name, error_code, confidence * 100, resolution_action,
    )

    # ── Step 2: Auto-Resolution ───────────────────────────────────────────────
    logger.info("[%s] Running auto-resolution ...", pipeline_name)
    resolver   = AdfErrorResolver()
    resolution = resolver.resolve(detection)

    resolved        = resolution.get("success", False)
    requires_manual = resolution.get("requires_manual_followup", True)

    logger.info(
        "[%s] Resolution | resolved=%s | requires_manual=%s | message=%s",
        pipeline_name, resolved, requires_manual,
        resolution.get("message", "")[:200],
    )

    # ── Step 3: Restart the exact pipeline that failed ────────────────────────
    restart_result = {}
    if detection.get("auto_resolvable", False) or resolved:
        logger.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr        = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=30,
            )
            restart_result = {"restarted": True, "new_run_id": new_run_id}
            logger.info(
                "[%s] Restarted successfully | new_run_id=%s",
                pipeline_name, new_run_id,
            )
        except Exception as exc:
            restart_result = {"restarted": False, "error": str(exc)}
            logger.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        restart_result = {"restarted": False, "reason": "not_auto_resolvable"}
        logger.error(
            "[%s] [%s] is not auto-resolvable — manual intervention required.",
            pipeline_name, error_code,
        )

    # ── Step 4: Full report ───────────────────────────────────────────────────
    report = {
        "pipeline_name":  pipeline_name,
        "factory_name":   factory_name,
        "original_run_id": run_id,
        "trigger_source": source,
        "error_message":  error_message,
        "detection":      detection,
        "resolution":     resolution,
        "restart":        restart_result,
    }

    logger.info(
        "[%s] Full report: %s",
        pipeline_name,
        json.dumps(report, default=str, indent=2),
    )

    return report
