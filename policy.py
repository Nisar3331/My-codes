import sqlite3
import os
import json
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

DB_PATH = Path(os.environ.get("REMEDIATION_DB_PATH", "data/remediation.db"))

def init_db():
    """Initialize database schema on startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Remediations table (audit log of all remediation attempts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS remediations (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            instance_name TEXT NOT NULL,
            run_id TEXT NOT NULL,
            failure_signature TEXT,
            classification TEXT,
            plan_source TEXT,
            plan TEXT NOT NULL,
            safety_eval TEXT NOT NULL,
            executed BOOLEAN DEFAULT 0,
            execution_status TEXT,
            execution_result TEXT,
            detected_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            email_sent BOOLEAN DEFAULT 0,
            email_recipients TEXT,
            is_still_failing BOOLEAN DEFAULT 0,
            notes TEXT,
            UNIQUE(platform, instance_name, run_id)
        )
    """)

    # Polling configuration per instance
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS polling_config (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            instance_name TEXT NOT NULL,
            enabled BOOLEAN DEFAULT 1,
            poll_interval_seconds INTEGER DEFAULT 300,
            auto_execute_low_risk BOOLEAN DEFAULT 0,
            last_polled_at TIMESTAMP,
            log_filter TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            UNIQUE(platform, instance_name)
        )
    """)

    # Email notification recipients (failures-only mode)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_config (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            instance_name TEXT NOT NULL,
            recipients TEXT,
            enabled BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            UNIQUE(platform, instance_name)
        )
    """)

    conn.commit()
    conn.close()


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Remediation queries

def create_remediation(
    platform: str,
    instance_name: str,
    run_id: str,
    failure_signature: str,
    classification: dict,
    plan: dict,
    safety_eval: dict,
    detected_at: datetime,
) -> str:
    """Store a new remediation attempt. Returns remediation_id."""
    remediation_id = str(uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO remediations (
                id, platform, instance_name, run_id, failure_signature,
                classification, plan, safety_eval, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            remediation_id,
            platform,
            instance_name,
            run_id,
            failure_signature,
            json.dumps(classification),
            json.dumps(plan),
            json.dumps(safety_eval),
            detected_at.isoformat(),
        ))
    return remediation_id


