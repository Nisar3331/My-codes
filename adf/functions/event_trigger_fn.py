"""
=======================================================
  Azure Function — Event Grid Trigger
  Fires automatically when ADF reports a pipeline
  failure via Azure Event Grid / Azure Monitor.

  Setup in Azure Portal:
    ADF → Events → Create Event Subscription
    → Endpoint: this Azure Function
    → Event type: Microsoft.DataFactory.PipelineRunStatusChanged
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


@app.event_grid_trigger(arg_name="event")
def adf_failure_handler(event: func.EventGridEvent) -> None:
    """
    Triggered by ADF pipeline failure events from Event Grid.
    Detects the error via LLM, resolves it, and restarts the pipeline.
    """
    logger.info("Event Grid trigger received: %s", event.event_type)

    try:
        data = event.get_json()
    except Exception as exc:
        logger.error("Failed to parse event payload: %s", exc)
        return

    # ── Filter: only act on Failed status ────────────────────────────────────
    status = data.get("status", "")
    pipeline_name = data.get("pipelineName", AZURE_CONFIG.adf_pipeline_name)
    run_id = data.get("runId", "unknown")

    if status != "Failed":
        logger.info("Ignoring event with status: %s", status)
        return

    # ── Extract error details from event payload ──────────────────────────────
    error_info = data.get("error", {})
    error_message = error_info.get("message", "Unknown ADF error")
    activity_name = data.get("activityName", None)

    logger.error(
        "ADF FAILURE | pipeline=%s | run_id=%s | error=%s",
        pipeline_name, run_id, error_message[:300],
    )

    # ── Step 1: LLM Error Detection ───────────────────────────────────────────
    logger.info("Starting LLM error detection ...")
    detector = LlmErrorDetector()
    detection = detector.detect(
        error_message=error_message,
        pipeline_name=pipeline_name,
        activity_name=activity_name,
        additional_context=f"Run ID: {run_id}",
    )

    error_code = detection.get("matched_error_code", "UNKNOWN")
    confidence = detection.get("confidence", 0)
    resolution_action = detection.get("resolution_action", "")

    logger.info(
        "Detection result | code=%s | confidence=%.0f%% | action=%s",
        error_code, confidence * 100, resolution_action,
    )

    # ── Step 2: Auto-Resolution ───────────────────────────────────────────────
    logger.info("Starting auto-resolution ...")
    resolver = AdfErrorResolver()
    resolution = resolver.resolve(detection)

    resolved = resolution.get("success", False)
    requires_manual = resolution.get("requires_manual_followup", True)

    logger.info(
        "Resolution result | resolved=%s | requires_manual=%s | message=%s",
        resolved, requires_manual, resolution.get("message", "")[:200],
    )

    # ── Step 3: Restart Pipeline ──────────────────────────────────────────────
    if detection.get("auto_resolvable", False) or resolved:
        logger.info("Restarting ADF pipeline ...")
        try:
            mgr = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(delay_seconds=30)
            logger.info("Pipeline restarted successfully | new_run_id=%s", new_run_id)
        except Exception as exc:
            logger.error("Pipeline restart failed: %s", exc)
    else:
        logger.error(
            "Error [%s] is not auto-resolvable. Manual intervention required.",
            error_code,
        )

    # ── Step 4: Log full resolution report ───────────────────────────────────
    report = {
        "original_run_id": run_id,
        "pipeline_name": pipeline_name,
        "error_message": error_message,
        "detection": detection,
        "resolution": resolution,
    }
    logger.info("Full resolution report: %s", json.dumps(report, default=str, indent=2))
