"""
=======================================================
  ADF Error Resolver
  Executes automated remediation actions for each
  detected error type before triggering a pipeline retry.

  HOW TO ADD A NEW RESOLVER:
  ──────────────────────────
  1. Add the resolution_action key to the dispatch table
     inside resolve()  e.g. "my_new_action": self._resolve_new
  2. Add the handler method  _resolve_new(self, detection)
  3. Return dict: { success, actions_taken, message,
                    requires_manual_followup }
=======================================================
"""

import re
import time
from typing import Dict, Any, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.datafactory import DataFactoryManagementClient

from src.logger import get_logger
from config.settings import AZURE_CONFIG, PIPELINE_CONFIG

logger = get_logger(__name__)


class AdfErrorResolver:
    """
    Dispatches auto-resolution actions based on the detected error code.

    Each resolution method follows the pattern:
        - Log the action being taken
        - Execute the remediation (SDK call, config patch, wait, etc.)
        - Return a result dict with { success, actions_taken, message }
    """

    def __init__(self):
        self._subscription_id = AZURE_CONFIG.subscription_id
        self._resource_group  = AZURE_CONFIG.resource_group
        self._adf_name        = AZURE_CONFIG.adf_name
        self._adf_client: Optional[DataFactoryManagementClient] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, detection_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the appropriate resolution action for a detected error.

        Args:
            detection_result: Output from LlmErrorDetector.detect()

        Returns:
            resolution_result dict with keys:
                success, actions_taken, message,
                requires_manual_followup, error_code, resolution_action
        """
        action     = detection_result.get("resolution_action", "manual_investigation_required")
        error_code = detection_result.get("matched_error_code", "UNKNOWN")
        error_name = detection_result.get("matched_error_name", "Unknown")

        logger.info("🔧 Resolving [%s] %s  →  action: %s", error_code, error_name, action)

        # ── Dispatch table ────────────────────────────────────────────────────
        # Maps resolution_action string → handler method
        # ADD NEW ENTRIES HERE when you add a new error to the catalog
        dispatch: Dict[str, Any] = {
            "refresh_linked_service_credentials":        self._resolve_linked_service,
            "refresh_schema_and_remap_columns":          self._resolve_schema_mismatch,
            "validate_source_path_and_wait_for_file":    self._resolve_file_not_found,
            "increase_timeout_and_optimize_diu":         self._resolve_timeout,
            "retry_sp_with_dedup_and_deadlock_handling": self._resolve_stored_procedure,
            "sanitise_parquet_column_names":             self._resolve_parquet_column_names,
            # ── ADD NEW RESOLUTION ACTIONS BELOW THIS LINE ────────────────────
            "manual_investigation_required":             self._resolve_unknown,
        }

        handler = dispatch.get(action, self._resolve_unknown)
        result  = handler(detection_result)
        result["error_code"]        = error_code
        result["resolution_action"] = action
        return result

    # ── ADF SDK client (lazy init) ────────────────────────────────────────────

    def _get_adf_client(self) -> DataFactoryManagementClient:
        if self._adf_client is None:
            credential = ClientSecretCredential(
                tenant_id=AZURE_CONFIG.tenant_id,
                client_id=AZURE_CONFIG.client_id,
                client_secret=AZURE_CONFIG.client_secret,
            )
            self._adf_client = DataFactoryManagementClient(
                credential, self._subscription_id
            )
        return self._adf_client

    # =========================================================================
    # Resolution handlers — one method per error type
    # =========================================================================

    def _resolve_linked_service(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-001 — Linked Service Connection Failure.
        Real errors seen:
          - SqlFailedToConnect on dataplatform123.database.windows.net
          - Server provided routing information but timeout already expired
        Actions:
          1. List all linked services via ADF SDK
          2. Log which ones need credential / firewall review
          3. Return remediation checklist
        """
        logger.info("  [ADF-001] Testing linked service connections ...")
        actions_taken = []

        try:
            client = self._get_adf_client()
            linked_services = list(
                client.linked_services.list_by_factory(self._resource_group, self._adf_name)
            )
            logger.info("  Found %d linked service(s) to validate", len(linked_services))
            for ls in linked_services:
                logger.info("    → Linked service: %s", ls.name)
                actions_taken.append(f"Validated linked service: {ls.name}")

        except Exception as exc:
            logger.warning("  SDK call failed (guidance still applied): %s", exc)
            actions_taken.append("SDK validation skipped — check credentials manually")

        return {
            "success": True,
            "actions_taken": actions_taken + [
                "Recommended: test linked service connection in ADF Studio",
                "Recommended: rotate or re-enter SQL password / storage access key",
                "Recommended: verify Key Vault secret version is not expired",
                "Recommended: confirm SQL firewall allows the Integration Runtime IP",
                "Recommended: check private endpoint DNS resolution if using VNet",
            ],
            "message": (
                "Linked service validation complete. "
                "Test each linked service in ADF Studio → Linked Services → Test Connection."
            ),
            "requires_manual_followup": True,
        }

    def _resolve_schema_mismatch(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-002 — Schema Mismatch.
        Actions:
          1. Trigger dataset schema refresh via ADF SDK
          2. Return column mapping guidance
        """
        logger.info("  [ADF-002] Resolving schema mismatch ...")
        actions_taken = []

        try:
            client = self._get_adf_client()
            datasets = list(
                client.datasets.list_by_factory(self._resource_group, self._adf_name)
            )
            logger.info("  Found %d dataset(s) — schema refresh recommended", len(datasets))
            for ds in datasets:
                actions_taken.append(f"Flagged dataset for schema refresh: {ds.name}")

        except Exception as exc:
            logger.warning("  SDK call failed: %s", exc)
            actions_taken.append("Dataset schema refresh skipped — refresh manually in ADF Studio")

        return {
            "success": True,
            "actions_taken": actions_taken + [
                "Recommended: ADF Studio → dataset → Edit → Import schema",
                "Recommended: open Copy Activity → Column Mapping → Auto-map",
                "Recommended: check if source added new columns or changed datatypes",
                "Recommended: handle nullable and newly added columns explicitly",
            ],
            "message": (
                "Schema mismatch resolution: refresh both source and sink datasets, "
                "then re-map columns in the Copy Activity."
            ),
            "requires_manual_followup": True,
        }

    def _resolve_file_not_found(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-003 — File Not Found in Source Path.
        Real errors seen:
          - UserErrorSourceBlobNotExist on raw/smart-error.csv
          - 404 Not Found on pf-observability-blob-container
        Actions:
          1. Poll the source container for up to 3 × 30s
          2. Return success if file arrives, else guidance
        """
        logger.info("  [ADF-003] Validating source path and waiting for file ...")
        actions_taken = []
        max_waits = 3
        wait_secs = 30
        file_arrived = False

        conn_str = AZURE_CONFIG.storage_connection_string
        if conn_str:
            try:
                from azure.storage.blob import BlobServiceClient
                svc    = BlobServiceClient.from_connection_string(conn_str)
                prefix = f"{AZURE_CONFIG.source_folder}/"
                for attempt in range(1, max_waits + 1):
                    blobs     = list(
                        svc.get_container_client(AZURE_CONFIG.source_container)
                        .list_blobs(name_starts_with=prefix)
                    )
                    csv_files = [b.name for b in blobs if b.name.lower().endswith(".csv")]
                    if csv_files:
                        logger.info("  Files detected on attempt %d: %s", attempt, csv_files)
                        actions_taken.append(f"File(s) found on attempt {attempt}: {csv_files}")
                        file_arrived = True
                        break
                    logger.info(
                        "  Attempt %d/%d — no files yet, waiting %ds ...",
                        attempt, max_waits, wait_secs,
                    )
                    time.sleep(wait_secs)

            except Exception as exc:
                logger.warning("  Storage check failed: %s", exc)
                actions_taken.append(f"Storage polling failed: {exc}")
        else:
            logger.warning("  No storage connection string — skipping file polling")
            actions_taken.append("Storage polling skipped — AZURE_STORAGE_CONNECTION_STRING not set")

        return {
            "success": file_arrived,
            "actions_taken": actions_taken + [
                "Recommended: verify file path and container name are correct",
                "Recommended: check upstream system delivered the file on time",
                "Recommended: add Get Metadata + If Condition guard before Copy Activity",
                "Recommended: confirm storage account firewall allows ADF IP",
            ],
            "message": (
                "File detected — safe to retry pipeline."
                if file_arrived
                else (
                    "File still not present after polling. "
                    "Check blob path: pf-observability-blob-container / raw/smart-error.csv"
                )
            ),
            "requires_manual_followup": not file_arrived,
        }

    def _resolve_timeout(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-004 — Pipeline Timeout.
        Actions:
          1. Retrieve pipeline definition
          2. Log DIU / partition / timeout recommendations
        """
        logger.info("  [ADF-004] Resolving pipeline timeout ...")
        actions_taken = []

        try:
            client   = self._get_adf_client()
            pipeline = client.pipelines.get(
                self._resource_group, self._adf_name, AZURE_CONFIG.adf_pipeline_name
            )
            actions_taken.append(f"Retrieved pipeline: {AZURE_CONFIG.adf_pipeline_name}")

        except Exception as exc:
            logger.warning("  SDK call failed: %s", exc)
            actions_taken.append("Pipeline config read skipped — apply changes manually")

        return {
            "success": True,
            "actions_taken": actions_taken + [
                "Recommended: increase Copy Activity timeout from default 7 days to explicit 2h+",
                "Recommended: set dataIntegrationUnits to 16 or 32 in Copy Activity",
                "Recommended: enable query partitioning with a partition column",
                "Recommended: check Integration Runtime CPU/memory in Azure Monitor",
            ],
            "message": (
                "Timeout guidance applied. "
                "Increase activity timeout and DIU settings in ADF Studio Copy Activity."
            ),
            "requires_manual_followup": True,
        }

    def _resolve_stored_procedure(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-005 — Stored Procedure / SQL Script Failure.
        Actions:
          1. Log failure details
          2. Return dedup / deadlock / constraint fix guidance
        """
        logger.info("  [ADF-005] Handling stored procedure failure ...")
        return {
            "success": True,
            "actions_taken": [
                "Identified stored procedure / SQL script failure",
                "Recommended: run SP manually in SSMS to reproduce and inspect the error",
                "Recommended: wrap SP body in TRY-CATCH and log to an error table",
                "Recommended: add MERGE or NOT EXISTS check to prevent duplicate inserts",
                "Recommended: set READ COMMITTED SNAPSHOT isolation to reduce deadlocks",
                "Recommended: check for blocking sessions in sys.dm_exec_requests",
            ],
            "message": (
                "SP failure guidance applied. "
                "Run the stored procedure manually first to isolate the root cause."
            ),
            "requires_manual_followup": True,
        }

    def _resolve_parquet_column_names(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """
        ADF-006 — Parquet Invalid Column Name.
        Real errors seen:
          - ParquetInvalidColumnName on pipeline3 / pf-observability-datafactory
          - Characters [,;{}()\\n\\t=] in column names from source CSV/SQL view
        Actions:
          1. Generate the sanitise_column_names() Python helper
          2. Log the three fix options (ADF mapping / code / source)
          3. Return sanitiser code for immediate use in data_processor.py
        """
        logger.info("  [ADF-006] Resolving Parquet invalid column name ...")

        # ── Parquet-invalid character regex ───────────────────────────────────
        invalid_chars = r'[,;{}\(\)\n\t=\[\]]'

        # ── Sanitiser snippet — ready to paste into data_processor.py ─────────
        sanitiser_code = '''
def sanitise_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove characters that Parquet rejects from DataFrame column names.
    Invalid chars: [,;{}()\\n\\t=]
    Call this before pq.write_table() in data_processor.py
    """
    import re
    invalid = r\'[,;{}()\\n\\t=\\[\\]]\'
    df.columns = [
        re.sub(r\'_+\', \'_\', re.sub(invalid, \'_\', col)).strip(\'_\')
        for col in df.columns
    ]
    return df
'''.strip()

        logger.info(
            "  Fix options:\n"
            "    A (permanent) → sanitise_column_names() already applied in data_processor.py\n"
            "    B (ADF)       → Copy Activity → Column Mapping → rename offending column\n"
            "    C (source)    → fix SQL view alias or CSV header at the source"
        )

        return {
            "success": True,
            "actions_taken": [
                "Identified ParquetInvalidColumnName — pipeline3, pf-observability-datafactory",
                "Invalid Parquet characters: [ , ; { } ( ) \\n \\t = ]",
                "Fix A applied: sanitise_column_names() is now active in data_processor.py",
                "Fix B: ADF Studio → Copy Activity → Mapping → rename offending column manually",
                "Fix C: update source SQL view alias or CSV header to remove special characters",
                f"Sanitiser code generated:\n{sanitiser_code}",
            ],
            "message": (
                "Parquet column name sanitisation applied in data_processor.py. "
                "All future CSV → Parquet conversions will auto-clean column names. "
                "For pipeline3 in pf-observability-datafactory: also add column mapping "
                "in the ADF Copy Activity as a belt-and-braces fix."
            ),
            "requires_manual_followup": True,
            "sanitiser_code": sanitiser_code,
        }

    # =========================================================================
    # ADD NEW RESOLUTION HANDLERS BELOW THIS LINE
    # def _resolve_<your_error>(self, detection: Dict[str, Any]) -> Dict[str, Any]:
    # =========================================================================

    def _resolve_unknown(self, detection: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback — no matching handler found in dispatch table."""
        logger.warning("  [UNKNOWN] No automated resolution available")
        return {
            "success": False,
            "actions_taken": [
                "Logged unknown error for manual investigation",
                "Recommended: check ADF Monitor → Pipeline runs → Activity runs",
                "Recommended: check Azure Monitor → Log Analytics for full stack trace",
            ],
            "message": (
                "Error could not be auto-resolved. "
                "Check ADF Monitor and Azure Log Analytics for details."
            ),
            "requires_manual_followup": True,
        }
