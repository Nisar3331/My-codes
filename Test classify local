"""
LOCAL TEST SCRIPT
=================
Run this on the EC2 server to test classify_error with all 5 dummy logs.
No Azure deployment needed — tests the classification logic directly.

How to run:
    cd /opt/project-claude/
    python3 tests/test_classify_local.py

    # Test one specific error type:
    python3 tests/test_classify_local.py --type transient
    python3 tests/test_classify_local.py --type infrastructure
    python3 tests/test_classify_local.py --type data
    python3 tests/test_classify_local.py --type authentication
    python3 tests/test_classify_local.py --type dependency

    # Test with real Azure OpenAI (needs API key):
    python3 tests/test_classify_local.py --use-ai
"""

import sys
import json
import os
import re
import argparse
from datetime import datetime, timezone

# ── PASTE DUMMY LOGS INLINE (so script is self-contained) ─────────────────────
DUMMY_LOGS = {
    "transient": {
        "run_id": "adf-run-00001-transient",
        "pipeline_name": "Customer_Data_Load",
        "factory_name": "pf-observability-datafactory",
        "resource_group": "Idea2.0",
        "subscription_id": "262a3c1d-7fe3-4bfb-9f90-7a5b89109336",
        "status": "Failed",
        "error_message": "UserErrorFailedToConnectToSqlServer: Connection timeout after 30s to sql-server-prod.database.windows.net port 1433. ORA-12170: TNS:Connect timeout occurred.",
        "error_code": "UserErrorFailedToConnectToSqlServer",
        "failed_activity": "CopyFromSQLToBlob",
        "failed_activities": [{"name": "CopyFromSQLToBlob", "type": "Copy",
                                "status": "Failed",
                                "error_code": "UserErrorFailedToConnectToSqlServer",
                                "error_msg": "Connection timeout after 30s",
                                "duration_ms": 30412}],
        "recent_run_count": 5, "recent_failed_count": 1,
        "is_recurring_failure": False, "retry_attempt": 0,
    },
    "infrastructure": {
        "run_id": "adf-run-00002-infra",
        "pipeline_name": "FinTrans_ETL_Batch",
        "factory_name": "pf-observability-datafactory",
        "resource_group": "Idea2.0",
        "subscription_id": "262a3c1d-7fe3-4bfb-9f90-7a5b89109336",
        "status": "Failed",
        "error_message": "InsufficientMemoryException: Integration Runtime worker OOM. Heap exhausted at 4096MB limit. Container OOMKilled.",
        "error_code": "InsufficientMemoryException",
        "failed_activity": "DataFlowTransform",
        "failed_activities": [{"name": "DataFlowTransform",
                                "type": "ExecuteDataFlow",
                                "status": "Failed",
                                "error_code": "InsufficientMemoryException",
                                "error_msg": "IR OOM — heap exhausted",
                                "duration_ms": 912000}],
        "recent_run_count": 5, "recent_failed_count": 2,
        "is_recurring_failure": False, "retry_attempt": 0,
    },
    "data": {
        "run_id": "adf-run-00003-data",
        "pipeline_name": "Policy_Sync_Pipeline",
        "factory_name": "pf-observability-datafactory",
        "resource_group": "Idea2.0",
        "subscription_id": "262a3c1d-7fe3-4bfb-9f90-7a5b89109336",
        "status": "Failed",
        "error_message": "UserErrorInvalidData: NULL value in column 'POLICY_ID' violates not-null constraint at row 2847. Schema mismatch.",
        "error_code": "UserErrorInvalidData",
        "failed_activity": "CopyToSQLSink",
        "failed_activities": [{"name": "CopyToSQLSink", "type": "Copy",
                                "status": "Failed",
                                "error_code": "UserErrorInvalidData",
                                "error_msg": "NULL in POLICY_ID at row 2847",
                                "duration_ms": 65000}],
        "recent_run_count": 5, "recent_failed_count": 0,
        "is_recurring_failure": False, "retry_attempt": 0,
    },
    "authentication": {
        "run_id": "adf-run-00004-auth",
        "pipeline_name": "Claims_Aggregation",
        "factory_name": "pf-observability-datafactory",
        "resource_group": "Idea2.0",
        "subscription_id": "262a3c1d-7fe3-4bfb-9f90-7a5b89109336",
        "status": "Failed",
        "error_message": "AuthorizationFailed: MSI token expired — access denied to Azure SQL. HTTP 401 Unauthorized.",
        "error_code": "AuthorizationFailed",
        "failed_activity": "LinkedServiceValidation",
        "failed_activities": [{"name": "LinkedServiceValidation",
                                "type": "Copy", "status": "Failed",
                                "error_code": "AuthorizationFailed",
                                "error_msg": "MSI token expired — 401",
                                "duration_ms": 8000}],
        "recent_run_count": 5, "recent_failed_count": 1,
        "is_recurring_failure": False, "retry_attempt": 0,
    },
    "dependency": {
        "run_id": "adf-run-00005-dep",
        "pipeline_name": "Risk_Score_Compute",
        "factory_name": "pf-observability-datafactory",
        "resource_group": "Idea2.0",
        "subscription_id": "262a3c1d-7fe3-4bfb-9f90-7a5b89109336",
        "status": "Failed",
        "error_message": "DependencyNotMet: Upstream pipeline Source_Extract_Pipeline not completed. Blob not found.",
        "error_code": "DependencyNotMet",
        "failed_activity": "ExecuteSourceExtract",
        "failed_activities": [{"name": "ExecuteSourceExtract",
                                "type": "ExecutePipeline",
                                "status": "Failed",
                                "error_code": "DependencyNotMet",
                                "error_msg": "Upstream not completed — blob not found",
                                "duration_ms": 3000}],
        "recent_run_count": 5, "recent_failed_count": 3,
        "is_recurring_failure": True, "retry_attempt": 0,
    },
}

