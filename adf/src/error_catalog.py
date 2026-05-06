"""
=======================================================
  ADF Error Catalog
  6 known pipeline error types with resolution actions.

  ADF-001  Linked Service Connection Failure
  ADF-002  Schema Mismatch
  ADF-003  File Not Found
  ADF-004  Pipeline Timeout
  ADF-005  Stored Procedure Failure
  ADF-006  Parquet Invalid Column Name  ← auto-fixed by
           downloading CSV, sanitising headers, re-uploading
=======================================================
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class AdfError:
    error_code:            str
    error_name:            str
    common_error_message:  str
    main_cause:            str
    business_impact:       str
    troubleshooting_steps: List[str]
    keywords:              List[str]
    resolution_action:     str
    auto_resolvable:       bool = True
    severity:              str  = "HIGH"


ADF_ERROR_CATALOG: List[AdfError] = [

    AdfError(
        error_code="ADF-001",
        error_name="Linked Service Connection Failure",
        common_error_message="Cannot connect to SQL Database / Storage account",
        main_cause=(
            "Wrong credentials, expired secret, firewall block, "
            "wrong server name, or private endpoint issue"
        ),
        business_impact="Pipeline fails at the connection stage; data load is blocked",
        troubleshooting_steps=[
            "Go to ADF Studio → Manage → Linked Services → Test Connection",
            "Verify Key Vault secret is not expired",
            "Check firewall / networking rules and private endpoint",
            "Confirm integration runtime is running",
        ],
        keywords=[
            "connect", "connection", "credentials", "sql database",
            "storage account", "linked service", "secret", "firewall",
            "private endpoint", "integration runtime", "authentication",
        ],
        resolution_action="refresh_linked_service_credentials",
        auto_resolvable=True,
        severity="HIGH",
    ),

    AdfError(
        error_code="ADF-002",
        error_name="Copy Activity Schema Mismatch",
        common_error_message="Column mapping failed or column does not exist in source or sink",
        main_cause=(
            "Source and target columns differ, datatype mismatch, "
            "missing column, or changed source schema"
        ),
        business_impact="Data copy fails or wrong data loaded into target",
        troubleshooting_steps=[
            "Compare source and sink schema in ADF Studio",
            "Refresh schema on both datasets",
            "Update column mapping in Copy Activity",
            "Align datatype conversions and nullable flags",
        ],
        keywords=[
            "column", "schema", "mapping", "datatype", "mismatch",
            "does not exist", "sink", "source schema", "nullable",
            "copy activity", "column mapping failed",
        ],
        resolution_action="refresh_schema_and_remap_columns",
        auto_resolvable=True,
        severity="HIGH",
    ),

    AdfError(
        error_code="ADF-003",
        error_name="File Not Found in Source Path",
        common_error_message="The specified file does not exist",
        main_cause=(
            "File missing in Blob/ADLS/SFTP path, wrong folder, "
            "incorrect filename pattern, or file not yet received"
        ),
        business_impact="Pipeline fails before ingestion starts",
        troubleshooting_steps=[
            "Check source folder path and blob container",
            "Validate dynamic filename expression",
            "Confirm file has arrived from upstream system",
            "Add Get Metadata + If Condition before Copy Activity",
        ],
        keywords=[
            "file not found", "does not exist", "path", "blob",
            "adls", "sftp", "folder", "filename", "missing file", "source path",
        ],
        resolution_action="validate_source_path_and_wait_for_file",
        auto_resolvable=True,
        severity="MEDIUM",
    ),

    AdfError(
        error_code="ADF-004",
        error_name="Pipeline Timeout",
        common_error_message="Activity timed out",
        main_cause=(
            "Large data volume, slow source query, network delay, "
            "or integration runtime performance issue"
        ),
        business_impact="Pipeline misses SLA; downstream reports delayed",
        troubleshooting_steps=[
            "Increase Copy Activity timeout (ADF Studio → activity → Settings)",
            "Enable query partitioning with partition column + boundaries",
            "Increase Data Integration Units (DIU) to 16 or 32",
            "Check integration runtime health in Azure Monitor",
        ],
        keywords=[
            "timeout", "timed out", "sla", "slow", "network delay",
            "integration runtime", "data integration units", "diu",
            "performance", "large data", "query",
        ],
        resolution_action="increase_timeout_and_optimize_diu",
        auto_resolvable=True,
        severity="HIGH",
    ),

    AdfError(
        error_code="ADF-005",
        error_name="Stored Procedure / SQL Script Failure",
        common_error_message="SQL execution failed or stored procedure failed with error",
        main_cause=(
            "Stored procedure logic error, deadlock, constraint violation, "
            "duplicate records, or invalid input parameter"
        ),
        business_impact="Transformation or post-load processing stops midway",
        troubleshooting_steps=[
            "Run stored procedure manually in SSMS to reproduce error",
            "Validate input parameters passed by ADF",
            "Check database error logs for deadlock / blocking",
            "Add TRY-CATCH and MERGE / NOT EXISTS for dedup",
        ],
        keywords=[
            "stored procedure", "sql", "deadlock", "constraint",
            "duplicate", "violation", "parameter", "procedure failed",
            "sql execution", "database error",
        ],
        resolution_action="retry_sp_with_dedup_and_deadlock_handling",
        auto_resolvable=True,
        severity="HIGH",
    ),

    AdfError(
        error_code="ADF-006",
        error_name="Parquet Invalid Column Name",
        common_error_message=(
            "The column name is invalid. "
            "Column name cannot contain these characters: [,;{}()\\n\\t=]"
        ),
        main_cause=(
            "Source CSV or SQL view has column names containing characters "
            "forbidden by the Parquet format: ,  ;  {  }  (  )  \\n  \\t  ="
        ),
        business_impact=(
            "ADF Copy Activity cannot write Parquet; "
            "pipeline fails every run until column names are cleaned"
        ),
        troubleshooting_steps=[
            "AUTO FIX: resolver downloads source CSV, renames bad columns, re-uploads",
            "Or: ADF Studio → Copy Activity → Mapping → rename offending column",
            "Or: fix SQL view alias / CSV header at the source",
        ],
        keywords=[
            "column name", "invalid", "parquet", "cannot contain",
            "characters", "[,;{}()\\n\\t=]", "parquetinvalidcolumnname",
            "copy data", "column names",
        ],
        resolution_action="sanitise_parquet_column_names",
        auto_resolvable=True,   # LLM reports True; policy=CONDITIONAL enforces the fix
        severity="HIGH",
    ),
]


def get_error_catalog_as_dict() -> List[Dict[str, Any]]:
    return [
        {
            "error_code":            e.error_code,
            "error_name":            e.error_name,
            "common_error_message":  e.common_error_message,
            "main_cause":            e.main_cause,
            "business_impact":       e.business_impact,
            "troubleshooting_steps": e.troubleshooting_steps,
            "keywords":              e.keywords,
            "auto_resolvable":       e.auto_resolvable,
            "resolution_action":     e.resolution_action,
            "severity":              e.severity,
        }
        for e in ADF_ERROR_CATALOG
    ]


def get_error_by_code(code: str) -> AdfError | None:
    return next((e for e in ADF_ERROR_CATALOG if e.error_code == code), None)
