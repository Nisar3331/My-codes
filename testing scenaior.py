"""
FUNCTION 1: fetch_run_context
-------------------------------
Purpose : Calls ADF REST API to get full details of a failed pipeline run.
Trigger : HTTP POST  (called by Azure Monitor Alert or Event Grid)
Output  : JSON with run details, error message, logs, retry count
"""

import json
import logging
import os
import requests
import azure.functions as func
from datetime import datetime, timezone

# ── Logging setup ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ── Helper: Get Azure ARM token using Service Principal ────────────────────────
def get_arm_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """
    Logs into Azure using Service Principal credentials.
    Returns an access token to call ADF REST APIs.
    """
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/token"
    payload = {
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "resource":      "https://management.azure.com/"
    }
    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    token = response.json().get("access_token")
    logger.info("ARM token acquired successfully")
    return token


# ── Helper: Get pipeline run details from ADF ──────────────────────────────────
def get_pipeline_run(token: str, subscription_id: str,
                     resource_group: str, factory_name: str,
                     run_id: str) -> dict:
    """
    Calls ADF Management API to get details of a specific pipeline run.
    Returns status, error message, start/end time, pipeline name.
    """
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{factory_name}"
        f"/pipelineruns/{run_id}"
        f"?api-version=2018-06-01"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    logger.info(f"Pipeline run details fetched for RunID: {run_id}")
    return response.json()


# ── Helper: Get activity runs inside a pipeline run ────────────────────────────
def get_activity_runs(token: str, subscription_id: str,
                      resource_group: str, factory_name: str,
                      run_id: str) -> list:
    """
    Gets details of each activity (step) inside the pipeline run.
    This is where the actual error message and stack trace lives.
    """
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{factory_name}"
        f"/pipelineruns/{run_id}/queryActivityruns"
        f"?api-version=2018-06-01"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    # Filter to only failed activities
    body = {
        "lastUpdatedAfter":  "2000-01-01T00:00:00Z",
        "lastUpdatedBefore": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filters": [
            {"operand": "Status", "operator": "Equals", "values": ["Failed"]}
        ]
    }
    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    activities = response.json().get("value", [])
    logger.info(f"Found {len(activities)} failed activities in run {run_id}")
    return activities


# ── Helper: Get previous runs of the same pipeline (history) ──────────────────
def get_pipeline_run_history(token: str, subscription_id: str,
                              resource_group: str, factory_name: str,
                              pipeline_name: str, last_n: int = 5) -> list:
    """
    Gets the last N runs of the same pipeline.
    Used to detect if this is a recurring failure.
    """
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{factory_name}"
        f"/queryPipelineRuns"
        f"?api-version=2018-06-01"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    body = {
        "lastUpdatedAfter":  "2024-01-01T00:00:00Z",
        "lastUpdatedBefore": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filters": [
            {"operand": "PipelineName", "operator": "Equals",
             "values": [pipeline_name]}
        ],
        "orderBy": [{"orderBy": "RunEnd", "order": "DESC"}]
    }
    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    runs = response.json().get("value", [])[:last_n]
    logger.info(f"Fetched {len(runs)} historical runs for {pipeline_name}")
    return runs


