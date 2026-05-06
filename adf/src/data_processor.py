"""
=======================================================
  CSV → Parquet Data Processor

  Key methods:
    run()                    — full CSV→Parquet pipeline
    fix_source_csv_columns() — called by ADF-006 resolver:
                               downloads every CSV, renames
                               forbidden columns, re-uploads
                               so ADF can read clean headers
    sanitise_column_names()  — static utility (reusable)
=======================================================
"""

import io
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from azure.storage.blob import BlobServiceClient, ContainerClient

from src.logger import get_logger
from config.settings import AZURE_CONFIG, PIPELINE_CONFIG

logger = get_logger(__name__)

# Characters forbidden in Parquet column names
_PARQUET_FORBIDDEN = re.compile(r'[,;{}\(\)\n\t= ]+')


class CsvToParquetProcessor:

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

    # ── Public: full pipeline ─────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Run the full CSV → Parquet conversion for all files in source folder."""
        logger.info("📂 Starting CSV → Parquet processor")
        csv_files = self._list_csv_files()

        if not csv_files:
            logger.warning("⚠️  No CSV files found in %s/%s",
                           self._source_container, self._source_folder)
            return {"files_processed": 0, "files_failed": 0, "details": []}

        logger.info("Found %d CSV file(s)", len(csv_files))
        manifest = {"files_processed": 0, "files_failed": 0, "details": []}

        for blob_name in csv_files:
            result = self._process_single_file(blob_name)
            manifest["details"].append(result)
            if result["status"] == "success":
                manifest["files_processed"] += 1
            else:
                manifest["files_failed"] += 1

        logger.info("✅ Done — processed: %d | failed: %d",
                    manifest["files_processed"], manifest["files_failed"])
        return manifest

    # ── Public: ADF-006 fix ───────────────────────────────────────────────────

    def fix_source_csv_columns(self) -> Dict[str, Any]:
        """
        Called by the ADF-006 resolver BEFORE the pipeline is restarted.

        For every CSV file in source_container/source_folder:
          1. Download the CSV
          2. Detect column names that contain Parquet-forbidden chars
          3. Rename those columns using sanitise_column_names()
          4. Re-upload the fixed CSV in place (overwrite=True)

        After this, when ADF restarts and reads the CSV it will find
        clean column names and the ParquetInvalidColumnName error will
        not occur again.

        Returns:
            {
              "fixed":   [list of blob names that were changed],
              "skipped": [list of blob names already clean],
              "failed":  [list of blob names that errored],
              "success": bool  — True when all files processed without error
            }
        """
        logger.info("🔧 [ADF-006] Sanitising CSV column names in source folder ...")
        csv_files = self._list_csv_files()

        if not csv_files:
            logger.warning("  No CSV files found in %s/%s",
                           self._source_container, self._source_folder)
            return {"fixed": [], "skipped": [], "failed": [], "success": False}

        fixed:   List[str] = []
        skipped: List[str] = []
        failed:  List[str] = []

        for blob_name in csv_files:
            try:
                # Download
                logger.info("  ⬇️  Downloading: %s", blob_name)
                raw_bytes = self._download_blob(self._source_container, blob_name)
                df        = pd.read_csv(io.BytesIO(raw_bytes), low_memory=False)

                original_cols = list(df.columns)
                clean_cols    = self.sanitise_column_names(original_cols)

                # Find what changed
                changes = [
                    (orig, clean)
                    for orig, clean in zip(original_cols, clean_cols)
                    if orig != clean
                ]

                if not changes:
                    logger.info("  ✅ All column names already clean: %s", blob_name)
                    skipped.append(blob_name)
                    continue

                # Apply renames
                df.columns = clean_cols
                logger.info("  🔄 Renamed %d column(s) in %s:", len(changes), blob_name)
                for orig, clean in changes:
                    logger.info("       %-40s →  %s", f"'{orig}'", f"'{clean}'")

                # Re-upload in place
                new_bytes = df.to_csv(index=False).encode("utf-8")
                logger.info("  ⬆️  Re-uploading (overwrite): %s", blob_name)
                self._upload_blob(self._source_container, blob_name, new_bytes)

                fixed.append(blob_name)
                logger.info("  ✅ Fixed and saved: %s", blob_name)

            except Exception as exc:
                logger.error("  ❌ Failed to fix %s: %s", blob_name, exc)
                failed.append(blob_name)

        success = len(failed) == 0 and (len(fixed) > 0 or len(skipped) > 0)
        logger.info(
            "  Column fix complete — fixed: %d | skipped (already clean): %d | failed: %d",
            len(fixed), len(skipped), len(failed),
        )
        return {"fixed": fixed, "skipped": skipped, "failed": failed, "success": success}

    # ── Static utility ────────────────────────────────────────────────────────

    @staticmethod
    def sanitise_column_names(columns: List[str]) -> List[str]:
        """
        Return Parquet-safe column names.

        Rules:
          1. Replace runs of forbidden chars [,;{}()\\n\\t= ] with '_'
          2. Strip leading/trailing underscores
          3. Collapse multiple underscores to one
          4. Fallback to col_<index> for empty names
          5. Deduplicate: append _2, _3 … if collision

        Example:
          "amount(total)"   →  "amount_total"
          "row,id"          →  "row_id"
          "col\nname"       →  "col_name"
          "value = sum"     →  "value_sum"
        """
        seen:   Dict[str, int] = {}
        result: List[str]      = []

        for i, col in enumerate(columns):
            clean = _PARQUET_FORBIDDEN.sub("_", str(col))
            clean = clean.strip("_").strip()
            clean = re.sub(r"_+", "_", clean)

            if not clean:
                clean = f"col_{i}"

            if clean in seen:
                seen[clean] += 1
                clean = f"{clean}_{seen[clean]}"
            else:
                seen[clean] = 1

            result.append(clean)

        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _list_csv_files(self) -> List[str]:
        container: ContainerClient = self._client.get_container_client(
            self._source_container
        )
        prefix = f"{self._source_folder}/" if self._source_folder else ""
        return [
            b.name
            for b in container.list_blobs(name_starts_with=prefix)
            if b.name.lower().endswith(".csv")
        ]

    def _process_single_file(self, blob_name: str) -> Dict[str, Any]:
        start             = datetime.utcnow()
        base_name         = os.path.splitext(os.path.basename(blob_name))[0]
        parquet_blob_name = f"{self._landing_folder}/{base_name}.parquet"

        try:
            logger.info("  ⬇️  Downloading: %s", blob_name)
            raw_bytes = self._download_blob(self._source_container, blob_name)

            df = pd.read_csv(io.BytesIO(raw_bytes), low_memory=False)
            # Always sanitise column names during normal processing too
            df.columns = self.sanitise_column_names(list(df.columns))

            logger.info("  🔄 Converting to Parquet (%d rows) ...", len(df))
            table         = pa.Table.from_pandas(df, preserve_index=False)
            buf           = io.BytesIO()
            pq.write_table(table, buf,
                           compression=self._compression,
                           row_group_size=self._row_group_size)
            parquet_bytes = buf.getvalue()

            logger.info("  ⬆️  Uploading: %s", parquet_blob_name)
            self._upload_blob(self._landing_container, parquet_blob_name, parquet_bytes)

            duration = (datetime.utcnow() - start).total_seconds()
            logger.info("  ✅ %s → %s (%.1fs, %d rows, %d cols)",
                        blob_name, parquet_blob_name,
                        duration, df.shape[0], df.shape[1])
            return {
                "status": "success", "source": blob_name,
                "destination": parquet_blob_name,
                "rows": df.shape[0], "columns": df.shape[1],
                "duration_seconds": duration,
            }

        except Exception as exc:
            logger.error("  ❌ Failed: %s — %s", blob_name, exc)
            return {
                "status": "failed", "source": blob_name,
                "destination": parquet_blob_name, "error": str(exc),
                "duration_seconds": (datetime.utcnow() - start).total_seconds(),
            }

    def _download_blob(self, container: str, blob_name: str) -> bytes:
        return (
            self._client.get_blob_client(container=container, blob=blob_name)
            .download_blob()
            .readall()
        )

    def _upload_blob(self, container: str, blob_name: str, data: bytes) -> None:
        self._client.get_blob_client(container=container, blob=blob_name).upload_blob(
            data, overwrite=True
        )
