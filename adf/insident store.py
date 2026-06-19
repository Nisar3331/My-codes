"""
=======================================================
  Incident Store — prevents duplicate restarts
  Uses Azure Blob Storage to track handled incidents
  
  Key: pipelineName + errorCode + date
  If same pipeline+error seen twice in 30 min → skip
=======================================================
"""

import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
from src.logger import get_logger
from config.settings import AZURE_CONFIG

logger = get_logger(__name__)

_COOLDOWN_MINUTES = 30  # do not restart same error within 30 min


def get_incident_key(pipeline_name: str, error_code: str) -> str:
    """Create unique key for this pipeline + error combination."""
    raw = f"{pipeline_name}:{error_code}:{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')}"
    return hashlib.md5(raw.encode()).hexdigest()


def is_already_handled(pipeline_name: str, error_code: str) -> bool:
    """Return True if this incident was already handled recently."""
    try:
        from azure.storage.blob import BlobServiceClient
        conn_str = AZURE_CONFIG.storage_connection_string
        if not conn_str:
            return False

        client    = BlobServiceClient.from_connection_string(conn_str)
        container = client.get_container_client("adf-incidents")

        # Create container if not exists
        try:
            container.create_container()
        except Exception:
            pass

        key       = get_incident_key(pipeline_name, error_code)
        blob_name = f"incident_{key}.json"

        try:
            blob = container.get_blob_client(blob_name)
            data = json.loads(blob.download_blob().readall())
            handled_at = datetime.fromisoformat(data["handled_at"])
            age_minutes = (datetime.now(timezone.utc) - handled_at).total_seconds() / 60

            if age_minutes < _COOLDOWN_MINUTES:
                logger.warning(
                    "DUPLICATE — [%s] [%s] already handled %.0f min ago — skipping restart",
                    pipeline_name, error_code, age_minutes,
                )
                return True
        except Exception:
            pass  # blob does not exist yet — first time

        return False

    except Exception as exc:
        logger.warning("Incident store check failed: %s — allowing restart", exc)
        return False


def mark_as_handled(pipeline_name: str, error_code: str, run_id: str = "") -> None:
    """Mark this incident as handled to prevent duplicate restarts."""
    try:
        from azure.storage.blob import BlobServiceClient
        conn_str = AZURE_CONFIG.storage_connection_string
        if not conn_str:
            return

        client    = BlobServiceClient.from_connection_string(conn_str)
        container = client.get_container_client("adf-incidents")

        try:
            container.create_container()
        except Exception:
            pass

        key       = get_incident_key(pipeline_name, error_code)
        blob_name = f"incident_{key}.json"
        data      = {
            "pipeline_name": pipeline_name,
            "error_code":    error_code,
            "run_id":        run_id,
            "handled_at":    datetime.now(timezone.utc).isoformat(),
        }

        container.get_blob_client(blob_name).upload_blob(
            json.dumps(data), overwrite=True
        )
        logger.info("Incident recorded: [%s] [%s]", pipeline_name, error_code)

    except Exception as exc:
        logger.warning("Could not record incident: %s", exc)
