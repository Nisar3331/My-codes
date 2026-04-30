# ADF LLM Self-Healing ETL Pipeline
**Munich RE × Capgemini DataRend Program — Use Case #1**

An intelligent Azure Data Factory ETL pipeline that automatically detects failures using Azure AI Foundry (GPT-4o), resolves them, and restarts — deployed as Azure Functions.

---

## Architecture

```
GitHub Repo
    │
    ├── push to main
    │         │
    │    GitHub Actions (CI/CD)
    │         │  pytest → deploy
    │         ▼
    │    Azure Function App
    │         ├── timer_trigger_fn.py   ← 6 AM daily schedule
    │         ├── event_trigger_fn.py   ← ADF failure via Event Grid
    │         └── http_trigger_fn.py    ← Manual REST trigger
    │                   │
    │         ┌─────────▼──────────┐
    │         │  Pipeline Flow     │
    │         │  1. CSV → Parquet  │  ADLS Gen2
    │         │  2. ADF trigger    │  azure-mgmt-datafactory
    │         │  3. LLM detect     │  Azure AI Foundry GPT-4o
    │         │  4. Auto-resolve   │  ADF SDK actions
    │         │  5. Restart        │  New pipeline run
    │         └────────────────────┘
```

---

## Project Structure

```
adf-llm-datarend/
├── main.py                          # CLI entry point
├── host.json                        # Azure Functions runtime config
├── requirements.txt                 # Python dependencies
├── .env.template                    # Environment variable template
├── local.settings.json.template     # Azure Functions local dev config
├── .gitignore
│
├── config/
│   ├── __init__.py
│   └── settings.py                  # AzureConfig + PipelineConfig dataclasses
│
├── src/
│   ├── __init__.py
│   ├── error_catalog.py             # 5 ADF error definitions + keywords
│   ├── data_processor.py            # CSV → Parquet (ADLS Gen2 + local mode)
│   ├── llm_error_detector.py        # Azure AI Foundry GPT-4o classifier
│   ├── error_resolver.py            # 5 auto-resolution handlers
│   ├── adf_pipeline_manager.py      # Trigger / monitor / restart ADF
│   ├── pipeline_orchestrator.py     # Full run loop + retry logic
│   └── logger.py                    # Centralised rotating logger
│
├── functions/
│   ├── __init__.py
│   ├── timer_trigger_fn.py          # Scheduled daily run (6 AM UTC)
│   ├── event_trigger_fn.py          # Auto-fires on ADF failure (Event Grid)
│   └── http_trigger_fn.py           # POST /run-pipeline, /detect-error, /health
│
├── tests/
│   ├── __init__.py
│   └── test_pipeline.py             # 33 unit tests (mock mode)
│
├── data/
│   ├── csv/                         # Sample CSV input files (gitignored)
│   └── landing/                     # Parquet output (gitignored)
│
├── logs/                            # Run logs + JSON reports (gitignored)
│
├── infra/
│   └── main.bicep                   # IaC — provisions all Azure resources
│
└── .github/
    └── workflows/
        └── ci.yml                   # pytest + Azure Functions deploy
```

---

## Supported Error Types

| Code | Error | Auto-Resolvable |
|------|-------|-----------------|
| ADF-001 | Linked Service Connection Failure | Yes |
| ADF-002 | Copy Activity Schema Mismatch | Yes |
| ADF-003 | File Not Found in Source Path | Yes |
| ADF-004 | Pipeline Timeout | Yes |
| ADF-005 | Stored Procedure / SQL Script Failure | Yes |

---

## Quick Start

### 1. Clone and set up locally

```bash
git clone https://github.com/<your-org>/adf-llm-datarend.git
cd adf-llm-datarend

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.template .env              # Fill in your Azure credentials
```

### 2. Run tests (no Azure needed)

```bash
pytest tests/ -v
```

### 3. Run in mock mode (no Azure needed)

```bash
python main.py --mode mock
```

### 4. Test a specific error message

```bash
python main.py --detect-only "Column mapping failed: column does not exist in sink"
```

### 5. Demo all 5 error types

```bash
python main.py --demo-errors
```

### 6. Run locally with real Azure

```bash
cp local.settings.json.template local.settings.json  # Fill in values
func start                                            # Requires Azure Functions Core Tools
```

---

## Deploy to Azure

### Prerequisites

- Azure CLI installed and logged in
- Azure Functions Core Tools v4
- Service Principal with Contributor on resource group

### Step 1 — Provision infrastructure

```bash
az group create --name rg-datarend-poc --location eastus

az deployment group create \
  --resource-group rg-datarend-poc \
  --template-file infra/main.bicep \
  --parameters env=poc
```

### Step 2 — Add GitHub Secrets

In GitHub → Settings → Secrets → Actions, add:

| Secret | Value |
|--------|-------|
| `AZURE_CREDENTIALS` | Service principal JSON (see below) |
| `AZURE_SUBSCRIPTION_ID` | Your subscription ID |
| `AZURE_TENANT_ID` | Your tenant ID |
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_RESOURCE_GROUP` | `rg-datarend-poc` |
| `ADF_FACTORY_NAME` | `adf-datarend-poc` |
| `ADF_PIPELINE_NAME` | `pl_csv_to_parquet_ingestion` |
| `AZURE_STORAGE_ACCOUNT` | Storage account name |
| `AZURE_STORAGE_KEY` | Storage account key |
| `AZURE_STORAGE_CONNECTION_STRING` | Full connection string |
| `AZURE_OPENAI_ENDPOINT` | AI Foundry endpoint URL |
| `AZURE_OPENAI_API_KEY` | AI Foundry API key |

**Generate service principal JSON:**
```bash
az ad sp create-for-rbac \
  --name "sp-datarend-poc" \
  --role Contributor \
  --scopes /subscriptions/<subscription-id>/resourceGroups/rg-datarend-poc \
  --sdk-auth
```

### Step 3 — Connect Event Grid to ADF

In Azure Portal:
1. Go to your ADF resource → Events
2. Create Event Subscription
3. Event type: `Microsoft.DataFactory.PipelineRunStatusChanged`
4. Filter: status = Failed
5. Endpoint type: Azure Function
6. Function: `adf_failure_handler`

### Step 4 — Push to deploy

```bash
git push origin main
# GitHub Actions runs pytest → deploys to Azure Functions automatically
```

---

## API Endpoints (HTTP Trigger)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/run-pipeline` | Trigger full pipeline |
| `POST` | `/api/detect-error` | Detect + resolve a single error |

**Example — detect an error:**
```bash
curl -X POST https://func-datarend-poc.azurewebsites.net/api/detect-error \
  -H "Content-Type: application/json" \
  -H "x-functions-key: <function-key>" \
  -d '{"error_message": "Activity timed out after 3600 seconds"}'
```

---

## Run Modes

| Mode | CSV → Parquet | ADF | LLM | Use for |
|------|---------------|-----|-----|---------|
| `mock` | Simulated | Simulated | Simulated | CI, unit tests |
| `local` | Local disk | Simulated | Real GPT-4o | Dev testing |
| `full` | ADLS Gen2 | Real ADF | Real GPT-4o | Production |

---

## Environment Variables

See `.env.template` for the full list. All variables are also documented in `config/settings.py`.

---

*Document prepared by: Capgemini DataRend Team | For: Munich RE | April 2026*
