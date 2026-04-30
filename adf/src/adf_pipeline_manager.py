"""
=======================================================
  ADF Pipeline Manager
  Uses azure-mgmt-datafactory SDK to:
    - Trigger pipeline runs
    - Monitor run status
    - Cancel and restart runs
    - Retrieve activity-level error details
=======================================================
"""

import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from azure.identity import ClientSecretCredential
from azure.mgmt.datafactory import DataFactoryManagementClient
from azure.mgmt.datafactory.models import CreateRunResponse

from src.logger import get_logger
from config.settings import AZURE_CONFIG, PIPELINE_CONFIG

logger = get_logger(__name__)

# ADF pipeline run terminal statuses
_TERMINAL_STATUSES = {"Succeeded", "Failed", "Cancelled", "Canceling"}
_FAILED_STATUSES = {"Failed", "Cancelled", "Canceling"}


class AdfPipelineManager:
    """
    Manages ADF pipeline lifecycle:
      trigger → monitor → detect failure → hand off to resolver → restart
    """

    def __init__(self):
        credential = ClientSecretCredential(
            tenant_id=AZURE_CONFIG.tenant_id,
            client_id=AZURE_CONFIG.client_id,
            client_secret=AZURE_CONFIG.client_secret,
        )
        self._client = DataFactoryManagementClient(credential, AZURE_CONFIG.subscription_id)
        self._rg = AZURE_CONFIG.resource_group
        self._factory = AZURE_CONFIG.adf_name
        self._pipeline = AZURE_CONFIG.adf_pipeline_name
        self._poll_interval = 30   # seconds between status polls
        self._timeout = PIPELINE_CONFIG.pipeline_timeout_minutes * 60

    # ── Public API ────────────────────────────────────────────────────────────

    def trigger_pipeline(self, parameters: Optional[Dict[str, Any]] = None) -> str:
        """
        Trigger a pipeline run.

        Args:
            parameters: Optional ADF pipeline parameters dict

        Returns:
            run_id (str)
        """
        logger.info("🚀 Triggering pipeline: %s", self._pipeline)
        response: CreateRunResponse = self._client.pipelines.create_run(
            resource_group_name=self._rg,
            factory_name=self._factory,
            pipeline_name=self._pipeline,
            parameters=parameters or {},
        )
        run_id = response.run_id
        logger.info("   Run ID: %s", run_id)
        return run_id

    def wait_for_completion(self, run_id: str) -> Dict[str, Any]:
        """
        Poll ADF until the run reaches a terminal state.

        Returns:
            dict with { run_id, status, duration_seconds, error_message, activities }
        """
        logger.info("⏳ Monitoring run: %s", run_id)
        start = datetime.now(timezone.utc)
        elapsed = 0

        while elapsed < self._timeout:
            run = self._client.pipeline_runs.get(self._rg, self._factory, run_id)
            status = run.status
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()

            logger.info(
                "   Status: %-12s | Elapsed: %ds", status, int(elapsed)
            )

            if status in _TERMINAL_STATUSES:
                activities = self._get_activity_errors(run_id)
                result = {
                    "run_id": run_id,
                    "status": status,
                    "duration_seconds": elapsed,
                    "message": run.message or "",
                    "activities": activities,
                    "error_message": self._extract_error_message(run, activities),
                }
                if status == "Succeeded":
                    logger.info("✅ Pipeline run succeeded in %.0fs", elapsed)
                else:
                    logger.error(
                        "❌ Pipeline run %s in %.0fs — Error: %s",
                        status, elapsed, result["error_message"][:300]
                    )
                return result

            time.sleep(self._poll_interval)

        # Timeout exceeded
        logger.error("⏰ Monitoring timeout after %ds", elapsed)
        return {
            "run_id": run_id,
            "status": "MonitorTimeout",
            "duration_seconds": elapsed,
            "message": "Pipeline monitoring timed out",
            "activities": [],
            "error_message": "Pipeline monitoring timed out after %d seconds" % int(elapsed),
        }

    def cancel_run(self, run_id: str) -> None:
        """Cancel an in-progress pipeline run."""
        logger.warning("🛑 Cancelling run: %s", run_id)
        self._client.pipeline_runs.cancel(self._rg, self._factory, run_id)

    def restart_pipeline(
        self,
        parameters: Optional[Dict[str, Any]] = None,
        delay_seconds: int = 0,
    ) -> str:
        """
        Restart the pipeline (trigger a fresh run).

        Args:
            parameters: Pipeline parameters for the new run
            delay_seconds: Wait time before triggering (for dependency / file arrival)

        Returns:
            new run_id
        """
        if delay_seconds > 0:
            logger.info("⏱️  Waiting %ds before restart ...", delay_seconds)
            time.sleep(delay_seconds)
        logger.info("♻️  Restarting pipeline: %s", self._pipeline)
        return self.trigger_pipeline(parameters)

    def get_pipeline_run_url(self, run_id: str) -> str:
        """Return the ADF Monitor deep-link URL for a run."""
        return (
            f"https://adf.azure.com/monitoring/pipelineruns/{run_id}"
            f"?factory=/subscriptions/{AZURE_CONFIG.subscription_id}"
            f"/resourceGroups/{self._rg}/providers/Microsoft.DataFactory"
            f"/factories/{self._factory}"
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_activity_errors(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve activity-level run details for a pipeline run."""
        try:
            activities = self._client.activity_runs.query_by_pipeline_run(
                resource_group_name=self._rg,
                factory_name=self._factory,
                run_id=run_id,
                filter_parameters={"lastUpdatedAfter": "1970-01-01T00:00:00Z",
                                    "lastUpdatedBefore": "9999-12-31T00:00:00Z"},
            )
            result = []
            for act in activities.value:
                result.append({
                    "activity_name": act.activity_name,
                    "activity_type": act.activity_type,
                    "status": act.status,
                    "error": act.error or {},
                    "duration_ms": act.duration_in_ms,
                })
            return result
        except Exception as exc:
            logger.warning("Could not retrieve activity runs: %s", exc)
            return []

    def _extract_error_message(
        self, run: Any, activities: List[Dict[str, Any]]
    ) -> str:
        """Combine pipeline-level and activity-level error info into one string."""
        parts = []
        if run.message:
            parts.append(f"Pipeline: {run.message}")
        for act in activities:
            err = act.get("error", {})
            if err:
                msg = err.get("message", "")
                code = err.get("errorCode", "")
                if msg or code:
                    parts.append(f"Activity [{act['activity_name']}]: [{code}] {msg}")
        return " | ".join(parts) if parts else "Unknown error"
