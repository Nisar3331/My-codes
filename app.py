import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.config import load_config
from backend.persistence import get_all_polling_configs, update_poll_timestamp
from backend.polling.aws_cloudwatch import CloudWatchPoller
from backend.polling.azure_log_analytics import AzureLogAnalyticsPoller
from backend.polling.gcp_cloud_logging import GcpCloudLoggingPoller
from backend.polling.airflow_logs import AirflowLogsPoller
from backend.polling.executor import execute_auto_remediation

logger = logging.getLogger(__name__)

POLLER_MAP = {
    "aws_glue": CloudWatchPoller,
    "azure_data_factory": AzureLogAnalyticsPoller,
    "azure_databricks": AzureLogAnalyticsPoller,
    "gcp_composer": GcpCloudLoggingPoller,
    "airflow": AirflowLogsPoller,
}


class PollingOrchestrator:
    """Manages scheduled log polling for all instances."""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.jobs = {}  # Track job IDs per instance

    def start(self):
        """Start the polling scheduler."""
        if self.scheduler.running:
            return

        logger.info("Starting polling orchestrator")
        self.scheduler.start()
        self._schedule_all_instances()

    def stop(self):
        """Stop the polling scheduler."""
        if self.scheduler.running:
            logger.info("Stopping polling orchestrator")
            self.scheduler.shutdown()

    def _schedule_all_instances(self):
        """Load all instances from config and schedule polling."""
        try:
            polling_configs = get_all_polling_configs()
            for config in polling_configs:
                self._schedule_instance(config)
        except Exception as e:
            logger.error(f"Error scheduling instances: {e}")

    def _schedule_instance(self, config: dict):
        """Schedule polling for a single instance."""
        platform = config["platform"]
        instance_name = config["instance_name"]
        interval_seconds = config.get("poll_interval_seconds", 300)
        job_id = f"{platform}-{instance_name}"

        # Remove existing job if present
        if job_id in self.jobs:
            self.scheduler.remove_job(self.jobs[job_id])

        # Schedule new job
        job = self.scheduler.add_job(
            self._poll_instance,
            IntervalTrigger(seconds=interval_seconds),
            args=[platform, instance_name],
            id=job_id,
            name=f"Poll {platform}/{instance_name}",
            replace_existing=True,
        )
        self.jobs[job_id] = job.id

        logger.info(f"Scheduled polling for {platform}/{instance_name} every {interval_seconds}s")

    def _poll_instance(self, platform: str, instance_name: str):
        """Poll logs for a single instance."""
        try:
            logger.debug(f"Polling {platform}/{instance_name}")

            # Load instance config
            config = load_config()
            instances = config.get("integrations", {}).get(platform, [])
            instance = next((i for i in instances if i.get("name") == instance_name), None)

            if not instance:
                logger.warning(f"Instance {platform}/{instance_name} not found in config")
                return

            # Get poller for platform
            Poller = POLLER_MAP.get(platform)
            if not Poller:
                logger.error(f"No poller for platform {platform}")
                return

            # Poll logs from last 5+ minutes
            poller = Poller(instance)
            now = datetime.now()
            since = now - timedelta(minutes=10)  # Look back 10 minutes
            until = now

            logs = poller.get_logs(since, until)

            if not logs:
                logger.debug(f"No logs found for {platform}/{instance_name}")
                update_poll_timestamp(platform, instance_name)
                return

            # Detect failures
            failure = poller.detect_failures(logs)

            if failure:
                logger.info(
                    f"Failure detected in {platform}/{instance_name}: {failure['failure_signature']}"
                )
                # Execute auto remediation
                execute_auto_remediation(
                    platform=platform,
                    instance_name=instance_name,
                    failure=failure,
                    instance=instance,
                )
            else:
                logger.debug(f"No failures detected in {platform}/{instance_name}")

            # Update last polled timestamp
            update_poll_timestamp(platform, instance_name)

        except Exception as e:
            logger.error(f"Error polling {platform}/{instance_name}: {e}", exc_info=True)


# Global orchestrator instance
_orchestrator = None


def get_orchestrator() -> PollingOrchestrator:
    """Get or create the global polling orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PollingOrchestrator()
    return _orchestrator


def start_polling():
    """Start the polling orchestrator."""
    orchestrator = get_orchestrator()
    orchestrator.start()


def stop_polling():
    """Stop the polling orchestrator."""
    orchestrator = get_orchestrator()
    orchestrator.stop()