# ── MAIN FUNCTION ENTRY POINT ──────────────────────────────────────────────────
def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP Trigger Entry Point.

    Accepts POST with JSON body:
    {
        "run_id":       "adf-run-id-here",
        "pipeline_name": "Customer_Data_Load",   (optional)
        "source":       "azure_monitor"           (optional)
    }

    Returns full run context needed for error classification.
    """
    logger.info(" fetch_run_context triggered")

    # ── 1. Read credentials from environment variables ─────────────────────────
    tenant_id       = os.environ.get("AZURE_TENANT_ID")
    client_id       = os.environ.get("AZURE_CLIENT_ID")
    client_secret   = os.environ.get("AZURE_CLIENT_SECRET")
    subscription_id = os.environ.get("ADF_SUBSCRIPTION_ID")
    resource_group  = os.environ.get("ADF_RESOURCE_GROUP")
    factory_name    = os.environ.get("ADF_FACTORY_NAME")

    # ── 2. Validate all credentials are present ────────────────────────────────
    missing = [k for k, v in {
        "AZURE_TENANT_ID":       tenant_id,
        "AZURE_CLIENT_ID":       client_id,
        "AZURE_CLIENT_SECRET":   client_secret,
        "ADF_SUBSCRIPTION_ID":   subscription_id,
        "ADF_RESOURCE_GROUP":    resource_group,
        "ADF_FACTORY_NAME":      factory_name,
    }.items() if not v]

    if missing:
        msg = f"Missing environment variables: {missing}"
        logger.error(msg)
        return func.HttpResponse(json.dumps({"error": msg}), status_code=500,
                                 mimetype="application/json")

    # ── 3. Parse request body ──────────────────────────────────────────────────
    try:
        req_body  = req.get_json()
        run_id    = req_body.get("run_id")
        source    = req_body.get("source", "manual")
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body. Expected: {run_id, pipeline_name}"}),
            status_code=400, mimetype="application/json"
        )

    if not run_id:
        return func.HttpResponse(
            json.dumps({"error": "run_id is required in request body"}),
            status_code=400, mimetype="application/json"
        )

    # ── 4. Fetch all context from ADF ──────────────────────────────────────────
    try:
        # Step A: Get ARM token
        token = get_arm_token(tenant_id, client_id, client_secret)

        # Step B: Get pipeline run details
        run_details = get_pipeline_run(
            token, subscription_id, resource_group, factory_name, run_id
        )

        pipeline_name = run_details.get("pipelineName", "unknown")
        run_status    = run_details.get("status", "Unknown")
        run_start     = run_details.get("runStart", "")
        run_end       = run_details.get("runEnd", "")
        message       = run_details.get("message", "")

        # Step C: Get individual activity failures (detailed errors)
        activity_runs = get_activity_runs(
            token, subscription_id, resource_group, factory_name, run_id
        )

        # Step D: Get historical runs to detect recurring failures
        history = get_pipeline_run_history(
            token, subscription_id, resource_group, factory_name, pipeline_name
        )
        failed_history_count = sum(
            1 for r in history if r.get("status") == "Failed"
        )

        # ── 5. Build the context payload ───────────────────────────────────────
        # Extract the most detailed error from activity runs
        detailed_error = ""
        error_code     = ""
        failed_activity = ""

        if activity_runs:
            first_failed = activity_runs[0]
            error_obj    = first_failed.get("error", {})
            detailed_error  = error_obj.get("message", message)
            error_code      = error_obj.get("errorCode", "")
            failed_activity = first_failed.get("activityName", "")

        context = {
            # Core identifiers
            "run_id":           run_id,
            "pipeline_name":    pipeline_name,
            "factory_name":     factory_name,
            "resource_group":   resource_group,
            "subscription_id":  subscription_id,

            # Run metadata
            "status":           run_status,
            "run_start":        run_start,
            "run_end":          run_end,
            "trigger_source":   source,
            "fetched_at":       datetime.now(timezone.utc).isoformat(),

            # Error details
            "error_message":    detailed_error or message,
            "error_code":       error_code,
            "failed_activity":  failed_activity,
            "raw_message":      message,

            # Activity-level details
            "failed_activities": [
                {
                    "name":         a.get("activityName"),
                    "type":         a.get("activityType"),
                    "status":       a.get("status"),
                    "error_code":   a.get("error", {}).get("errorCode", ""),
                    "error_msg":    a.get("error", {}).get("message", ""),
                    "duration_ms":  a.get("durationInMs", 0),
                }
                for a in activity_runs
            ],

            # History context
            "recent_run_count":         len(history),
            "recent_failed_count":      failed_history_count,
            "is_recurring_failure":     failed_history_count >= 3,

            # Retry info
            "retry_attempt":            run_details.get("runGroupId", 0),
        }

        logger.info(f" Context built successfully for {pipeline_name} / {run_id}")

        return func.HttpResponse(
            json.dumps(context, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except requests.exceptions.HTTPError as e:
        msg = f"ADF API error: {str(e)} | Response: {e.response.text[:500]}"
        logger.error(msg)
        return func.HttpResponse(json.dumps({"error": msg}),
                                 status_code=502, mimetype="application/json")

    except Exception as e:
        msg = f"Unexpected error: {str(e)}"
        logger.exception(msg)
        return func.HttpResponse(json.dumps({"error": msg}),
                                 status_code=500, mimetype="application/json")
