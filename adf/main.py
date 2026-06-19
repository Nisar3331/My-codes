"""
=======================================================
  ADF LLM Self-Healing ETL Pipeline
  Main Entry Point

  Usage:
    python main.py --mode mock          # Fully offline demo
    python main.py --mode local         # Local CSV files, real LLM
    python main.py --mode full          # Full Azure deployment
    python main.py --detect-only "error message here"
=======================================================
"""

import argparse
import json
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.logger import get_logger
from src.pipeline_orchestrator import PipelineOrchestrator, MODE_FULL, MODE_LOCAL, MODE_MOCK
from src.llm_error_detector import LlmErrorDetector
from src.error_resolver import AdfErrorResolver

logger = get_logger("main")


def run_pipeline(mode: str, retries: int) -> None:
    """Run the full pipeline orchestration."""
    orchestrator = PipelineOrchestrator(mode=mode, max_retries=retries)
    report = orchestrator.run()
    status = report.get("final_status", "Unknown")
    sys.exit(0 if status == "Succeeded" else 1)


def detect_and_resolve(error_message: str) -> None:
    """Standalone: detect + resolve a single error message (for testing)."""
    logger.info("🔍 Standalone error detection mode")
    logger.info("Error: %s", error_message)

    detector = LlmErrorDetector()
    detection = detector.detect(error_message)
    logger.info("Detection result:\n%s", json.dumps(detection, indent=2))

    resolver = AdfErrorResolver()
    resolution = resolver.resolve(detection)
    logger.info("Resolution result:\n%s", json.dumps(resolution, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(
        description="ADF LLM Self-Healing ETL Pipeline — Munich RE × Capgemini DataRend"
    )
    parser.add_argument(
        "--mode",
        choices=[MODE_FULL, MODE_LOCAL, MODE_MOCK],
        default=MODE_MOCK,
        help=(
            "Run mode: "
            "'mock' = fully offline; "
            "'local' = local files + real LLM; "
            "'full' = real Azure ADF + ADLS + OpenAI"
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum number of auto-retry attempts (default: 3)",
    )
    parser.add_argument(
        "--detect-only",
        metavar="ERROR_MESSAGE",
        help="Run only error detection + resolution for the given error message string",
    )
    parser.add_argument(
        "--demo-errors",
        action="store_true",
        help="Run LLM detection against all 5 error types and print results",
    )

    args = parser.parse_args()

    if args.detect_only:
        detect_and_resolve(args.detect_only)
    elif args.demo_errors:
        run_error_demo()
    else:
        run_pipeline(args.mode, args.retries)


def run_error_demo():
    """
    Demo: run LLM detection against sample messages for all 5 error types.
    Works in mock mode (uses keyword fallback if LLM not configured).
    """
    sample_errors = [
        {
            "name": "ADF-001 Linked Service",
            "message": "Cannot connect to SQL Database. Login failed for user 'sa'. Firewall rule may be blocking access.",
        },
        {
            "name": "ADF-002 Schema Mismatch",
            "message": "Column mapping failed: column 'order_date' does not exist in the sink table. Source schema has changed.",
        },
        {
            "name": "ADF-003 File Not Found",
            "message": "The specified path does not exist: '/raw-data/csv-input/daily_sales_20240401.csv'. File not found in ADLS Gen2.",
        },
        {
            "name": "ADF-004 Timeout",
            "message": "Activity 'Copy_Large_Table' timed out after 3600 seconds. Integration runtime performance degraded.",
        },
        {
            "name": "ADF-005 Stored Procedure",
            "message": "Stored procedure 'usp_load_fact_sales' failed. Transaction deadlock on table 'FactSales'. Duplicate key violation.",
        },
    ]

    detector = LlmErrorDetector()
    resolver = AdfErrorResolver()

    print("\n" + "=" * 70)
    print("  ADF ERROR DETECTION DEMO — All 5 Error Types")
    print("=" * 70)

    for sample in sample_errors:
        print(f"\n{'─' * 70}")
        print(f"  Test: {sample['name']}")
        print(f"  Error: {sample['message'][:80]}...")
        detection = detector.detect(sample["message"])
        resolution = resolver.resolve(detection)

        print(f"  → Matched : [{detection.get('matched_error_code')}] {detection.get('matched_error_name')}")
        print(f"  → Confidence : {detection.get('confidence', 0):.0%}")
        print(f"  → Action     : {detection.get('resolution_action')}")
        print(f"  → Resolved   : {resolution.get('success')}")
        print(f"  → Message    : {resolution.get('message')}")


if __name__ == "__main__":
    main()
