"""
=======================================================
  Pipeline Orchestrator — DYNAMIC VERSION

  Key fix vs static version:
  ─────────────────────────
  - Accepts pipeline_name as a constructor parameter
  - Passes it through to AdfPipelineManager
  - Can be instantiated for any pipeline name at runtime
  - When called from Event Grid trigger, the failing
    pipeline's name is passed in — not the env variable
=======================================================
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from src.logger import get_logger
from src.data_processor import CsvToParquetProcessor
from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver
from src.adf_pipeline_manager import AdfPipelineManager
from config.settings import AZURE_CONFIG, PIPELINE_CONFIG

logger = get_logger(__name__)

MODE_FULL  = "full"
MODE_LOCAL = "local"
MODE_MOCK  = "mock"


class PipelineOrchestrator:
    """
    Orchestrates the full ADF ETL pipeline with LLM-powered
    auto-error-detection and self-healing retry logic.

    Dynamic: the pipeline_name parameter overrides the env variable
    so this orchestrator can be pointed at any ADF pipeline at runtime.
    """

    def __init__(
        self,
        mode: str = MODE_FULL,
        max_retries: Optional[int] = None,
        pipeline_name: Optional[str] = None,        # ← NEW: dynamic pipeline name
        pipeline_parameters: Optional[Dict[str, Any]] = None,
    ):
        self._mode            = mode
        self._max_retries     = max_retries or PIPELINE_CONFIG.max_retry_attempts
        self._pipeline_params = pipeline_parameters or {}
        self._run_history: list = []

        # ── Dynamic pipeline name ─────────────────────────────────────────────
        # If provided → use it (e.g. "pipeline3" from Event Grid payload)
        # If not      → fall back to ADF_PIPELINE_NAME env variable
        self._pipeline_name = pipeline_name or AZURE_CONFIG.adf_pipeline_name

        logger.info(
            "Orchestrator initialised | mode=%s | pipeline=%s | retries=%d",
            mode, self._pipeline_name, self._max_retries,
        )

        self._processor = CsvToParquetProcessor()
        self._detector  = LlmErrorDetector() if mode != MODE_MOCK else None
        self._resolver  = AdfErrorResolver()
        self._adf       = AdfPipelineManager() if mode == MODE_FULL else None

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Execute the full orchestrated pipeline run."""
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        logger.info("=" * 60)
        logger.info("  ADF LLM PIPELINE ORCHESTRATOR")
        logger.info("  Session  : %s", session_id)
        logger.info("  Pipeline : %s", self._pipeline_name)   # ← shows actual name
        logger.info("  Mode     : %s", self._mode)
        logger.info("  Retries  : %d", self._max_retries)
        logger.info("=" * 60)

        ingest_result = self._run_ingestion()
        if ingest_result.get("files_failed", 0) > 0:
            logger.warning("⚠️  Some files failed ingestion — pipeline will proceed")

        final_run = self._run_with_auto_heal()
        report    = self._build_report(session_id, ingest_result, final_run)
        self._save_report(report, session_id)
        self._print_summary(report)
        return report

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def _run_ingestion(self) -> Dict[str, Any]:
        logger.info("\n📦 STEP 1: CSV → Parquet Ingestion")
        if self._mode == MODE_FULL:
            return self._processor.run()
        elif self._mode == MODE_LOCAL:
            return self._processor.run_local()
        else:
            return {"files_processed": 2, "files_failed": 0, "details": []}

    # ── Pipeline run + auto-heal loop ─────────────────────────────────────────

    def _run_with_auto_heal(self) -> Dict[str, Any]:
        attempt       = 0
        last_run_result = {}

        while attempt <= self._max_retries:
            label = f"Attempt {attempt + 1}/{self._max_retries + 1}"
            logger.info("\n🔄 PIPELINE RUN [%s] — %s", self._pipeline_name, label)

            run_result = self._trigger_and_monitor(attempt)
            self._run_history.append(run_result)
            last_run_result = run_result

            if run_result["status"] == "Succeeded":
                logger.info("🎉 [%s] succeeded on %s", self._pipeline_name, label)
                return run_result

            if run_result["status"] in {"MonitorTimeout", "Cancelled"}:
                logger.error("🛑 Non-retryable status: %s", run_result["status"])
                return run_result

            error_message = run_result.get("error_message", "Unknown ADF error")

            if attempt >= self._max_retries:
                logger.error("❌ Max retries (%d) reached for [%s]",
                             self._max_retries, self._pipeline_name)
                run_result["final_status"] = "ExhaustedRetries"
                return run_result

            # ── LLM detection ─────────────────────────────────────────────────
            logger.info("\n🤖 STEP 4: LLM Error Detection")
            detection = self._detect_error(error_message, run_result)
            run_result["detection"] = detection

            logger.info(
                "   Matched: [%s] %s (confidence=%.2f)",
                detection.get("matched_error_code"),
                detection.get("matched_error_name"),
                detection.get("confidence", 0),
            )

            # ── Auto-resolve ──────────────────────────────────────────────────
            logger.info("\n🔧 STEP 5: Auto-Resolution")
            resolution = self._resolver.resolve(detection)
            run_result["resolution"] = resolution

            if not resolution.get("success") and not detection.get("auto_resolvable"):
                logger.error("❌ Not auto-resolvable. Manual intervention required.")
                run_result["final_status"] = "RequiresManualIntervention"
                return run_result

            delay = self._get_retry_delay(detection.get("matched_error_code", ""), attempt)
            logger.info("\n♻️  STEP 6: Restart [%s] — waiting %ds ...",
                        self._pipeline_name, delay)
            attempt += 1

        return last_run_result

    def _trigger_and_monitor(self, attempt: int) -> Dict[str, Any]:
        if self._mode == MODE_FULL:
            # ── Pass dynamic pipeline name to manager ─────────────────────────
            run_id = self._adf.trigger_pipeline(
                pipeline_name=self._pipeline_name,      # ← DYNAMIC
                parameters=self._pipeline_params,
            )
            result             = self._adf.wait_for_completion(run_id)
            result["monitor_url"] = self._adf.get_pipeline_run_url(run_id)
            return result
        else:
            return self._simulate_pipeline_run(attempt)

    def _detect_error(self, error_message: str, run_result: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode == MODE_MOCK or self._detector is None:
            return self._mock_detection(error_message)
        activities    = run_result.get("activities", [])
        activity_name = activities[0]["activity_name"] if activities else None
        return self._detector.detect(
            error_message=error_message,
            pipeline_name=self._pipeline_name,          # ← DYNAMIC
            activity_name=activity_name,
        )

    # ── Simulation (mock / local modes) ──────────────────────────────────────

    def _simulate_pipeline_run(self, attempt: int) -> Dict[str, Any]:
        import uuid
        run_id = str(uuid.uuid4())
        if attempt == 0:
            logger.info("  [MOCK] Simulating failure for [%s]", self._pipeline_name)
            return {
                "run_id":        run_id,
                "status":        "Failed",
                "duration_seconds": 45,
                "message":       "Column mapping failed",
                "activities":    [{
                    "activity_name": "Copy_CSV_to_Parquet",
                    "activity_type": "Copy",
                    "status":        "Failed",
                    "error": {
                        "errorCode": "2200",
                        "message": (
                            "Column mapping failed: column 'customer_id' does not "
                            "exist in sink table."
                        ),
                    },
                    "duration_ms": 45000,
                }],
                "error_message": (
                    "Column mapping failed: column 'customer_id' does not exist "
                    "in sink table."
                ),
                "pipeline_name": self._pipeline_name,
            }
        else:
            logger.info("  [MOCK] Simulating success for [%s]", self._pipeline_name)
            return {
                "run_id":           run_id,
                "status":           "Succeeded",
                "duration_seconds": 120,
                "message":          "Pipeline completed successfully",
                "activities":       [],
                "error_message":    "",
                "pipeline_name":    self._pipeline_name,
            }

    def _mock_detection(self, error_message: str) -> Dict[str, Any]:
        return {
            "matched_error_code": "ADF-002",
            "matched_error_name": "Copy Activity Failed Due to Schema Mismatch",
            "confidence":         0.95,
            "detected_root_cause": "Column missing in sink — schema drift",
            "resolution_action":  "refresh_schema_and_remap_columns",
            "suggested_steps":    ["Compare schemas", "Refresh dataset", "Update mapping"],
            "auto_resolvable":    True,
            "severity":           "HIGH",
            "explanation":        "Mock detection",
            "detection_method":   "mock",
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_retry_delay(self, error_code: str, attempt: int) -> int:
        if error_code == "ADF-003":
            return 60 * (attempt + 1)
        return PIPELINE_CONFIG.retry_delay_seconds * (2 ** attempt)

    def _build_report(self, session_id, ingest_result, final_run) -> Dict[str, Any]:
        return {
            "session_id":     session_id,
            "pipeline_name":  self._pipeline_name,       # ← actual pipeline name
            "mode":           self._mode,
            "timestamp_utc":  datetime.now(timezone.utc).isoformat(),
            "ingestion":      ingest_result,
            "total_attempts": len(self._run_history),
            "final_status":   final_run.get("status", "Unknown"),
            "run_history":    self._run_history,
            "final_run":      final_run,
        }

    def _save_report(self, report: Dict[str, Any], session_id: str) -> None:
        os.makedirs("logs", exist_ok=True)
        path = f"logs/run_report_{session_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("📄 Run report saved: %s", path)

    def _print_summary(self, report: Dict[str, Any]) -> None:
        status   = report["final_status"]
        attempts = report["total_attempts"]
        ingest   = report["ingestion"]
        emoji    = "✅" if status == "Succeeded" else "❌"

        logger.info("")
        logger.info("=" * 60)
        logger.info("  %s  FINAL SUMMARY", emoji)
        logger.info("=" * 60)
        logger.info("  Session    : %s", report["session_id"])
        logger.info("  Pipeline   : %s", report["pipeline_name"])
        logger.info("  Status     : %s", status)
        logger.info("  Attempts   : %d", attempts)
        logger.info("  Files OK   : %d", ingest.get("files_processed", 0))
        logger.info("  Files Fail : %d", ingest.get("files_failed", 0))
        for i, run in enumerate(report["run_history"], 1):
            detection  = run.get("detection", {})
            resolution = run.get("resolution", {})
            logger.info(
                "  Attempt %-2d : status=%-12s | error=%s (%.0f%%) | resolved=%s",
                i, run["status"],
                detection.get("matched_error_code", "—"),
                detection.get("confidence", 0) * 100,
                resolution.get("success", False),
            )
        logger.info("=" * 60)