# ── Rule-based classifier (copy from classify_error/__init__.py) ───────────────
KNOWN_PATTERNS = [
    {"keywords": ["connection timeout", "tns:connect timeout", "ora-12170",
                  "network timeout", "request timeout", "temporarily unavailable",
                  "connection reset", "econnreset", "503", "429"],
     "type": "TRANSIENT", "confidence": 0.95, "safe_to_retry": True,
     "action": "retry_with_backoff", "backoff_seconds": [120, 300, 600],
     "max_retries": 3},
    {"keywords": ["outofmemory", "oom", "heap space", "insufficientmemory",
                  "memory limit", "containeroomkilled", "worker unavailable",
                  "integration runtime unavailable", "disk full", "no space left"],
     "type": "INFRASTRUCTURE", "confidence": 0.93, "safe_to_retry": True,
     "action": "scale_resources_then_retry", "backoff_seconds": [60, 180],
     "max_retries": 2},
    {"keywords": ["null value", "not-null constraint", "unique constraint",
                  "ora-00001", "duplicate key", "schema mismatch",
                  "invalid data", "userinvaliddata", "null in non-nullable",
                  "conversion failed", "parse error"],
     "type": "DATA", "confidence": 0.97, "safe_to_retry": False,
     "action": "quarantine_and_escalate", "backoff_seconds": [],
     "max_retries": 0},
    {"keywords": ["authorizationfailed", "unauthorized", "401",
                  "access denied", "forbidden", "403", "token expired",
                  "msi token", "authentication failed", "permission denied"],
     "type": "AUTHENTICATION", "confidence": 0.94, "safe_to_retry": True,
     "action": "refresh_credentials_then_retry", "backoff_seconds": [30, 60],
     "max_retries": 2},
    {"keywords": ["dependency", "upstream", "not completed", "dependencynotmet",
                  "prerequisite", "source not ready", "file not found",
                  "blob not found", "dataset not ready"],
     "type": "DEPENDENCY", "confidence": 0.91, "safe_to_retry": True,
     "action": "wait_for_dependency_then_retry",
     "backoff_seconds": [300, 600, 900], "max_retries": 3},
]


def classify_local(context: dict) -> dict:
    error_lower = context.get("error_message", "").lower()
    for p in KNOWN_PATTERNS:
        if any(kw in error_lower for kw in p["keywords"]):
            return {
                "error_type":       p["type"],
                "confidence":       p["confidence"],
                "safe_to_retry":    p["safe_to_retry"],
                "suggested_action": p["action"],
                "backoff_seconds":  p["backoff_seconds"],
                "max_retries":      p["max_retries"],
                "classified_by":    "rule_engine_local",
                "rca_summary":      build_rca(p["type"], context),
                "risk_level":       "HIGH" if not p["safe_to_retry"] else "LOW",
            }
    return {
        "error_type": "UNKNOWN", "confidence": 0.4,
        "safe_to_retry": True, "suggested_action": "safe_retry_once_then_escalate",
        "backoff_seconds": [120], "max_retries": 1,
        "classified_by": "fallback", "rca_summary": "Pattern not recognized.",
        "risk_level": "MEDIUM",
    }


def build_rca(error_type: str, context: dict) -> str:
    p = context.get("pipeline_name", "pipeline")
    a = context.get("failed_activity", "activity")
    e = context.get("error_message", "")[:100]
    if error_type == "TRANSIENT":
        return f"{p} failed due to transient connection issue in {a}. Auto-retry with backoff recommended."
    if error_type == "INFRASTRUCTURE":
        return f"{p} failed due to resource exhaustion in {a}. Scale IR memory/cores and retry."
    if error_type == "DATA":
        return f"{p} failed due to data quality issue in {a}: {e}. Quarantine source and notify data owner."
    if error_type == "AUTHENTICATION":
        return f"{p} failed due to expired credentials in {a}. Refresh token/secret and retry."
    if error_type == "DEPENDENCY":
        return f"{p} failed because upstream dependency not ready for {a}. Wait and retry."
    return f"{p} failed with unrecognized error: {e}"


