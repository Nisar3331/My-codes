# Use Cases & UI Implementation

This document describes how the two primary use cases are implemented and addressed by the system.

---

## Use Case 1: Reporting Dashboard (For Operations/Analytics Teams)

### Requirements
- Show what actions the system took
- Report total remediation actions taken
- Report how many issues got fixed
- Report how many issues are still not fixed
- Historical trend analysis

### Implementation

#### Backend Support
**API Endpoints** (`backend/api/reporting.py`):

- `GET /reporting/metrics?period=24h|7d|30d&platform=&instance=`
  - Returns: `{total_attempts, success_count, failure_count, success_rate}`
  - Filters by platform/instance if needed
  - Multiple time periods supported

- `GET /reporting/history?platform=&instance=&limit=100&offset=0`
  - Returns: Full audit log with pagination
  - Shows: created_at, platform, instance_name, failure_signature, execution_status, is_still_failing
  - JSON fields parsed for readability

- `GET /reporting/summary?platform=&instance=`
  - Returns: Metrics across all time periods (24h, 7d, 30d) at once

**Database Storage** (`backend/persistence/db.py`):
- `remediations` table tracks every remediation attempt
- Fields: failure_signature, classification, plan, execution_status, is_still_failing, execution_result
- Supports full audit trail with timestamps

#### Frontend UI
**Page: "Reporting"** (`frontend/reporting.py`):

1. **Metric Cards** (top dashboard):
   ```
   ┌─────────────────┬──────────────────┬──────────────┬────────────────┐
   │ Total Attempted │  Success Rate %  │  Successful  │  Still Failing │
   │       87        │      87.4%       │      76      │       11       │
   └─────────────────┴──────────────────┴──────────────┴────────────────┘
   ```
   - **Total Attempted**: All remediations (successful + failed + pending)
   - **Success Rate**: % of successful vs total
   - **Successful**: Count of fixed issues
   - **Still Failing**: Count of attempted but unresolved issues

2. **Period Selector**: 24h / 7d / 30d
   - Switch between time windows to see trends

3. **Platform Filter**: View metrics per platform or all platforms
   - Helps identify which platforms have more failures

4. **Remediation History Table**:
   ```
   | Created     | Platform | Instance | Failure Type | Status  | Still? | Source |
   |─────────────┼──────────┼──────────┼──────────────┼─────────┼────────┼────────|
   | 2026-04-26  | aws_glue | prod     | OutOfMemory  | SUCCESS | ❌     | SOP    |
   | 2026-04-26  | azure_adf| staging  | Timeout      | FAILED  | ✅     | LLM    |
   | 2026-04-26  | gcp      | prod     | AccessDenied| PENDING | ✅     | SOP    |
   ```
   - Full audit trail with sortable columns
   - Click to expand for details (classification, plan, execution result)

5. **Export Options** (future):
   - CSV export for analysis
   - Webhook integration for alerts

#### Usage Scenario

**Scenario: Weekly Operations Review**

```
1. Head of Operations opens http://localhost:8501/reporting
2. Selects "7d" period to see weekly trends
3. Views metrics:
   - 157 total remediations attempted
   - 91.1% success rate (143 successful, 14 still failing)
   - Identifies "azure_data_factory" as highest failure platform

4. Filters by "azure_data_factory" to investigate:
   - 32 total attempts, 78% success rate, 7 still failing
   - Sees failed types: "CredentialExpired", "NetworkTimeout", "StorageQuota"

5. Exports history to CSV for dashboard/reporting
6. Presents findings to management:
   - "System fixed 143 issues autonomously this week"
   - "14 issues required manual intervention"
   - "Azure Data Factory needs credential rotation (7 incidents)"

7. Action items:
   - Update SOPs for credential rotation
   - Increase allowlist for network recovery actions
   - Investigate storage quota pattern
```

---

## Use Case 2: Admin Configuration (For Platform/Ops Engineers)

### Requirements
- Admin configures platform settings
- Platform-specific polling (CloudWatch for AWS, Log Analytics for Azure, etc.)
- Agent polls every 5 minutes
- Agent automatically performs necessary setup

### Implementation

#### Backend Support
**API Endpoints** (`backend/api/admin.py`):

Polling Configuration:
- `POST /admin/polling/{platform}/{instance}` - Create/update polling config
- `GET /admin/polling/{platform}/{instance}` - Get specific config
- `PATCH /admin/polling/{platform}/{instance}` - Update config
- `GET /admin/polling` - List all configs

Notification Configuration:
- `POST /admin/notifications/{platform}/{instance}` - Set email recipients
- `GET /admin/notifications/{platform}/{instance}` - Get recipients
- `PATCH /admin/notifications/{platform}/{instance}` - Update recipients

