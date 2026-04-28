# System Architecture Overview

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                     FRONTEND LAYER                              │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ Generate     │  │ Reporting    │  │ Admin Config       │   │
│  │ Plan (UI)    │  │ Dashboard    │  │ (Polling/Notif)    │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘   │
│         │                 │                    │                │
│         └─────────────────┼────────────────────┘                │
│                           │ (Streamlit)                         │
│                    HTTP Requests/Responses                      │
└─────────────────────────────────────┬──────────────────────────┘
                                      │
┌─────────────────────────────────────┴──────────────────────────┐
│                                                                 │
│                     API LAYER (FastAPI)                         │
│                                                                 │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ /self-heal     │  │ /admin/*     │  │ /reporting/*     │   │
│  │ /approve       │  │ Polling      │  │ Metrics          │   │
│  │ /integrations  │  │ Notifications│  │ History/Summary  │   │
│  │ /health        │  │              │  │                  │   │
│  └────────────────┘  └──────────────┘  └──────────────────┘   │
│                                                                 │
└─────────────────────────────────────┬──────────────────────────┘
                                      │
┌─────────────────────────────────────┴──────────────────────────┐
│                                                                 │
│                  ORCHESTRATION LAYER                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │           LangGraph Healing Workflow                      │  │
│  │  Classify → Match SOPs → Generate Plan → Evaluate Safety │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │        APScheduler Polling Orchestrator                   │  │
│  │  (Manages background polling jobs, 1 per enabled instance)│  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │        Auto-Remediation Executor                          │  │
│  │  (Decides auto-execute vs pending based on safety gates)  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │        Email Notification Service                         │  │
│  │  (Failures-only: sends when remediation attempted+failed) │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────┬──────────────────────────┘
                                      │
┌─────────────────────────────────────┴──────────────────────────┐
│                                                                 │
│               POLLING LAYER (Platform-Specific)                │
│                                                                 │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────┐    │
│  │ CloudWatch   │  │ Log Analytics │  │ Cloud Logging   │    │
│  │ Poller       │  │ Poller        │  │ Poller          │    │
│  │ (AWS Glue)   │  │ (Azure)       │  │ (GCP Composer)  │    │
│  └──────────────┘  └───────────────┘  └─────────────────┘    │
│                                                                 │
│  ┌──────────────┐  ┌───────────────────────────────────────┐  │
│  │ Airflow      │  │ Platform-specific Auth Modules        │  │
│  │ Logs Poller  │  │ - aws_auth.py                         │  │
│  │ (3 variants) │  │ - azure_auth.py                       │  │
│  └──────────────┘  │ - gcp_auth.py                         │  │
│                    │ - airflow_client.py                   │  │
│                    └───────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────┬──────────────────────────┘
                                      │
┌─────────────────────────────────────┴──────────────────────────┐
│                                                                 │
│              PERSISTENCE LAYER (SQLite)                         │
│                                                                 │
│  ┌────────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ remediations   │  │ polling_cfg  │  │ notification_cfg│   │
│  │ table          │  │ table        │  │ table           │   │
│  │ (audit log)    │  │ (settings)   │  │ (recipients)    │   │
│  └────────────────┘  └──────────────┘  └─────────────────┘   │
│                                                                 │
│  data/remediation.db                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────┴──────────────────────────┐
│                                                                 │
│              EXTERNAL SYSTEMS (Configured)                      │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Cloud        │  │ SMTP Server  │  │ Encrypted Config │   │
│  │ Platforms    │  │ (Email)      │  │ (integrations.enc)  │   │
│  │ (AWS/Azure   │  │              │  │                  │   │
│  │  /GCP)       │  │              │  │                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Component Interactions

### 1. Request Flow: Generate Plan (Manual)

```
User
  ↓
[Streamlit] "Generate Plan" tab
  ↓
  + User selects platform, instance, enters run_id
  + POSTs to /self-heal endpoint
  ↓
[FastAPI] /self-heal endpoint
  ↓
  + Loads instance config from encrypted integrations.enc
  + Creates FailureEvent object
  + Invokes healing workflow
  ↓
[LangGraph] Healing Workflow
  ├─→ collect → get logs from integration
  ├─→ classify → determine error type + confidence
  ├─→ match_sops → find matching SOP from knowledge base
  ├─→ generate → create RemediationPlan (SOP or LLM fallback)
  ├─→ evaluate_safety → check allowlist + risk level
  └─→ execute (if LOW risk + allowlisted)
  ↓
[Backend] execute_plan()
  ├─→ Look up executor for platform
  ├─→ Instantiate executor with auth from config
  ├─→ Execute remediation steps
  └─→ Validate success
  ↓
[FastAPI] Returns SelfHealResult
  ├─→ classification: error type + confidence
  ├─→ plan: steps + safety eval
  ├─→ safety: allowlisted flag, risk level
  ├─→ executed: true if plan was run
  └─→ execution_result: platform-specific output
  ↓
[Streamlit] Displays plan, shows "Execute" button if LOW risk + allowlisted
  ↓
User clicks [Execute] or [Approve] button
  ↓
/approve endpoint called
  ↓
Plan executed (if not already executed)
  ↓
Result displayed to user
```

### 2. Auto-Healing Flow: Polling → Detection → Remediation

```
[APScheduler] (Background, every 5 min per instance)
  ↓
[PollingOrchestrator._poll_instance()]
  ├─→ Load instance config from integrations.enc
  ├─→ Get appropriate Poller for platform
  ├─→ Query logs from last 10 minutes
  │
  ├─→ AWS Glue? → CloudWatchPoller.get_logs()
  ├─→ Azure? → AzureLogAnalyticsPoller.get_logs()
  ├─→ GCP? → GcpCloudLoggingPoller.get_logs()
  ├─→ Airflow? → AirflowLogsPoller.get_logs()
  │
  ├─→ Parse logs (apply platform-specific auth)
  │   ├─→ AWS: use aws_auth.get_aws_client(auth_config)
  │   ├─→ Azure: use azure_auth.get_azure_credential(auth_config)
  │   ├─→ GCP: use gcp_auth.get_gcp_logging_client(auth_config)
  │   └─→ Airflow: use airflow_client.build_airflow_client(auth_config)
  │
  ├─→ Poller.detect_failures(logs)
  │   ├─→ Pattern match against FAILURE_PATTERNS
  │   ├─→ Extract run_id, error message
  │   └─→ Return {failure_signature, error_text, run_id}
  │
  └─→ If failure detected:
      ↓
      [Executor] execute_auto_remediation()
        ├─→ Load polling_config from database
        ├─→ Build graph and invoke workflow (same as manual flow)
        ├─→ Get safety evaluation
        │
        ├─→ Decision: Auto-execute?
        │   ├─→ LOW risk + allowlisted + auto_execute_low_risk flag?
        │   │   ├─→ YES: Execute immediately
        │   │   └─→ NO: Store as PENDING
        │   └─→ HIGH/MED risk or not allowlisted?
        │       └─→ Store as PENDING
        │
        ├─→ Store result in remediations table
        │   ├─→ execution_status: SUCCESS, FAILED, or PENDING
        │   ├─→ is_still_failing: true if attempted but not resolved
        │   ├─→ execution_result: platform-specific output
        │   └─→ created_at timestamp
        │
        └─→ If is_still_failing=true and email recipients configured:
            ↓
            [Notifications] send_failure_email()
              ├─→ Load notification_config from database
              ├─→ Get recipients list
              ├─→ Format HTML email with remediation details
              ├─→ Connect to SMTP server (env vars)
              └─→ Send email notification
      ↓
      update_poll_timestamp()
        └─→ Record last_polled_at in polling_config

Next polling cycle (in 5 min or configured interval):
  ↓
Repeat from top
```

### 3. Admin Configuration Flow

```
[Streamlit] Admin Configuration tab
  ↓
User selects:
  ├─→ Platform + Instance
  ├─→ Poll Interval (seconds)
  ├─→ Auto-execute flag
  └─→ Optional log filter
  ↓
User clicks [Save Polling Config]
  ↓
POST /admin/polling/{platform}/{instance}
  ├─→ Validate inputs
  ├─→ Check if config exists (UPDATE vs INSERT)
  └─→ Store in SQLite polling_config table
  ↓
Backend immediately:
  ├─→ PollingOrchestrator detects change
  ├─→ Reschedules APScheduler job with new interval
  └─→ Polling begins (or resumes with new settings)
  ↓
Configuration saved. Next polling cycle uses new settings.

Separately: User configures Email Recipients
  ↓
POST /admin/notifications/{platform}/{instance}
  ├─→ Validate email list
  └─→ Store in SQLite notification_config table
  ↓
Going forward: Failures trigger emails to configured recipients
```

### 4. Reporting Flow

```
[Streamlit] Reporting tab
  ├─→ User selects period: 24h / 7d / 30d
  ├─→ Optional platform filter
  └─→ Clicks to load
  ↓
[FastAPI] GET /reporting/metrics?period=24h&platform=&instance=
  ├─→ Query SQLite: 
  │   SELECT COUNT(*), SUM(success), SUM(still_failing)
  │   FROM remediations
  │   WHERE created_at >= NOW() - INTERVAL
  ├─→ Calculate success_rate = (success / total) * 100
  └─→ Return JSON metrics
  ↓
[FastAPI] GET /reporting/history?limit=100&offset=0
  ├─→ Query SQLite:
  │   SELECT * FROM remediations
  │   ORDER BY created_at DESC
  │   LIMIT 100
  ├─→ Parse JSON fields for display
  └─→ Return with pagination
  ↓
[Streamlit] Renders:
  ├─→ 4 metric cards (total, success rate, success count, still failing)
  ├─→ Remediation history table with columns:
  │   ├─→ Created
  │   ├─→ Platform
  │   ├─→ Instance
  │   ├─→ Failure Signature
  │   ├─→ Status (SUCCESS, FAILED, PENDING)
  │   ├─→ Still Failing (✅ / ❌)
  │   └─→ Source (SOP or LLM)
  └─→ User can click rows to expand details
```

---

## Data Model

### remediations Table
```
id                  (TEXT PRIMARY KEY) - UUID
platform            (TEXT NOT NULL) - aws_glue, azure_data_factory, etc.
instance_name       (TEXT NOT NULL) - prod-glue, prod-adf, etc.
run_id              (TEXT NOT NULL) - unique run identifier
failure_signature   (TEXT) - "OutOfMemory", "Timeout", "AccessDenied", etc.
classification      (TEXT/JSON) - error analysis from graph
plan_source         (TEXT) - "SOP" or "LLM"
plan                (TEXT/JSON) - full RemediationPlan with steps
safety_eval         (TEXT/JSON) - {allowlisted, risk, violations}
executed            (BOOLEAN) - was plan executed?
execution_status    (TEXT) - "PENDING", "SUCCESS", "FAILED"
execution_result    (TEXT/JSON) - platform-specific outcome
detected_at         (TIMESTAMP) - when failure was detected
created_at          (TIMESTAMP) - record creation time
updated_at          (TIMESTAMP) - last update time
email_sent          (BOOLEAN) - was email notification sent?
email_recipients    (TEXT/JSON) - ["ops@company.com", ...]
is_still_failing    (BOOLEAN) - was issue still failing after remediation?
notes               (TEXT) - free-form notes
UNIQUE(platform, instance_name, run_id)
```

### polling_config Table
```
id                  (TEXT PRIMARY KEY) - UUID
platform            (TEXT NOT NULL)
instance_name       (TEXT NOT NULL)
enabled             (BOOLEAN DEFAULT 1) - polling enabled?
poll_interval_seconds (INTEGER DEFAULT 300) - how often to poll
auto_execute_low_risk (BOOLEAN DEFAULT 0) - auto-execute LOW-risk?
last_polled_at      (TIMESTAMP) - last successful poll
log_filter          (TEXT) - optional log filter regex
created_at          (TIMESTAMP)
updated_at          (TIMESTAMP)
UNIQUE(platform, instance_name)
```

### notification_config Table
```
id                  (TEXT PRIMARY KEY) - UUID
platform            (TEXT NOT NULL)
instance_name       (TEXT NOT NULL)
recipients          (TEXT/JSON) - ["user1@company.com", "user2@company.com"]
enabled             (BOOLEAN DEFAULT 1) - send emails?
created_at          (TIMESTAMP)
updated_at          (TIMESTAMP)
UNIQUE(platform, instance_name)
```

---

## Authentication & Credential Management

### Credential Flow

```
config/integrations.enc
  (Encrypted YAML with auth details)
  ├─→ AWS Glue
  │   ├─→ auth.method: default | profile | assume_role | explicit
  │   ├─→ auth.region: us-east-1
  │   └─→ auth.role_arn, profile_name, access_key_id, etc.
  │
  ├─→ Azure Data Factory
  │   ├─→ auth.method: default | managed_identity | client_secret
  │   ├─→ auth.tenant_id, client_id, client_secret
  │   └─→ subscription_id, meta.resource_group
  │
  ├─→ GCP Cloud Composer
  │   ├─→ auth.method: default | service_account_key
  │   ├─→ auth.key_path: /secrets/gcp-key.json
  │   └─→ project_id, meta.composer_environment
  │
  └─→ Airflow
      ├─→ variant: self_hosted | mwaa | composer
      ├─→ auth.method: jwt_password | aws_invoke_rest_api | iap_oidc
      ├─→ auth.username, password (for jwt_password)
      ├─→ auth.audience (for iap_oidc)
      └─→ region, environment_name (for mwaa)
  ↓
On API startup: load_config() decrypts using SELF_HEALING_MASTER_KEY
  ↓
When polling/executing: Use appropriate auth factory
  ├─→ aws_auth.get_aws_client(service, instance, **kwargs)
  ├─→ azure_auth.get_azure_credential(auth_config)
  ├─→ gcp_auth.get_gcp_logging_client(instance)
  └─→ airflow_client.build_airflow_client(instance)
  ↓
Auth factories return authenticated client/credential
  ↓
Client used for platform-specific API calls
  ├─→ boto3 for AWS
  ├─→ Azure SDK for Azure
  ├─→ google-cloud for GCP
  ├─→ REST API for Airflow
  └─→ Results used for remediation execution
```

---

## Deployment Architecture

### Minimal Deployment (Single Server)

```
┌─────────────────────────────────────┐
│ Single Server Instance              │
│ ┌─────────────────────────────────┐ │
│ │ FastAPI Backend                 │ │
│ │ + Streamlit Frontend (same port)│ │
│ │ + SQLite DB (local filesystem)  │ │
│ │ + APScheduler (in-process)      │ │
│ └─────────────────────────────────┘ │
│            ↓                         │
│ ┌─────────────────────────────────┐ │
│ │ Cloud APIs (AWS/Azure/GCP)      │ │
│ │ (logs polling + remediation)    │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

### Production Deployment (Distributed)

```
┌────────────────────────────────────────────────────────────┐
│ API Layer                                                  │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│ │ API Server 1 │  │ API Server 2 │  │ API Server N     │  │
│ │ (FastAPI)    │  │ (FastAPI)    │  │ (FastAPI)        │  │
│ └──────┬───────┘  └──────┬───────┘  └──────┬──────────┘  │
│        │                 │                  │             │
└────────┼─────────────────┼──────────────────┼─────────────┘
         │                 │                  │
      ┌──┴─────────────────┴──────────────────┴──┐
      │                                          │
      ↓                                          ↓
┌──────────────────┐              ┌──────────────────────┐
│ PostgreSQL DB    │              │ Streaming/Message    │
│ (remediations +  │              │ Queue (Celery)       │
│ configs + audit) │              │ (async jobs)         │
└──────────────────┘              └──────────────────────┘
                                           ↓
┌────────────────────────────────────────────────────────────┐
│ Worker Layer (Polling & Notifications)                     │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│ │ Polling      │  │ Polling      │  │ Email/Notif      │  │
│ │ Worker 1     │  │ Worker 2     │  │ Worker           │  │
│ │ (APScheduler)│  │ (APScheduler)│  │                  │  │
│ └──────────────┘  └──────────────┘  └──────────────────┘  │
└────────────────────────────────────────────────────────────┘
         │                                      │
         └──────────────┬───────────────────────┘
                        ↓
┌────────────────────────────────────────────────────────────┐
│ Cloud APIs (AWS/Azure/GCP)                                 │
│ ├─ CloudWatch Logs                                         │
│ ├─ Log Analytics                                           │
│ ├─ Cloud Logging                                           │
│ ├─ Airflow APIs                                            │
│ └─ External SMTP (email)                                   │
└────────────────────────────────────────────────────────────┘

Frontend: Separate Streamlit instance or same servers
```

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **API** | FastAPI 0.115.2 | HTTP REST endpoints |
| **Web Server** | Uvicorn 0.32.0 | ASGI server |
| **Frontend** | Streamlit 1.39.0 | Web UI for reporting & config |
| **Workflow** | LangGraph 0.1.11 | Healing workflow orchestration |
| **Database** | SQLite 3 | Persistence (embeddings, config, audit) |
| **Task Scheduler** | APScheduler 3.10.4 | Background polling jobs |
| **Data Validation** | Pydantic 2.9.2 | Request/response models |
| **Cloud SDKs** | boto3, azure-sdk, google-auth | Platform-specific APIs |
| **Encryption** | cryptography 43.0.1 | Encrypt config files |
| **Testing** | pytest 7.4.4 | Test suite |

---

## Security Considerations

1. **Credential Encryption**: All credentials stored in encrypted `config/integrations.enc`
2. **Admin API Key**: Simple shared secret for `/admin` endpoints (upgrade to JWT/OIDC)
3. **Database**: SQLite on local filesystem (upgrade to PostgreSQL with backups for production)
4. **Email**: SMTP via env vars, no credentials in code
5. **Audit Trail**: All remediation attempts logged with timestamps and outcomes
6. **Least Privilege**: Create dedicated IAM roles/service accounts per platform with minimal permissions
7. **Secrets Management**: Use cloud secret managers (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager)

---

## Scalability & Performance

- **Polling**: APScheduler handles 100+ instances polling concurrently
- **Database**: SQLite suitable up to ~1M remediations; use PostgreSQL for larger deployments
- **API**: FastAPI can handle 1000+ requests/sec on single server
- **Notifications**: SMTP rate-limiting depends on mail provider
- **Memory**: ~200MB base + 50MB per 100 instances being polled

---

## Monitoring & Logging

```
Application Logs:
  - uvicorn: API server logs
  - streamlit: Frontend logs
  - APScheduler: Polling job execution
  - backend.polling: Failure detection logs
  - backend.notifications: Email sending logs

Database Logs:
  sqlite3 data/remediation.db ".mode column" "SELECT * FROM remediations LIMIT 10;"

Metrics:
  - /reporting/metrics - real-time remediation metrics
  - /reporting/history - audit log
  - /reporting/summary - cross-period comparison
```

---

**Status**: Ready for deployment and operation.
