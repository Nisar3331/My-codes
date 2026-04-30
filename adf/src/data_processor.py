"""
=======================================================
  CSV → Parquet Data Processor
  Reads CSV files from Azure Blob / ADLS Gen2,
  converts them to Parquet, and writes to the
  landing folder.

  Enhancement (ADF-006):
    sanitise_column_names() is applied automatically
    on every CSV read to prevent ParquetInvalidColumnName
    errors caused by special characters in column headers.
=======================================================
"""

import io
import os
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from azure.storage.blob import BlobServiceClient, ContainerClient

from src.logger import get_logger
from config.settings import AZURE_CONFIG, PIPELINE_CONFIG

logger = get_logger(__name__)

# ── Parquet-invalid characters ───────────────────────────────────────────────
# Parquet rejects column names containing any of: [,;{}()\n\t=]
_PARQUET_INVALID_CHARS = re.compile(r'[,;{}\(\)\n\t=\[\]]')


def sanitise_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace Parquet-invalid characters in column names with underscores.

    Invalid characters: [,;{}()\\n\\t=]
    Also collapses consecutive underscores and strips leading/trailing ones.

    Example:
        "amount(usd)"   → "amount_usd"
        "order;id"      → "order_id"
        "customer[name]"→ "customer_name"
        "col  name"     → "col  name"  (spaces are allowed in Parquet)

    Call this before pq.write_table() to prevent ADF-006 errors.
    """
    cleaned = []
    for col in df.columns:
        sanitised = _PARQUET_INVALID_CHARS.sub('_', col)   # replace invalid chars
        sanitised = re.sub(r'_+', '_', sanitised)           # collapse runs of _
        sanitised = sanitised.strip('_')                    # remove leading/trailing _
        if sanitised != col:
            logger.debug("Column renamed: '%s'  →  '%s'", col, sanitised)
        cleaned.append(sanitised)
    df.columns = cleaned
    return df


class CsvToParquetProcessor:
    """
    Handles end-to-end CSV → Parquet conversion.

    Flow:
        1. List CSV files in source container / folder
        2. Download each CSV from ADLS / Blob
        3. Strip whitespace + sanitise Parquet-invalid chars from column names
        4. Apply type inference and basic validation
        5. Convert to Parquet (Snappy compressed by default)
        6. Upload Parquet to landing container / folder
        7. Return a processing manifest
    """

    def __init__(self, connection_string: Optional[str] = None):
        conn_str = connection_string or AZURE_CONFIG.storage_connection_string
        if not conn_str:
            conn_str = (
                f"DefaultEndpointsProtocol=https;"
                f"AccountName={AZURE_CONFIG.storage_account_name};"
                f"AccountKey={AZURE_CONFIG.storage_account_key};"
                f"EndpointSuffix=core.windows.net"
            )
        self._client            = BlobServiceClient.from_connection_string(conn_str)
        self._source_container  = AZURE_CONFIG.source_container
        self._landing_container = AZURE_CONFIG.landing_container
        self._source_folder     = AZURE_CONFIG.source_folder
        self._landing_folder    = AZURE_CONFIG.landing_folder
        self._compression       = PIPELINE_CONFIG.parquet_compression
        self._row_group_size    = PIPELINE_CONFIG.parquet_row_group_size

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """
        Execute the full CSV → Parquet pipeline against Azure ADLS Gen2.

        Returns:
            manifest dict: { files_processed, files_failed, details }
        """
        logger.info("📂 Starting CSV → Parquet processor  (Azure ADLS Gen2)")
        csv_files = self._list_csv_files()

        if not csv_files:
            logger.warning(
                "⚠️  No CSV files found in %s/%s",
                self._source_container, self._source_folder,
            )
            return {"files_processed": 0, "files_failed": 0, "details": []}

        logger.info("Found %d CSV file(s) to process", len(csv_files))
        manifest = {"files_processed": 0, "files_failed": 0, "details": []}

        for blob_name in csv_files:
            result = self._process_single_file(blob_name)
            manifest["details"].append(result)
            if result["status"] == "success":
                manifest["files_processed"] += 1
            else:
                manifest["files_failed"] += 1

        logger.info(
            "✅ Processing complete — Processed: %d | Failed: %d",
            manifest["files_processed"], manifest["files_failed"],
        )
        return manifest

    # ── Private helpers ───────────────────────────────────────────────────────

    def _list_csv_files(self):
        """List all .csv blobs under source_container/source_folder."""
        container: ContainerClient = self._client.get_container_client(
            self._source_container
        )
        prefix = f"{self._source_folder}/" if self._source_folder else ""
        blobs  = container.list_blobs(name_starts_with=prefix)
        return [b.name for b in blobs if b.name.lower().endswith(".csv")]

    def _process_single_file(self, blob_name: str) -> Dict[str, Any]:
        """Download, convert, and upload a single CSV file."""
        start          = datetime.now(timezone.utc)
        base_name      = os.path.splitext(os.path.basename(blob_name))[0]
        parquet_blob   = f"{self._landing_folder}/{base_name}.parquet"

        try:
            logger.info("  ⬇️  Downloading  : %s", blob_name)
            csv_bytes = self._download_blob(self._source_container, blob_name)

            logger.info("  🔄 Converting   : CSV → Parquet")
            df          = self._read_csv(csv_bytes)          # sanitisation happens here
            schema_info = self._infer_schema(df)
            parquet_buf = self._convert_to_parquet(df)

            logger.info("  ⬆️  Uploading    : %s", parquet_blob)
            self._upload_blob(self._landing_container, parquet_blob, parquet_buf)

            duration = (datetime.now(timezone.utc) - start).total_seconds()
            logger.info(
                "  ✅ Done: %s → %s  (%.1fs | %d rows | %d cols)",
                blob_name, parquet_blob, duration, df.shape[0], df.shape[1],
            )
            return {
                "status": "success",
                "source": blob_name,
                "destination": parquet_blob,
                "rows": df.shape[0],
                "columns": df.shape[1],
                "schema": schema_info,
                "duration_seconds": duration,
            }

        except Exception as exc:
            logger.error("  ❌ Failed: %s  →  %s", blob_name, str(exc))
            return {
                "status": "failed",
                "source": blob_name,
                "destination": parquet_blob,
                "error": str(exc),
                "duration_seconds": (datetime.now(timezone.utc) - start).total_seconds(),
            }

    def _download_blob(self, container: str, blob_name: str) -> bytes:
        blob_client = self._client.get_blob_client(container=container, blob=blob_name)
        return blob_client.download_blob().readall()

    def _upload_blob(self, container: str, blob_name: str, data: bytes) -> None:
        blob_client = self._client.get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(data, overwrite=True)

    def _read_csv(self, csv_bytes: bytes) -> pd.DataFrame:
        """
        Read CSV bytes into a DataFrame.
        Steps:
          1. Parse CSV with pandas type inference
          2. Strip whitespace from all column names
          3. Sanitise Parquet-invalid characters (ADF-006 fix)
        """
        df = pd.read_csv(
            io.BytesIO(csv_bytes),
            low_memory=False,
        )
        # Step 2 — strip whitespace
        df.columns = [c.strip() for c in df.columns]
        # Step 3 — sanitise for Parquet  (fixes ADF-006)
        df = sanitise_column_names(df)
        return df

    def _convert_to_parquet(self, df: pd.DataFrame) -> bytes:
        """Serialise DataFrame to Parquet bytes (Snappy compressed)."""
        table = pa.Table.from_pandas(df, preserve_index=False)
        buf   = io.BytesIO()
        pq.write_table(
            table,
            buf,
            compression=self._compression,
            row_group_size=self._row_group_size,
        )
        return buf.getvalue()

    def _infer_schema(self, df: pd.DataFrame) -> Dict[str, str]:
        """Return column → dtype mapping for the processing manifest."""
        return {col: str(dtype) for col, dtype in df.dtypes.items()}

    # ── Local mode (no Azure — for dev / CI testing) ──────────────────────────

    def run_local(
        self,
        input_folder: str = "data/csv",
        output_folder: str = "data/landing",
    ) -> Dict[str, Any]:
        """
        Run processor on local filesystem.
        Uses the same sanitise_column_names() logic as the Azure path.
        """
        os.makedirs(output_folder, exist_ok=True)
        csv_files = [
            f for f in os.listdir(input_folder) if f.lower().endswith(".csv")
        ]
        manifest = {"files_processed": 0, "files_failed": 0, "details": []}

        for fname in csv_files:
            src_path  = os.path.join(input_folder, fname)
            dst_name  = os.path.splitext(fname)[0] + ".parquet"
            dst_path  = os.path.join(output_folder, dst_name)
            start     = datetime.now(timezone.utc)

            try:
                df = pd.read_csv(src_path)

                # Step 1 — strip whitespace
                df.columns = [c.strip() for c in df.columns]
                # Step 2 — sanitise Parquet-invalid chars  (ADF-006 fix)
                df = sanitise_column_names(df)

                table = pa.Table.from_pandas(df, preserve_index=False)
                pq.write_table(table, dst_path, compression=self._compression)

                duration = (datetime.now(timezone.utc) - start).total_seconds()
                logger.info(
                    "LOCAL ✅ %s → %s  (%.1fs | %d rows | %d cols)",
                    fname, dst_name, duration, len(df), len(df.columns),
                )
                manifest["files_processed"] += 1
                manifest["details"].append({
                    "status": "success",
                    "source": src_path,
                    "destination": dst_path,
                    "rows": len(df),
                    "columns": len(df.columns),
                    "duration_seconds": duration,
                })

            except Exception as exc:
                logger.error("LOCAL ❌ %s: %s", fname, exc)
                manifest["files_failed"] += 1
                manifest["details"].append({
                    "status": "failed",
                    "source": src_path,
                    "error": str(exc),
                })

        return manifest