**Request Format**:
```json
{
  "poll_interval_seconds": 300,           // 5-60 minutes
  "auto_execute_low_risk": true,          // Auto-execute LOW risk plans?
  "log_filter": "ERROR"                   // Optional: filter logs
}
```

**Database Storage** (`backend/persistence/db.py`):
- `polling_config` table: stores per-instance polling settings
- `notification_config` table: stores email recipients per instance
- Loaded on startup, can be updated dynamically

**Orchestration** (`backend/polling/orchestrator.py`):
- Loads all enabled polling configs on startup
- Schedules APScheduler jobs per instance with configurable intervals
- Each job polls platform-specific logs every N seconds
- Automatically invokes auto-remediation when failures detected

#### Frontend UI
**Page: "Admin Configuration"** (`frontend/reporting.py`):

**Tab 1: Polling Configuration**

```
┌─────────────────────────────────────────────────────────┐
│ Configure polling for instances                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Platform: [aws_glue                    ▼]              │
│ Instance Name: [prod-glue              ]              │
│                                                         │
│                    [Load Config]                        │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Create/Update Polling Config                            │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Poll Interval (seconds): [300]    [60 - 3600]         │
│                                                         │
│ Auto-execute LOW-risk plans? ☐                         │
│                                                         │
│ Log Filter (optional): [ERROR]                         │
│                                                         │
│                    [Save Polling Config]                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Fields:
- **Platform**: Select from aws_glue, azure_data_factory, azure_databricks, gcp_composer, airflow
- **Instance Name**: Enter the instance name to poll
- **Poll Interval**: 60-3600 seconds (default: 300 = 5 minutes)
- **Auto-execute LOW-risk**: Toggle whether to automatically execute LOW-risk + allowlisted plans
- **Log Filter**: Optional regex/keyword to filter logs before processing

**Tab 2: Notification Configuration**

```
┌─────────────────────────────────────────────────────────┐
│ Configure email notifications (failures-only)           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Platform: [aws_glue                    ▼]              │
│ Instance Name: [prod-glue              ]              │
│                                                         │
│                    [Load Config]                        │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Create/Update Notification Config                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Email Recipients (comma-separated):                     │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ ops@example.com,                                    │ │
│ │ alerts@example.com,                                 │ │
│ │ oncall@example.com                                  │ │
│ └─────────────────────────────────────────────────────┘ │
│                                                         │
│                [Save Notification Config]               │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Fields:
- **Email Recipients**: Comma-separated list of email addresses
- Emails only sent when remediation attempted but issue persists (failures-only)

#### Usage Scenario

**Scenario: Enabling Auto-Healing for Production Glue Cluster**

```
1. Ops Engineer opens http://localhost:8501
2. Navigates to "Admin Configuration" tab
3. Clicks "Polling Configuration" sub-tab

4. Configures polling:
   - Platform: "aws_glue"
   - Instance Name: "prod-glue"
   - Poll Interval: 300 seconds (5 minutes)
   - Auto-execute LOW-risk: ✓ (checked)
   - Log Filter: "" (empty, get all logs)
   - Clicks [Save Polling Config]

5. Switches to "Notification Configuration" sub-tab

6. Configures notifications:
   - Platform: "aws_glue"
   - Instance Name: "prod-glue"
   - Email Recipients: "ops@company.com, oncall@company.com"
   - Clicks [Save Notification Config]

7. Backend immediately:
   - Stores config in SQLite database
   - Scheduling event: APScheduler creates new job
   - Starts polling CloudWatch Logs for this instance every 5 minutes

8. Result:
   ✅ Polls start immediately (every 5 min)
   ✅ Detects failures in real-time
   ✅ Auto-executes LOW-risk fixes
   ✅ Emails only if fix fails (not on success)
   ✅ Ops team sees metrics in Reporting dashboard
```

**Scenario: Adjusting for Noisy Staging Environment**

```
1. Ops Engineer notices too many "still failing" items for staging
2. Loads existing config for "azure_data_factory" staging instance
3. Increases poll interval to 900 seconds (15 minutes) to reduce noise
4. Unchecks "Auto-execute LOW-risk" (require manual approval)
5. Adds log filter "CRITICAL" to only process high-severity items
6. Clicks [Save Polling Config]

Backend immediately:
- Updates APScheduler job to run every 15 minutes instead of 5
- Only auto-remediates HIGH-risk items (not LOW)
- Filters logs by "CRITICAL" keyword only
- Results: Fewer false positives, better signal-to-noise ratio
```

---

## Implementation Architecture

### Admin Configuration Flow

