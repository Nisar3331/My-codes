"""
=======================================================
  ADF Pipeline Manager — DYNAMIC VERSION

  Key fix vs static version:
  ─────────────────────────
  - trigger_pipeline() accepts pipeline_name parameter
  - restart_pipeline() accepts pipeline_name parameter
  - ADF_PIPELINE_NAME env variable is only the DEFAULT
    fallback, not a hardcoded override
  - Supports triggering and monitoring ANY pipeline
    in the data factory dynamically
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

_TERMINAL_STATUSES = {"Succeeded", "Failed", "Cancelled", "Canceling"}
_FAILED_STATUSES   = {"Failed", "Cancelled", "Canceling"}


class AdfPipelineManager:
    """
    Manages ADF pipeline lifecycle dynamically.

    Every public method accepts an optional pipeline_name parameter.
    If not provided, falls back to ADF_PIPELINE_NAME from env.
    This means one manager instance can handle ALL pipelines
    in the data factory — not just the one in the config.
    """

    def __init__(self):
        credential = ClientSecretCredential(
            tenant_id=AZURE_CONFIG.tenant_id,
            client_id=AZURE_CONFIG.client_id,
            client_secret=AZURE_CONFIG.client_secret,
        )
        self._client          = DataFactoryManagementClient(
            credential, AZURE_CONFIG.subscription_id
        )
        self._rg              = AZURE_CONFIG.resource_group
        self._factory         = AZURE_CONFIG.adf_name
        self._default_pipeline = AZURE_CONFIG.adf_pipeline_name   # fallback only
        self._poll_interval   = 30
        self._timeout         = PIPELINE_CONFIG.pipeline_timeout_minutes * 60

    # ── Public API ────────────────────────────────────────────────────────────

    def trigger_pipeline(
        self,
        pipeline_name: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Trigger any pipeline in the data factory.

        Args:
            pipeline_name : Name of pipeline to trigger.
                            If None → uses ADF_PIPELINE_NAME from env (default fallback).
            parameters    : Optional ADF pipeline parameters dict.

        Returns:
            run_id (str)
        """
        # ── Use provided name OR fall back to env variable ────────────────────
        name = pipeline_name or self._default_pipeline

        if not name:
            raise ValueError(
                "pipeline_name must be provided or ADF_PIPELINE_NAME must be set in .env"
            )

        logger.info("🚀 Triggering pipeline: %s (factory: %s)", name, self._factory)

        response: CreateRunResponse = self._client.pipelines.create_run(
            resource_group_name=self._rg,
            factory_name=self._factory,
            pipeline_name=name,             # ← DYNAMIC — uses the actual failing pipeline
            parameters=parameters or {},
        )
        run_id = response.run_id
        logger.info("   Run ID: %s", run_id)
        return run_id

    def wait_for_completion(self, run_id: str) -> Dict[str, Any]:
        """
        Poll ADF until a run reaches a terminal state.
        Works for any pipeline run ID.
        """
        logger.info("⏳ Monitoring run: %s", run_id)
        start   = datetime.now(timezone.utc)
        elapsed = 0

        while elapsed < self._timeout:
            run     = self._client.pipeline_runs.get(self._rg, self._factory, run_id)
            status  = run.status
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()

            logger.info("   Status: %-12s | Elapsed: %ds", status, int(elapsed))

            if status in _TERMINAL_STATUSES:
                activities = self._get_activity_errors(run_id)
                result = {
                    "run_id":        run_id,
                    "status":        status,
                    "duration_seconds": elapsed,
                    "message":       run.message or "",
                    "activities":    activities,
                    "error_message": self._extract_error_message(run, activities),
                }
                if status == "Succeeded":
                    logger.info("✅ Run succeeded in %.0fs", elapsed)
                else:
                    logger.error(
                        "❌ Run %s in %.0fs — Error: %s",
                        status, elapsed, result["error_message"][:300],
                    )
                return result

            time.sleep(self._poll_interval)

        logger.error("⏰ Monitoring timeout after %ds", elapsed)
        return {
            "run_id":           run_id,
            "status":           "MonitorTimeout",
            "duration_seconds": elapsed,
            "message":          "Pipeline monitoring timed out",
            "activities":       [],
            "error_message":    f"Monitor timeout after {int(elapsed)}s",
        }

    def cancel_run(self, run_id: str) -> None:
        """Cancel any in-progress pipeline run."""
        logger.warning("🛑 Cancelling run: %s", run_id)
        self._client.pipeline_runs.cancel(self._rg, self._factory, run_id)

    def restart_pipeline(
        self,
        pipeline_name: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        delay_seconds: int = 0,
    ) -> str:
        """
        Restart any pipeline by name.

        Args:
            pipeline_name : Name of the pipeline to restart.
                            If None → uses ADF_PIPELINE_NAME from env.
                            Pass the name from the Event Grid payload to
                            restart the exact pipeline that failed.
            parameters    : Optional pipeline parameters.
            delay_seconds : Wait time before triggering (e.g. for file arrival).

        Returns:
            new run_id
        """
        name = pipeline_name or self._default_pipeline

        if delay_seconds > 0:
            logger.info("⏱️  Waiting %ds before restarting %s ...", delay_seconds, name)
            time.sleep(delay_seconds)

        logger.info("♻️  Restarting pipeline: %s", name)
        return self.trigger_pipeline(
            pipeline_name=name,         # ← DYNAMIC — exact pipeline name
            parameters=parameters,
        )

    def list_all_pipelines(self) -> List[str]:
        """
        List all pipelines in the data factory.
        Useful for verifying which pipelines are available.
        """
        pipelines = self._client.pipelines.list_by_factory(self._rg, self._factory)
        names     = [p.name for p in pipelines]
        logger.info("Found %d pipelines in %s: %s", len(names), self._factory, names)
        return names

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
        try:
            activities = self._client.activity_runs.query_by_pipeline_run(
                resource_group_name=self._rg,
                factory_name=self._factory,
                run_id=run_id,
                filter_parameters={
                    "lastUpdatedAfter":  "1970-01-01T00:00:00Z",
                    "lastUpdatedBefore": "9999-12-31T00:00:00Z",
                },
            )
            return [
                {
                    "activity_name": a.activity_name,
                    "activity_type": a.activity_type,
                    "status":        a.status,
                    "error":         a.error or {},
                    "duration_ms":   a.duration_in_ms,
                }
                for a in activities.value
            ]
        except Exception as exc:
            logger.warning("Could not retrieve activity runs: %s", exc)
            return []

    def _extract_error_message(
        self, run: Any, activities: List[Dict[str, Any]]
    ) -> str:
        parts = []
        if run.message:
            parts.append(f"Pipeline: {run.message}")
        for act in activities:
            err  = act.get("error", {})
            msg  = err.get("message", "")
            code = err.get("errorCode", "")
            if msg or code:
                parts.append(f"Activity [{act['activity_name']}]: [{code}] {msg}")
        return " | ".join(parts) if parts else "Unknown error"
