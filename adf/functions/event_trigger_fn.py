"""
=======================================================
  Azure Function — Event Grid Trigger
  DYNAMIC VERSION

  Key fix vs static version:
  ─────────────────────────
  - Pipeline name is read from the Event Grid payload
  - Works for ANY pipeline that fails (pipeline3,
    pipeline22, or any future pipeline)
  - Does NOT rely on ADF_PIPELINE_NAME env variable
    for deciding which pipeline to restart
  - One function heals ALL pipelines automatically

  Event payload from ADF looks like:
  {
    "pipelineName": "pipeline3",
    "runId": "abc-123",
    "status": "Failed",
    "error": { "message": "ParquetInvalidColumnName..." }
  }
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

logger = get_logger("event_trigger")
app = func.FunctionApp()


@app.event_grid_trigger(arg_name="event")
def adf_failure_handler(event: func.EventGridEvent) -> None:
    """
    Triggered by ANY ADF pipeline failure via Event Grid.

    Reads the pipeline name directly from the event payload
    so it works for pipeline3, pipeline22, or any pipeline
    in the data factory — fully dynamic, no hardcoding.
    """
    logger.info("Event Grid trigger received: %s", event.event_type)

    # ── Parse event payload ───────────────────────────────────────────────────
    try:
        data = event.get_json()
    except Exception as exc:
        logger.error("Failed to parse event payload: %s", exc)
        return

    # ── Read status from payload ──────────────────────────────────────────────
    status = data.get("status", "")
    if status != "Failed":
        logger.info("Ignoring event with status: %s (only acting on Failed)", status)
        return

    # ── READ PIPELINE NAME DYNAMICALLY FROM PAYLOAD ───────────────────────────
    # This is the key fix — pipeline name comes from the event, not from .env
    pipeline_name   = data.get("pipelineName", "")
    run_id          = data.get("runId", "unknown")
    factory_name    = data.get("factoryName", "")
    activity_name   = data.get("activityName", None)

    # Fallback: try nested paths ADF sometimes uses
    if not pipeline_name:
        pipeline_name = (
            data.get("payload", {}).get("pipelineName", "") or
            data.get("resource", {}).get("pipelineName", "unknown-pipeline")
        )

    if not pipeline_name:
        logger.error("Could not extract pipeline name from event payload: %s",
                     json.dumps(data, default=str)[:500])
        return

    # ── Extract error details ─────────────────────────────────────────────────
    error_info    = data.get("error", {})
    error_message = (
        error_info.get("message", "") or
        error_info.get("errorCode", "") or
        "Unknown ADF error"
    )

    logger.error(
        "ADF FAILURE | factory=%s | pipeline=%s | run_id=%s | error=%s",
        factory_name, pipeline_name, run_id, error_message[:300],
    )

    # ── Step 1: LLM Error Detection ───────────────────────────────────────────
    logger.info("[%s] Starting LLM error detection ...", pipeline_name)
    detector  = LlmErrorDetector()
    detection = detector.detect(
        error_message=error_message,
        pipeline_name=pipeline_name,        # ← actual failing pipeline name
        activity_name=activity_name,
        additional_context=(
            f"Run ID: {run_id} | "
            f"Factory: {factory_name} | "
            f"Full payload: {json.dumps(data, default=str)[:500]}"
        ),
    )

    error_code        = detection.get("matched_error_code", "UNKNOWN")
    confidence        = detection.get("confidence", 0)
    resolution_action = detection.get("resolution_action", "")

    logger.info(
        "[%s] Detection | code=%s | confidence=%.0f%% | action=%s",
        pipeline_name, error_code, confidence * 100, resolution_action,
    )

    # ── Step 2: Auto-Resolution ───────────────────────────────────────────────
    logger.info("[%s] Starting auto-resolution ...", pipeline_name)
    resolver   = AdfErrorResolver()
    resolution = resolver.resolve(detection)

    resolved         = resolution.get("success", False)
    requires_manual  = resolution.get("requires_manual_followup", True)

    logger.info(
        "[%s] Resolution | resolved=%s | requires_manual=%s | message=%s",
        pipeline_name, resolved, requires_manual,
        resolution.get("message", "")[:200],
    )

    # ── Step 3: Restart the EXACT pipeline that failed ────────────────────────
    if detection.get("auto_resolvable", False) or resolved:
        logger.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr = AdfPipelineManager()

            # ── DYNAMIC restart — uses the pipeline name from the event ───────
            # NOT the static env variable
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,    # ← exact pipeline that failed
                delay_seconds=30,
            )
            logger.info(
                "[%s] Restarted successfully | new_run_id=%s",
                pipeline_name, new_run_id,
            )
        except Exception as exc:
            logger.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        logger.error(
            "[%s] Error [%s] is not auto-resolvable — manual intervention required.",
            pipeline_name, error_code,
        )

    # ── Step 4: Log full report ───────────────────────────────────────────────
    report = {
        "original_run_id":  run_id,
        "pipeline_name":    pipeline_name,
        "factory_name":     factory_name,
        "error_message":    error_message,
        "detection":        detection,
        "resolution":       resolution,
    }
    logger.info(
        "[%s] Full resolution report: %s",
        pipeline_name,
        json.dumps(report, default=str, indent=2),
    )
