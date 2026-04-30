"""
=======================================================
  ADF LLM Pipeline Configuration
  Azure AI Foundry + ADF ETL Framework
  Munich RE × Capgemini DataRend Program
=======================================================
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AzureConfig:
    """Azure platform configuration — loaded from environment variables."""

    # ── Azure Subscription & Tenant ──────────────────────────────────────────
    subscription_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    )
    tenant_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_TENANT_ID", "")
    )
    resource_group: str = field(
        default_factory=lambda: os.environ.get("AZURE_RESOURCE_GROUP", "rg-datarend-poc")
    )

    # ── Azure Data Factory ────────────────────────────────────────────────────
    adf_name: str = field(
        default_factory=lambda: os.environ.get("ADF_FACTORY_NAME", "adf-datarend-poc")
    )
    adf_pipeline_name: str = field(
        default_factory=lambda: os.environ.get("ADF_PIPELINE_NAME", "pl_csv_to_parquet_ingestion")
    )

    # ── Azure Storage (ADLS Gen2 / Blob) ─────────────────────────────────────
    storage_account_name: str = field(
        default_factory=lambda: os.environ.get("AZURE_STORAGE_ACCOUNT", "stgdatarendpoc")
    )
    storage_account_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_STORAGE_KEY", "")
    )
    storage_connection_string: str = field(
        default_factory=lambda: os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    )
    source_container: str = field(
        default_factory=lambda: os.environ.get("SOURCE_CONTAINER", "raw-data")
    )
    landing_container: str = field(
        default_factory=lambda: os.environ.get("LANDING_CONTAINER", "landing")
    )
    source_folder: str = field(
        default_factory=lambda: os.environ.get("SOURCE_FOLDER", "csv-input")
    )
    landing_folder: str = field(
        default_factory=lambda: os.environ.get("LANDING_FOLDER", "parquet-output")
    )

    # ── Azure AI Foundry / Azure OpenAI ──────────────────────────────────────
    azure_openai_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "AZURE_OPENAI_ENDPOINT", "https://<your-foundry-resource>.openai.azure.com/"
        )
    )
    azure_openai_api_key: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_KEY", "")
    )
    azure_openai_api_version: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    )
    # Deployment name inside Azure AI Foundry
    llm_deployment_name: str = field(
        default_factory=lambda: os.environ.get("LLM_DEPLOYMENT_NAME", "gpt-4o")
    )

    # ── Service Principal (for ADF SDK auth) ─────────────────────────────────
    client_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_ID", "")
    )
    client_secret: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_SECRET", "")
    )

    # ── Azure Key Vault (optional — for secret rotation) ─────────────────────
    key_vault_url: Optional[str] = field(
        default_factory=lambda: os.environ.get("KEY_VAULT_URL", None)
    )


@dataclass
class PipelineConfig:
    """ETL pipeline behaviour settings."""
    max_retry_attempts: int = 3
    retry_delay_seconds: int = 30
    pipeline_timeout_minutes: int = 60
    max_file_size_mb: int = 500
    parquet_compression: str = "snappy"      # snappy | gzip | brotli
    parquet_row_group_size: int = 100_000
    log_level: str = "INFO"
    log_file: str = "logs/pipeline_run.log"
    enable_monitoring: bool = True


# ── Global singletons ────────────────────────────────────────────────────────
AZURE_CONFIG = AzureConfig()
PIPELINE_CONFIG = PipelineConfig()