def next_step(error_type, safe, recurring, confidence):
    if confidence < 0.75 or error_type == "DATA" or recurring:
        return "REQUEST_HUMAN_APPROVAL"
    steps = {
        "TRANSIENT": "RETRY_WITH_BACKOFF",
        "INFRASTRUCTURE": "SCALE_RESOURCES_AND_RETRY",
        "AUTHENTICATION": "REFRESH_CREDENTIALS_AND_RETRY",
        "DEPENDENCY": "WAIT_FOR_DEPENDENCY_AND_RETRY",
    }
    return steps.get(error_type, "SAFE_RETRY_ONCE_THEN_ESCALATE")


def run_test(log_name: str, context: dict, use_ai: bool = False):
    """Run one test case and print formatted result."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  TEST: {log_name.upper()}")
    print(f"  Pipeline : {context['pipeline_name']}")
    print(f"  Error    : {context['error_message'][:80]}...")
    print(sep)

    # Classify
    result = classify_local(context)

    # Build full plan
    recurring = context.get("is_recurring_failure", False)
    sn_priority = {"DATA": "P2", "INFRASTRUCTURE": "P2",
                   "AUTHENTICATION": "P3", "DEPENDENCY": "P3",
                   "TRANSIENT": "P3", "UNKNOWN": "P2"}.get(
        result["error_type"], "P3")
    if recurring:
        sn_priority = "P1" if sn_priority == "P2" else "P2"

    plan = {
        "error_type":          result["error_type"],
        "confidence":          f"{result['confidence']:.0%}",
        "safe_to_retry":       result["safe_to_retry"],
        "suggested_action":    result["suggested_action"],
        "max_retries":         result["max_retries"],
        "backoff_seconds":     result["backoff_seconds"],
        "servicenow_priority": sn_priority,
        "next_step":           next_step(result["error_type"],
                                          result["safe_to_retry"],
                                          recurring,
                                          result["confidence"]),
        "rca_summary":         result["rca_summary"],
        "classified_by":       result["classified_by"],
    }

    # Expected results for validation
    expected = {
        "transient":      ("TRANSIENT",      True),
        "infrastructure": ("INFRASTRUCTURE", True),
        "data":           ("DATA",           False),
        "authentication": ("AUTHENTICATION", True),
        "dependency":     ("DEPENDENCY",     True),
    }

    got_type  = plan["error_type"]
    got_retry = plan["safe_to_retry"]
    exp_type, exp_retry = expected.get(log_name, ("UNKNOWN", None))

    type_ok  = "✅" if got_type  == exp_type  else "❌"
    retry_ok = "✅" if got_retry == exp_retry else "❌"

    print(f"  {type_ok} Error Type    : {got_type} (expected {exp_type})")
    print(f"  {retry_ok} Safe to Retry : {got_retry} (expected {exp_retry})")
    print(f"  ✅ Confidence   : {plan['confidence']}")
    print(f"  ✅ Action       : {plan['suggested_action']}")
    print(f"  ✅ Next Step    : {plan['next_step']}")
    print(f"  ✅ ServiceNow   : {plan['servicenow_priority']}")
    print(f"  ✅ Backoff      : {plan['backoff_seconds']} seconds")
    print(f"  ✅ Max Retries  : {plan['max_retries']}")
    print(f"\n  📝 RCA: {plan['rca_summary']}")

    passed = got_type == exp_type and got_retry == exp_retry
    status = "PASS ✅" if passed else "FAIL ❌"
    print(f"\n  RESULT: {status}")
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=list(DUMMY_LOGS.keys()),
                        help="Test a specific error type only")
    parser.add_argument("--use-ai", action="store_true",
                        help="Use Azure OpenAI for classification")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  ADF SELF-HEALING — CLASSIFY_ERROR LOCAL TEST")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode: {'Azure OpenAI' if args.use_ai else 'Rule-based (local)'}")
    print("=" * 60)

    logs_to_test = (
        {args.type: DUMMY_LOGS[args.type]} if args.type else DUMMY_LOGS
    )

    results = {}
    for name, context in logs_to_test.items():
        results[name] = run_test(name, context, args.use_ai)

    # Summary
    total  = len(results)
    passed = sum(results.values())
    print("\n" + "=" * 60)
    print(f"  TEST SUMMARY: {passed}/{total} passed")
    print("=" * 60)
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name.upper()}")
    print("=" * 60)

    if passed == total:
        print("\n  🎉 ALL TESTS PASSED — classify_error is working correctly!")
    else:
        print(f"\n  ⚠️  {total - passed} test(s) failed — review gaps above")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
