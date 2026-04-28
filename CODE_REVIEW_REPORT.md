# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Unified Self-Healing Platform** that provides automated remediation for cloud data pipeline failures across multiple orchestration platforms. The system is built around three core principles:
1. **SOP-first remediation**: YAML-based Standard Operating Procedures for known issues
2. **LLM fallback**: When SOPs don't match, generate remediation plans using Claude
3. **Safety-gated execution**: All plans are evaluated against allowlists and risk policies before execution

## Architecture

### Core Layers

**Frontend** (`frontend/app.py`)
- Streamlit web UI for submitting failure events and approving remediation plans
- Shows connected integration instances and plan recommendations
- Integrates with the FastAPI backend via HTTP

**API Layer** (`backend/api/main.py`)
- FastAPI application with three main endpoints:
  - `GET /health`: Health check
  - `GET /integrations`: Lists configured integrations and instances
  - `POST /self-heal`: Generates a remediation plan for a failure event
  - `POST /approve`: Executes an approved plan (only LOW risk + allowlisted)
- Handles secret/auth separation: returns public instances to UI, keeps auth internal for execution

**Orchestration Engine** (`backend/orchestration/graph.py`)
- LangGraph state machine that coordinates the healing workflow
- Nodes: collect → classify → match SOPs → generate plan → evaluate safety → execute
- Maintains a `HealState` with platform, instance, classification, SOP matches, and execution results
- Routes to platform-specific executors (AWS Glue, Azure Data Factory, Azure Databricks, GCP Composer, Airflow)

**Knowledge Base** (`backend/kb/`)
- **SOP Store** (`sop_store.py`): Loads YAML-based Standard Operating Procedures from `data/sops/`
- **SOP Match** (`sop_match.py`): Fuzzy/semantic matching of error signatures to SOPs using FAISS embeddings
- Default embeddings: FakeEmbeddings (stub) — plug in real embeddings (OpenAI, etc.) as needed

**Safety Layer** (`backend/safety/`)
- **Policy** (`policy.py`): Defines allowlisted actions per platform/instance
- **Checks** (`checks.py`): Evaluates remediation plans against policy (allowlist, risk level)
- Only executes LOW risk plans that match allowlisted actions

**LLM Layer** (`backend/llm/providers/stub.py`)
- Stub LLM provider for plan generation when SOPs don't match
- Framework to plug in real Claude API or other providers

**Integrations** (`backend/integrations/`)
- Platform-specific executors: AWS Glue, Azure Data Factory, Azure Databricks, GCP Composer, Airflow
- Each has an `executor.py` that implements actual remediation (currently stubs)
- Cloud SDKs: boto3, azure-identity, google-auth

**Persistence Layer** (`backend/persistence/db.py`)
- SQLite database with three tables: `remediations`, `polling_config`, `notification_config`
- Stores full audit trail of all remediation attempts with timestamps and results
- Per-instance polling configuration: interval (60-3600s), auto-execute flag, log filters
- Email recipient configuration per platform/instance (failures-only mode)
- Query layer for metrics aggregation (24h, 7d, 30d time windows)

**Log Polling Service** (`backend/polling/`)
- Platform-specific collectors for AWS CloudWatch, Azure Log Analytics, GCP Cloud Logging, Airflow
- APScheduler-based background polling every 5 minutes (configurable per instance)
- Failure detection via pattern matching → extracts failure signatures and run IDs
- Automatic invocation of healing workflow on detected failures
- Auto-remediation executor with safety gates: auto-executes LOW risk + allowlisted + configured; otherwise stores as PENDING

**Reporting & Metrics** (`backend/api/reporting.py`)
- Endpoints for metrics (total attempted, success rate, success count, still-failing count) across time periods
- Full audit history with pagination, filtering by platform/instance
- Summary aggregation across all time windows

**Admin Configuration API** (`backend/api/admin.py`)
- Endpoints to enable/disable polling, configure per-instance intervals, toggle auto-execute flag
- Email recipient configuration for failure notifications (failures-only: only sends when remediation attempted but issue persists)
- Per-instance settings stored in database for dynamic runtime configuration

**Notification Service** (`backend/notifications.py`)
- SMTP-based email alerts (configurable via environment variables)
- Failures-only mode: only sends when remediation attempted but issue persists (reduces noise)
- Template-based emails with links to reporting dashboard

**Enhanced Frontend** (`frontend/reporting.py`)
- Multi-page Streamlit UI:
  - Page 1: "Generate Plan" — manual failure submission and plan approval (original functionality)
  - Page 2: "Reporting" — metrics dashboard with period selector, platform filters, remediation history table
  - Page 3: "Admin Configuration" — polling enable/disable, interval control, auto-execute toggle, email recipients
