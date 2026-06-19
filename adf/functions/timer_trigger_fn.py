"""
=======================================================
  Azure Function — Timer Trigger
  Runs the ADF ETL pipeline on a daily schedule.
  Cron: 0 0 6 * * *  →  6:00 AM UTC every day
=======================================================
"""

import logging
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pipeline_orchestrator import PipelineOrchestrator, MODE_FULL
from src.logger import get_logger

logger = get_logger("timer_trigger")
app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 6 * * *",       # 6:00 AM UTC daily
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def scheduled_pipeline_run(timer: func.TimerRequest) -> None:
    """Scheduled daily ADF ETL pipeline run."""
    if timer.past_due:
        logger.warning("Timer is past due — running immediately")

    logger.info("Timer trigger fired — starting ADF ETL pipeline")

    orchestrator = PipelineOrchestrator(mode=MODE_FULL, max_retries=3)
    report = orchestrator.run()

    status = report.get("final_status", "Unknown")
    attempts = report.get("total_attempts", 0)
    files_ok = report.get("ingestion", {}).get("files_processed", 0)

    logger.info(
        "Pipeline finished | status=%s | attempts=%d | files_processed=%d",
        status, attempts, files_ok,
    )

    if status != "Succeeded":
        raise RuntimeError(f"Pipeline did not succeed: {status}")
