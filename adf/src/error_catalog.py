"""
=======================================================
  ADF Error Catalog
  Defines all known pipeline errors, their causes,
  impacts, and auto-resolution strategies.

  HOW TO ADD A NEW ERROR:
  ───────────────────────
  1. Add a new AdfError() block below ADF-006
  2. Increment error_code  (ADF-007, ADF-008 ...)
  3. Choose a unique resolution_action string
  4. Add handler method in error_resolver.py
  5. Register it in the dispatch table in resolve()
  6. Add a unit test in tests/test_pipeline.py
=======================================================
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class AdfError:
    """Represents a single ADF error type with full resolution context."""
    error_code: str
    error_name: str
    common_error_message: str
    main_cause: str
    business_impact: str
    troubleshooting_steps: List[str]
    keywords: List[str]           # Used by LLM keyword fallback for matching
    auto_resolvable: bool = True
    resolution_action: str = ""   # Must match a key in error_resolver.py dispatch table
    severity: str = "HIGH"        # HIGH | MEDIUM | LOW


# ── Full Error Catalog ───────────────────────────────────────────────────────
ADF_ERROR_CATALOG: List[AdfError] = [

    # ── ADF-001 ───────────────────────────────────────────────────────────────
    AdfError(
        error_code="ADF-001",
        error_name="Linked Service Connection Failure",
        common_error_message="Cannot connect to SQL Database / Storage account",
        main_cause=(
            "Wrong credentials, expired secret, firewall issue, "
            "wrong server name, or private endpoint issue"
        ),
        business_impact="Pipeline fails at the connection stage and data load is blocked",
        troubleshooting_steps=[
            "Validate linked service connection",
            "Check username, password, access key, or service principal secret",
            "Verify Key Vault secret is not expired",
            "Check firewall and networking rules",
            "Validate private endpoint and integration runtime connectivity",
        ],
        keywords=[
            "connect", "connection", "credentials", "sql database",
            "storage account", "linked service", "secret", "firewall",
            "private endpoint", "integration runtime", "authentication",
            "sqlfailedtoconnect", "cannot connect", "login failed",
            "server provided routing", "timeout expired", "check the linked service",
        ],
        auto_resolvable=True,
        resolution_action="refresh_linked_service_credentials",
        severity="HIGH",
    ),

    # ── ADF-002 ───────────────────────────────────────────────────────────────
    AdfError(
        error_code="ADF-002",
        error_name="Copy Activity Failed Due to Schema Mismatch",
        common_error_message="Column mapping failed or the column does not exist in source or sink",
        main_cause=(
            "Source and target table columns are different, datatype mismatch, "
            "missing column, or changed source schema"
        ),
        business_impact="Data copy fails or incorrect data may be loaded into the target",
        troubleshooting_steps=[
            "Compare source and sink schema",
            "Refresh schema in ADF dataset",
            "Update column mapping",
            "Align datatype conversions",
            "Handle nullable and newly added columns",
        ],
        keywords=[
            "column", "schema", "mapping", "datatype", "mismatch",
            "does not exist", "sink", "source schema", "nullable",
            "copy activity", "column mapping failed",
        ],
        auto_resolvable=True,
        resolution_action="refresh_schema_and_remap_columns",
        severity="HIGH",
    ),

    # ── ADF-003 ───────────────────────────────────────────────────────────────
    AdfError(
        error_code="ADF-003",
        error_name="File Not Found in Source Path",
        common_error_message="The specified file does not exist",
        main_cause=(
            "File is missing in Blob, ADLS, or SFTP path; wrong folder path; "
            "incorrect filename pattern; or file not yet received"
        ),
        business_impact="Pipeline fails before ingestion starts",
        troubleshooting_steps=[
            "Check source folder path",
            "Validate dynamic filename expression",
            "Confirm file arrival from upstream system",
            "Check trigger timing",
            "Add validation activity before copy activity",
        ],
        keywords=[
            "file not found", "does not exist", "path", "blob",
            "adls", "sftp", "folder", "filename", "trigger", "upstream",
            "missing file", "source path", "usererrorsourceblobnotexist",
            "required blob is missing", "blob is missing", "404", "not found",
            "containername", "blob storage",
        ],
        auto_resolvable=True,
        resolution_action="validate_source_path_and_wait_for_file",
        severity="MEDIUM",
    ),

    # ── ADF-004 ───────────────────────────────────────────────────────────────
    AdfError(
        error_code="ADF-004",
        error_name="Pipeline Timeout Error",
        common_error_message="Activity timed out",
        main_cause=(
            "Large data volume, slow source query, network delay, "
            "or integration runtime performance issue"
        ),
        business_impact="Pipeline misses SLA and downstream reports get delayed",
        troubleshooting_steps=[
            "Increase activity timeout",
            "Optimize source query",
            "Enable partitioning",
            "Increase Data Integration Units",
            "Check integration runtime health",
        ],
        keywords=[
            "timeout", "timed out", "sla", "slow", "network delay",
            "integration runtime", "data integration units", "partitioning",
            "performance", "large data", "query",
        ],
        auto_resolvable=True,
        resolution_action="increase_timeout_and_optimize_diu",
        severity="HIGH",
    ),

    # ── ADF-005 ───────────────────────────────────────────────────────────────
    AdfError(
        error_code="ADF-005",
        error_name="Stored Procedure or SQL Script Failure",
        common_error_message="SQL execution failed or procedure failed with error",
        main_cause=(
            "Stored procedure logic error, deadlock, constraint violation, "
            "duplicate records, or invalid input parameter"
        ),
        business_impact="Transformation or post-load processing stops midway",
        troubleshooting_steps=[
            "Run stored procedure manually in database",
            "Validate input parameters",
            "Check database error logs",
            "Review constraints and duplicate records",
            "Check for deadlocks or blocking sessions",
        ],
        keywords=[
            "stored procedure", "sql", "sql script", "deadlock",
            "constraint", "duplicate", "violation", "parameter",
            "procedure failed", "sql execution", "database error",
        ],
        auto_resolvable=True,
        resolution_action="retry_sp_with_dedup_and_deadlock_handling",
        severity="HIGH",
    ),

    # ── ADF-006 ── Real error from pf-observability-datafactory (Apr 2026) ────
    AdfError(
        error_code="ADF-006",
        error_name="Parquet Invalid Column Name",
        common_error_message=(
            "The column name is invalid. Column name cannot contain "
            "these characters: [,;{}()\\n\\t=]"
        ),
        main_cause=(
            "Source data contains column names with special characters that "
            "Parquet format does not allow: [,;{}()\\n\\t=]. "
            "Common causes: SQL views with calculated or aliased columns, "
            "CSV files with brackets or semicolons in headers, or Excel exports "
            "with formula-based column names."
        ),
        business_impact=(
            "Copy Activity fails before writing any data to the Parquet landing "
            "folder. All downstream transforms, reports, and dependent pipelines "
            "are blocked until the column name is fixed."
        ),
        troubleshooting_steps=[
            "Identify which column name contains the invalid Parquet character",
            "Add explicit column mapping in ADF Copy Activity to rename the column",
            "Use Derived Column in ADF Mapping Data Flow to sanitise column names",
            "Apply sanitise_column_names() in data_processor.py before writing Parquet",
            "Fix the column name at source — SQL view alias or CSV header",
        ],
        keywords=[
            "parquet", "invalid column name", "column name", "cannot contain",
            "characters", "parquetinvalidcolumnname", "bracket", "semicolon",
            "special character", "copy data", "column",
            "[,;{}()", "hybriddeliveryexception", "microsoft.datatransfer",
        ],
        auto_resolvable=True,
        resolution_action="sanitise_parquet_column_names",
        severity="HIGH",
    ),

    # ────────────────────────────────────────────────────────────────────────────
    # ADD NEW ERRORS BELOW THIS LINE
    # Follow the pattern above — increment to ADF-007, ADF-008, etc.
    # ────────────────────────────────────────────────────────────────────────────

]


# ── Helper functions ─────────────────────────────────────────────────────────

def get_error_catalog_as_dict() -> List[Dict[str, Any]]:
    """Return the full catalog as a list of dicts (injected into LLM context)."""
    return [
        {
            "error_code": e.error_code,
            "error_name": e.error_name,
            "common_error_message": e.common_error_message,
            "main_cause": e.main_cause,
            "business_impact": e.business_impact,
            "troubleshooting_steps": e.troubleshooting_steps,
            "keywords": e.keywords,
            "auto_resolvable": e.auto_resolvable,
            "resolution_action": e.resolution_action,
            "severity": e.severity,
        }
        for e in ADF_ERROR_CATALOG
    ]


def get_error_by_code(code: str) -> AdfError | None:
    """Look up a single error by its ADF-XXX code."""
    return next((e for e in ADF_ERROR_CATALOG if e.error_code == code), None)