- Real-time data from reporting API
- Admin API key authentication for configuration changes

### Configuration & Secrets

- **Config** (`backend/core/config.py`): Loads `config/integrations.enc` (encrypted YAML)
- **Secrets Manager** (`scripts/secrets_manager.py`): Encrypt/decrypt config with `SELF_HEALING_MASTER_KEY`
- Encrypted file keeps auth tokens, API keys, and credentials secure
- Public-facing API never exposes secrets
- **Startup** (`backend/core/startup.py`): Initializes database and starts polling orchestrator on application startup

## Development

### Prerequisites
- Python 3.9+ with pip
- Virtual environment for dependency isolation

### Quick Start

```bash
# Create and activate venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Initialize secrets and encryption key
python scripts/secrets_manager.py init
# Copy the printed key and set it:
export SELF_HEALING_MASTER_KEY='<paste key here>'

# Set admin API key for configuration endpoints
export ADMIN_API_KEY='your-admin-key'

# (Optional) Configure SMTP for email notifications
export SMTP_HOST='smtp.gmail.com'
export SMTP_PORT='587'
export SMTP_USER='your-email@gmail.com'
export SMTP_PASSWORD='your-app-password'
export SMTP_FROM='remediation@example.com'

# Build knowledge base (FAISS embeddings from SOPs)
python scripts/build_index.py

# Start backend API (initializes database and polling orchestrator on startup)
uvicorn backend.api.main:app --reload

# In another terminal: Start Streamlit frontend
streamlit run frontend/reporting.py
```

- Frontend (Reporting UI): http://localhost:8501
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

**Initial Configuration** (via Streamlit Admin tab or API):
1. Enable polling for cloud platform instances
2. Set per-instance poll intervals (default: 300 seconds = 5 minutes)
3. Toggle auto-execute flag for LOW-risk plans per instance
4. Configure email recipients for failure notifications (comma-separated)
5. Optionally set log filters to reduce noise

### Common Commands

**Build knowledge base index:**
```bash
python scripts/build_index.py
```

**Encrypt configuration:**
```bash
python scripts/secrets_manager.py init
```

**Verify system components:**
```bash
python scripts/verify_system.py
```

**Configure polling via API:**
```bash
# Enable polling for an instance
curl -X POST http://localhost:8000/admin/polling/aws_glue/prod-glue \
  -H "x-token: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "poll_interval_seconds": 300,
    "auto_execute_low_risk": true,
    "log_filter": null
  }'

# Set email recipients
curl -X POST http://localhost:8000/admin/notifications/aws_glue/prod-glue \
  -H "x-token: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"recipients": ["ops@example.com"]}'
```

**Get metrics:**
```bash
# Last 24 hours
curl "http://localhost:8000/reporting/metrics?period=24h"

# Last 7 days for specific platform
curl "http://localhost:8000/reporting/metrics?period=7d&platform=aws_glue"

# Full history with pagination
curl "http://localhost:8000/reporting/history?limit=50&offset=0"
```

**Run tests:**
```bash
pytest tests/ -v
```

**Run a single test:**
```bash
pytest tests/test_sop_match.py -v
```

**Start API with hot-reload:**
```bash
uvicorn backend.api.main:app --reload
```

**Start Streamlit UI (Reporting + Admin):**
```bash
streamlit run frontend/reporting.py
```

### Key Files & Modules

- **Models** (`backend/models/schemas.py`): Pydantic schemas for FailureEvent, RemediationPlan, ErrorClassification, SelfHealResult
- **Vectorstore** (`backend/vectorstore/`): FAISS-backed embedding storage
- **Data** (`data/sops/`): YAML files defining remediation SOPs (not code changes — configuration)
- **Config** (`config/integrations.sample.yaml`): Template for integrations configuration
- **Persistence** (`backend/persistence/db.py`): SQLite schema and query layer for remediations, polling_config, notification_config
- **Polling** (`backend/polling/`): APScheduler orchestrator, platform-specific log collectors (AWS/Azure/GCP/Airflow), auto-remediation executor
- **Admin API** (`backend/api/admin.py`): Configuration endpoints for polling and notifications
- **Reporting API** (`backend/api/reporting.py`): Metrics and history endpoints
- **Notifications** (`backend/notifications.py`): SMTP email service for failure alerts
- **Frontend** (`frontend/reporting.py`): Multi-page Streamlit UI (Generate Plan, Reporting, Admin Configuration)
- **Startup** (`backend/core/startup.py`): Database initialization and polling orchestrator startup
- **Requirements** (`requirements.txt`): All Python dependencies (27 packages)

## Important Notes

### No Code Changes to DAGs/Pipelines
All remediation is driven by **YAML SOPs** and allowlisted actions. The system never modifies user-facing DAG code or pipeline logic — only remediates operational failures (restart job, increase memory, fix connection issues, etc.).