```
Streamlit UI
    ↓
POST /admin/polling/{platform}/{instance}
    ↓
Admin API validates and stores in SQLite
    ↓
PollingOrchestrator detects change (on next check)
    ↓
APScheduler re-schedules job with new interval
    ↓
Polling starts/stops based on enabled flag
    ↓
Failures detected → Auto-remediation triggered
    ↓
Results stored in remediations table
    ↓
Streamlit Reporting page shows metrics/history
```

### Platform-Specific Polling

Each platform uses dedicated log collection method:

| Platform | Log Source | Poller | Auth |
|----------|------------|--------|------|
| **AWS Glue** | CloudWatch Logs | `CloudWatchPoller` | boto3 (configurable) |
| **Azure Data Factory** | Log Analytics | `AzureLogAnalyticsPoller` | Azure Identity (configurable) |
| **Azure Databricks** | Log Analytics | `AzureLogAnalyticsPoller` | Azure Identity (configurable) |
| **GCP Cloud Composer** | Cloud Logging | `GcpCloudLoggingPoller` | GCP Auth (configurable) |
| **Airflow** | REST API / DB | `AirflowLogsPoller` | JWT / boto3 / OIDC (configurable) |

Each poller:
1. Queries platform logs from last 10 minutes
2. Applies optional log filter
3. Detects failures via regex patterns
4. Extracts run_id from log entries
5. Returns structured failure: `{failure_signature, error_text, run_id}`

### Auto-Remediation Decision Tree

```
Failure Detected
    ↓
Run Healing Workflow (classify → match SOPs → generate plan)
    ↓
Evaluate Safety (risk level, allowlist)
    ↓
Is LOW risk AND allowlisted?
    ├─→ YES: Check auto_execute_low_risk flag
    │   ├─→ YES: Execute immediately → Record result
    │   └─→ NO: Store as PENDING → Send "approval needed" email
    │
    └─→ NO: Store as PENDING → Send "approval needed" email
            (HIGH/MED risk or not allowlisted)
            ↓
            Execution Result
            ├─→ SUCCESS: execution_status = "SUCCESS", is_still_failing = false
            └─→ FAILED: execution_status = "FAILED", is_still_failing = true
                        → Send "still failing" email if recipients configured
```

---

## Configuration Management Options

### Option 1: Streamlit UI (Recommended for Ops)
- No technical knowledge required
- Visual configuration
- Real-time updates
- Can be used by non-technical staff

### Option 2: API (Recommended for Automation)
- Scriptable configuration
- CI/CD integration
- Infrastructure-as-code
- Mass configuration (100+ instances)

### Example: Bulk Configuration via API Script

```python
import requests

# Admin key
API_KEY = "your-admin-key"
API = "http://localhost:8000"

# Instances to enable
instances = [
    ("aws_glue", "prod-glue", ["ops@company.com"]),
    ("aws_glue", "staging-glue", ["staging-ops@company.com"]),
    ("azure_data_factory", "prod-adf", ["ops@company.com"]),
    ("gcp_composer", "prod-composer", ["ops@company.com"]),
]

for platform, instance_name, recipients in instances:
    # Enable polling
    polling_config = {
        "poll_interval_seconds": 300,
        "auto_execute_low_risk": False,  # Require approval
        "log_filter": None
    }
    
    resp = requests.post(
        f"{API}/admin/polling/{platform}/{instance_name}",
        json=polling_config,
        headers={"x-token": API_KEY}
    )
    print(f"Polling {platform}/{instance_name}: {resp.json()['status']}")
    
    # Configure notifications
    notif_config = {"recipients": recipients}
    
    resp = requests.post(
        f"{API}/admin/notifications/{platform}/{instance_name}",
        json=notif_config,
        headers={"x-token": API_KEY}
    )
    print(f"Notifications {platform}/{instance_name}: {resp.json()['status']}")

print("✅ All instances configured")
```

---

## Summary

### Reporting Dashboard (Use Case 1)
- **What**: Shows what system did, how many fixed, how many still failing
- **Where**: Streamlit "Reporting" tab, API `/reporting/metrics` + `/reporting/history`
- **Who**: Operations, Analytics, Management
- **Benefits**: Real-time visibility, trend analysis, identification of problem areas

### Admin Configuration (Use Case 2)
- **What**: Configure polling per platform/instance, enable auto-execution, set email recipients
- **Where**: Streamlit "Admin Configuration" tab, API `/admin/polling` + `/admin/notifications`
- **Who**: Platform Engineers, Ops Leads, Infrastructure Team
- **Benefits**: No code changes, dynamic reconfiguration, per-instance control, audit trail

Both use cases are fully implemented and integrated with the backend, database, and API layer.
