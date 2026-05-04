"""
=======================================================
  EventGridTrigger1 — ADF Self-Healing Function
  Handles Azure Monitor Alert payload
  
  Trigger: Azure Monitor Alert Rule → Action Group
           → Azure Function (this file)
  
  When ADF pipeline fails:
    Monitor detects it → fires alert → calls this function
    → LLM detects error → resolver fixes → pipeline restarts
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


def main(event: func.EventGridEvent) -> None:
    """
    Main entry point — handles both:
    1. Azure Monitor Alert payload
    2. ADF Event Grid payload (if Event Grid ever becomes available)
    """
    logging.info("=== EventGridTrigger1 fired ===")

    try:
        data = event.get_json()
    except Exception as exc:
        logging.error("Failed to parse payload: %s", exc)
        return

    logging.info("Raw payload: %s", json.dumps(data, default=str)[:500])

    # ── Detect payload type and extract pipeline info ─────────────────────────
    pipeline_info = _extract_pipeline_info(data)

    if not pipeline_info:
        logging.info("Could not extract pipeline failure info — ignoring event")
        return

    pipeline_name = pipeline_info["pipeline_name"]
    error_message = pipeline_info["error_message"]
    run_id        = pipeline_info.get("run_id", "unknown")

    logging.error(
        "ADF FAILURE DETECTED | pipeline=%s | run_id=%s | error=%s",
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
    if detection.get("auto_resolvable", False) or resolution.get("success", False):
        logging.info("[%s] Restarting pipeline ...", pipeline_name)
        try:
            mgr        = AdfPipelineManager()
            new_run_id = mgr.restart_pipeline(
                pipeline_name=pipeline_name,
                delay_seconds=30,
            )
            logging.info(
                "[%s] RESTARTED SUCCESSFULLY | new_run_id=%s",
                pipeline_name, new_run_id,
            )
        except Exception as exc:
            logging.error("[%s] Restart failed: %s", pipeline_name, exc)
    else:
        logging.error(
            "[%s] Not auto-resolvable — manual intervention required",
            pipeline_name,
        )


def _extract_pipeline_info(data: dict) -> dict | None:
    """
    Auto-detects payload format and extracts pipeline info.
    Handles 3 formats:
      1. Azure Monitor Common Alert Schema
      2. ADF Event Grid payload
      3. Direct ADF Monitor payload
    """

    # ── Format 1: Azure Monitor Common Alert Schema ───────────────────────────
    # Sent when Azure Monitor Alert Rule fires via Action Group
    if "data" in data and "essentials" in data.get("data", {}):
        essentials    = data["data"]["essentials"]
        alert_context = data["data"].get("alertContext", {})
        properties    = alert_context.get("properties", {})

        # Skip if alert is resolved (not fired)
        if essentials.get("monitorCondition") == "Resolved":
            logging.info("Monitor alert resolved — ignoring")
            return None

        pipeline_name = (
            properties.get("pipelineName") or
            properties.get("PipelineName") or
            AZURE_CONFIG.adf_pipeline_name or
            "unknown-pipeline"
        )
        error_message = (
            properties.get("errorMessage") or
            properties.get("message") or
            f"Pipeline failure detected by Azure Monitor alert: {essentials.get('alertRule', '')}"
        )
        return {
            "pipeline_name": pipeline_name,
            "error_message": error_message,
            "run_id":        properties.get("runId", "unknown"),
            "source":        "azure_monitor",
        }

    # ── Format 2: ADF Event Grid payload ─────────────────────────────────────
    # Sent when ADF Event Grid subscription fires
    if "pipelineName" in data or "status" in data:
        status = data.get("status", "")
        if status and status != "Failed":
            logging.info("ADF event status=%s — ignoring", status)
            return None

        pipeline_name = (
            data.get("pipelineName") or
            AZURE_CONFIG.adf_pipeline_name or
            "unknown-pipeline"
        )
        error_obj     = data.get("error", {})
        error_message = (
            error_obj.get("message") or
            error_obj.get("errorCode") or
            "ADF pipeline failed"
        )
        return {
            "pipeline_name": pipeline_name,
            "error_message": error_message,
            "run_id":        data.get("runId", "unknown"),
            "source":        "event_grid",
        }

    # ── Format 3: Direct Monitor payload ─────────────────────────────────────
    # Some alert rules send simpler payloads
    if "context" in data:
        context = data.get("context", {})
        return {
            "pipeline_name": (
                context.get("resourceName") or
                AZURE_CONFIG.adf_pipeline_name or
                "unknown-pipeline"
            ),
            "error_message": f"Pipeline failure from Monitor context: {json.dumps(context)[:200]}",
            "run_id":        "unknown",
            "source":        "monitor_direct",
        }

    logging.warning("Unknown payload format — logging for analysis: %s",
                    json.dumps(data, default=str)[:300])
    return None