### Safety by Default
- Plans are **always evaluated** against allowlists and risk policies
- Only LOW risk + allowlisted plans auto-execute (when polling detects failures or user submits via UI)
- Higher-risk plans require explicit approval via `/approve` endpoint or Streamlit approval button
- Auth is kept in encrypted config; never exposed to frontend or logs
- Polling auto-remediation respects per-instance configuration: LOW-risk plans only execute if `auto_execute_low_risk` flag is enabled for that instance

### Two Remediation Paths
1. **Manual** (original): User submits failure event via UI → plan generated → user approves via UI button → executes
2. **Autonomous** (new): Polling detects failure in logs every 5 min → plan generated → auto-executes if LOW risk + allowlisted + configured, else stores as PENDING → emails alert

### Comprehensive Authentication Support
- **AWS Glue**: default, profile, assume_role, explicit
- **Azure Data Factory/Databricks**: default, managed_identity, client_secret
- **GCP Cloud Composer**: default, service_account_key
- **Airflow**: jwt_password (self-hosted), aws_invoke_rest_api (MWAA), iap_oidc (Cloud Composer)
- All auth methods stored in encrypted `config/integrations.enc` — see `backend/polling/AUTH_METHODS.md` for examples

### Pluggable Providers
- **LLM**: Replace `backend/llm/providers/stub.py` with real provider (Claude, OpenAI, etc.)
- **Embeddings**: Replace FakeEmbeddings in `backend/kb/sop_match.py` with real embeddings (OpenAI, Hugging Face, etc.)
- **Executors**: Implement actual remediation in `backend/integrations/*/executor.py` (currently stubs)
- **Notifications**: Add Slack, PagerDuty, Webhooks alongside SMTP in `backend/notifications.py`

### Secrets Management
- Store all credentials in encrypted `config/integrations.enc`
- Set `SELF_HEALING_MASTER_KEY` env var at runtime
- Set `ADMIN_API_KEY` for admin configuration endpoints
- Never commit unencrypted credentials
- Use least-privilege cloud IAM roles for execution identities

### Failures-Only Email Notifications
- Emails only sent when remediation was attempted but issue persists (`is_still_failing=true`)
- Reduces alert fatigue — successful auto-remediations don't trigger emails
- Recipients configured per platform/instance via Admin UI or API
- Emails include links to reporting dashboard for investigation

## Testing

Basic test structure in `tests/test_sop_match.py` tests SOP matching and classification logic.

To add new tests:
1. Create test files in `tests/` directory
2. Use pytest conventions (test_*.py, test_*() functions)
3. Run with `pytest tests/ -v`

## Making It Production-Ready

### Immediate (Required for Deployment)
1. **Add real credentials** to `config/integrations.sample.yaml`, rename to `config/integrations.enc`
2. **Encrypt it** with `python scripts/secrets_manager.py init`
3. **Configure environment**:
   - `SELF_HEALING_MASTER_KEY`: Encryption key for credentials
   - `ADMIN_API_KEY`: API key for admin endpoints
   - `SMTP_*`: Email configuration (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)
4. **Enable polling** for target instances via Admin UI or API
5. **Verify database** initialized: `sqlite3 data/remediation.db ".tables"` should show 3 tables

### Short Term (First Week)
1. **Plug in real LLM**: Replace stub provider in `backend/llm/providers/`
2. **Plug in real embeddings**: Update `backend/kb/sop_match.py` (remove FakeEmbeddings)
3. **Set up SOPs**: Add YAML files to `data/sops/` with your org's remediation procedures
4. **Configure allowlists**: Define `allowlisted_actions` per instance in encrypted config
5. **Test end-to-end**: Verify polling detects failures and auto-remediation executes
6. **Set up email monitoring**: Verify failure alerts arrive when remediation attempts fail

### Medium Term (Ongoing)
1. **Implement executors**: Add actual remediation logic in `backend/integrations/*/executor.py`
2. **Add custom failure patterns**: Create YAML-based pattern definitions in `data/failure_patterns.yaml`
3. **Expand platforms**: Add pollers for additional orchestration platforms (Prefect, Dagster, etc.)
4. **Set up monitoring**: Wire up your telemetry/logging system (CloudWatch, DataDog, etc.)
5. **Archive metrics**: Move old records to time-series DB (InfluxDB, Prometheus) for long-term retention

### Advanced (Future Enhancements)
- Multi-tenant isolation
- Webhook notifications (replace/supplement SMTP)
- Advanced auth integration (OIDC, SAML, enterprise SSO)
- Credential rotation automation
- Audit logging for configuration changes
- Slack/PagerDuty integration
- Custom remediation approval workflows
