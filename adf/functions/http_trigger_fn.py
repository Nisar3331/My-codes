"""
=======================================================
  Azure Function — HTTP Trigger
  Exposes two REST endpoints:

  POST /api/run-pipeline
       Body: { "mode": "full|local|mock", "retries": 3 }
       → Triggers full pipeline orchestration

  POST /api/detect-error
       Body: { "error_message": "..." }
       → Runs LLM detection + resolution only (no pipeline)

  GET  /api/health
       → Returns function app health status
=======================================================
"""

import json
import logging
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pipeline_orchestrator import PipelineOrchestrator, MODE_FULL, MODE_LOCAL, MODE_MOCK
from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.logger import get_logger

logger = get_logger("http_trigger")
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# ── POST /api/run-pipeline ────────────────────────────────────────────────────

@app.route(route="run-pipeline", methods=["POST"])
def run_pipeline_http(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manually trigger a full pipeline orchestration run.

    Request body (JSON):
        {
            "mode": "full",     # full | local | mock
            "retries": 3        # max retry attempts
        }
    """
    logger.info("HTTP trigger: POST /api/run-pipeline")

    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be valid JSON", 400)

    mode = body.get("mode", MODE_FULL)
    retries = int(body.get("retries", 3))

    if mode not in (MODE_FULL, MODE_LOCAL, MODE_MOCK):
        return _error_response(
            f"Invalid mode '{mode}'. Must be one of: full, local, mock", 400
        )

    logger.info("Starting pipeline | mode=%s | retries=%d", mode, retries)

    try:
        orchestrator = PipelineOrchestrator(mode=mode, max_retries=retries)
        report = orchestrator.run()

        status_code = 200 if report.get("final_status") == "Succeeded" else 500
        return func.HttpResponse(
            json.dumps(report, default=str, indent=2),
            mimetype="application/json",
            status_code=status_code,
        )

    except Exception as exc:
        logger.exception("Pipeline run failed with exception")
        return _error_response(str(exc), 500)


# ── POST /api/detect-error ────────────────────────────────────────────────────

@app.route(route="detect-error", methods=["POST"])
def detect_error_http(req: func.HttpRequest) -> func.HttpResponse:
    """
    Run LLM error detection + auto-resolution on a single error message.
    Does NOT restart the pipeline — useful for testing and debugging.

    Request body (JSON):
        {
            "error_message": "Column mapping failed...",
            "pipeline_name": "pl_csv_to_parquet",   (optional)
            "activity_name": "Copy_Activity_1"       (optional)
        }
    """
    logger.info("HTTP trigger: POST /api/detect-error")

    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Request body must be valid JSON", 400)

    error_message = body.get("error_message", "").strip()
    if not error_message:
        return _error_response("Field 'error_message' is required", 400)

    pipeline_name = body.get("pipeline_name")
    activity_name = body.get("activity_name")

    try:
        detector = LlmErrorDetector()
        detection = detector.detect(
            error_message=error_message,
            pipeline_name=pipeline_name,
            activity_name=activity_name,
        )

        resolver = AdfErrorResolver()
        resolution = resolver.resolve(detection)

        result = {
            "input": {
                "error_message": error_message,
                "pipeline_name": pipeline_name,
                "activity_name": activity_name,
            },
            "detection": detection,
            "resolution": resolution,
        }

        logger.info(
            "Detection complete | code=%s | confidence=%.0f%%",
            detection.get("matched_error_code"),
            detection.get("confidence", 0) * 100,
        )

        return func.HttpResponse(
            json.dumps(result, default=str, indent=2),
            mimetype="application/json",
            status_code=200,
        )

    except Exception as exc:
        logger.exception("Detection failed with exception")
        return _error_response(str(exc), 500)


# ── GET /api/health ───────────────────────────────────────────────────────────

@app.route(route="health", methods=["GET"])
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Simple health check endpoint for Azure monitoring."""
    return func.HttpResponse(
        json.dumps({"status": "healthy", "service": "adf-llm-datarend"}),
        mimetype="application/json",
        status_code=200,
    )


# ── Helper ────────────────────────────────────────────────────────────────────

def _error_response(message: str, status_code: int) -> func.HttpResponse:
    logger.error("HTTP error %d: %s", status_code, message)
    return func.HttpResponse(
        json.dumps({"error": message}),
        mimetype="application/json",
        status_code=status_code,
    )
