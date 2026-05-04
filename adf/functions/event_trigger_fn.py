"""
=======================================================
  EventGridTrigger1 — ADF Self-Healing Function
  Azure Functions v1 style (folder based)

  This function fires when any ADF pipeline fails
  via Event Grid subscription.
  
  Reads pipeline name from event payload dynamically
  so it works for pipeline3, pipeline22, or any pipeline.
=======================================================
"""

import json
import logging
import os
import sys

# Add parent folder to path so we can import src/ and config/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import azure.functions as func

from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.adf_pipeline_manager import AdfPipelineManager
from config.settings import AZURE_CONFIG


def main(event: func.EventGridEvent) -> None:
    """
    Main entry point for EventGridTrigger1.
    Fires on ADF PipelineRunStatusChanged events.
    """
    logging.info("=== EventGridTrigger1 fired ===")
    logging.info("Event type: %s", event.event_type)

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        data = event.get_json()
    except Exception as exc:
        logging.error("Failed to parse event payload: %s", exc)
        return

    logging.info("Payload: %s", json.dumps(data, default=str)[:500])

    # ── Only act on Failed status ─────────────────────────────────────────────
    status = data.get("status", "")
    if status != "Failed":
        logging.info("Ignoring event with status: %s", status)
        return

    # ── Extract pipeline name from payload ────────────────────────────────────
    pipeline_name = (
        data.get("pipelineName") or
        data.get("payload", {}).get("pipelineName") or
        data.get("properties", {}).get("pipelineName") or
        AZURE_CONFIG.adf_pipeline_name or
        "unknown-pipeline"
    )

    run_id       = data.get("runId", "unknown")
    factory_name = data.get("factoryName", AZURE_CONFIG.adf_name)
    error_obj    = data.get("error", {})
    error_message = (
        error_obj.get("message") or
        error_obj.get("errorCode") or
        "Unknown ADF error"
    )

    logging.error(
        "ADF FAILURE | pipeline=%s | factory=%s | run_id=%s | error=%s",
        pipeline_name, factory_name, run_id, error_message[:300],
    )

    # ── Step 1: LLM Error Detection ───────────────────────────────────────────
    logging.info("[%s] Running LLM error detection ...", pipeline_name)
    try:
        detector  = LlmErrorDetector()
        detection = detector.detect(
            error_message=error_message,
            pipeline_name=pipeline_name,
            activity_name=data.get("activityName"),
            additional_context=f"Run ID: {run_id} | Factory: {factory_name}",
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
    logging.info("[%s] Running auto-resolution ...", pipeline_name)
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
    if detection.get("auto_resolvable", False) or resolution.get("success", False):
        logging.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr        = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=30,
            )
            logging.info("[%s] Restarted | new_run_id=%s", pipeline_name, new_run_id)
        except Exception as exc:
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        logging.error(
            "[%s] Not auto-resolvable — manual intervention required",
            pipeline_name,
        )

    # ── Step 4: Log full report ───────────────────────────────────────────────
    logging.info(
        "[%s] Complete | detection=%s | resolution=%s",
        pipeline_name,
        detection.get("matched_error_code"),
        resolution.get("success"),
    )