def get_remediation(remediation_id: str) -> dict:
    """Retrieve a remediation by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM remediations WHERE id = ?", (remediation_id,))
        row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def update_remediation(
    remediation_id: str,
    executed: bool = None,
    execution_status: str = None,
    execution_result: dict = None,
    email_sent: bool = None,
    is_still_failing: bool = None,
    notes: str = None,
) -> None:
    """Update remediation execution status."""
    updates = []
    params = []

    if executed is not None:
        updates.append("executed = ?")
        params.append(executed)
    if execution_status is not None:
        updates.append("execution_status = ?")
        params.append(execution_status)
    if execution_result is not None:
        updates.append("execution_result = ?")
        params.append(json.dumps(execution_result))
    if email_sent is not None:
        updates.append("email_sent = ?")
        params.append(email_sent)
    if is_still_failing is not None:
        updates.append("is_still_failing = ?")
        params.append(is_still_failing)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    if not updates:
        return

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(remediation_id)

    query = f"UPDATE remediations SET {', '.join(updates)} WHERE id = ?"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)


def get_remediation_metrics(period_hours: int = 24, platform: str = None, instance_name: str = None) -> dict:
    """Calculate metrics for remediations in the last N hours."""
    query = """
        SELECT
            COUNT(*) as total_attempts,
            SUM(CASE WHEN execution_status = 'SUCCESS' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN is_still_failing = 1 THEN 1 ELSE 0 END) as failure_count
        FROM remediations
        WHERE datetime(created_at) >= datetime('now', ?)
    """
    params = [f"-{period_hours} hours"]

    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if instance_name:
        query += " AND instance_name = ?"
        params.append(instance_name)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()

    total = row["total_attempts"] or 0
    success = row["success_count"] or 0
    failure = row["failure_count"] or 0
    success_rate = (success / total * 100) if total > 0 else 0

    return {
        "total_attempts": total,
        "success_count": success,
        "failure_count": failure,
        "success_rate": round(success_rate, 2),
    }


def get_remediation_history(platform: str = None, instance_name: str = None, limit: int = 100, offset: int = 0) -> list:
    """Get audit log of remediations."""
    query = "SELECT * FROM remediations WHERE 1=1"
    params = []

    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if instance_name:
        query += " AND instance_name = ?"
        params.append(instance_name)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


# Polling config queries

def create_polling_config(
    platform: str,
    instance_name: str,
    poll_interval_seconds: int = 300,
    auto_execute_low_risk: bool = False,
    log_filter: str = None,
) -> str:
    """Create polling configuration for an instance."""
    config_id = str(uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO polling_config (
                id, platform, instance_name, poll_interval_seconds,
                auto_execute_low_risk, log_filter, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            config_id,
            platform,
            instance_name,
            poll_interval_seconds,
            auto_execute_low_risk,
            log_filter,
        ))
    return config_id


def get_polling_config(platform: str, instance_name: str) -> dict:
    """Get polling config for a specific instance."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM polling_config
            WHERE platform = ? AND instance_name = ?
        """, (platform, instance_name))
        row = cursor.fetchone()
    return dict(row) if row else None


def get_all_polling_configs() -> list:
    """Get all polling configurations."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM polling_config WHERE enabled = 1")
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def update_polling_config(
    platform: str,
    instance_name: str,
    enabled: bool = None,
    poll_interval_seconds: int = None,
    auto_execute_low_risk: bool = None,
    log_filter: str = None,
) -> None:
    """Update polling configuration."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if enabled is not None:
        updates.append("enabled = ?")
        params.append(enabled)
    if poll_interval_seconds is not None:
        updates.append("poll_interval_seconds = ?")
        params.append(poll_interval_seconds)
    if auto_execute_low_risk is not None:
        updates.append("auto_execute_low_risk = ?")
        params.append(auto_execute_low_risk)
    if log_filter is not None:
        updates.append("log_filter = ?")
        params.append(log_filter)

    if len(updates) == 1:
        return

    params.extend([platform, instance_name])
    query = f"""UPDATE polling_config SET {', '.join(updates)}
                WHERE platform = ? AND instance_name = ?"""

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)


def update_poll_timestamp(platform: str, instance_name: str) -> None:
    """Update last_polled_at timestamp."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE polling_config
            SET last_polled_at = CURRENT_TIMESTAMP
            WHERE platform = ? AND instance_name = ?
        """, (platform, instance_name))


# Notification config queries

def create_notification_config(
    platform: str,
    instance_name: str,
    recipients: list,  # List of email addresses
) -> str:
    """Create notification configuration."""
    config_id = str(uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO notification_config (
                id, platform, instance_name, recipients, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            config_id,
            platform,
            instance_name,
            json.dumps(recipients),
        ))
    return config_id


def get_notification_config(platform: str, instance_name: str) -> dict:
    """Get notification config for a specific instance."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM notification_config
            WHERE platform = ? AND instance_name = ?
        """, (platform, instance_name))
        row = cursor.fetchone()

    if not row:
        return None

    row_dict = dict(row)
    row_dict["recipients"] = json.loads(row_dict.get("recipients", "[]"))
    return row_dict


def update_notification_config(
    platform: str,
    instance_name: str,
    recipients: list = None,
    enabled: bool = None,
) -> None:
    """Update notification configuration."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if recipients is not None:
        updates.append("recipients = ?")
        params.append(json.dumps(recipients))
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(enabled)

    if len(updates) == 1:
        return

    params.extend([platform, instance_name])
    query = f"""UPDATE notification_config SET {', '.join(updates)}
                WHERE platform = ? AND instance_name = ?"""

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
