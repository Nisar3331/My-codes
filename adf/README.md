# ADF LLM Self-Healing ETL Pipeline
**Munich RE × Capgemini DataRend Program — Use Case #1**

> An intelligent Azure Data Factory ETL pipeline that automatically detects failures
> using Azure AI Foundry (GPT-4o), resolves them, and restarts — deployed as Azure Functions.

---

## Table of Contents

1. [What this does](#1-what-this-does)
2. [Project structure](#2-project-structure)
3. [Quick start](#3-quick-start)
4. [Deploy to Azure](#4-deploy-to-azure)
5. [How to add a new error](#5-how-to-add-a-new-error)
6. [Current error catalog](#6-current-error-catalog)
7. [API endpoints](#7-api-endpoints)
8. [Run modes](#8-run-modes)
9. [Environment variables](#9-environment-variables)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What this does

When an ADF pipeline fails, this system:

```
ADF Pipeline Fails
       │
       ▼
Event Grid fires → Azure Function triggered
       │
       ▼
LLM Error Detector (GPT-4o)
  reads the error message
  matches it to the error catalog
  returns: error code + confidence + resolution action
       │
       ▼
Error Resolver
  executes the fix (ADF SDK calls, file polling, schema refresh etc.)
       │
       ▼
Pipeline Manager
  restarts the ADF pipeline
  retries up to 3 times
       │
       ▼
Run report saved to logs/
```

The CSV → Parquet conversion also runs automatically:
- Reads CSV files from ADLS Gen2 `raw-data/csv-input`
- Sanitises column names (prevents ADF-006 Parquet errors)
- Writes Parquet files to ADLS Gen2 `landing/parquet-output`

---

## 2. Project structure

```
adf-llm-datarend/
│
├── main.py                            ← CLI entry point
├── host.json                          ← Azure Functions runtime config
├── requirements.txt                   ← Python dependencies
├── .env.template                      ← Copy this to .env and fill in values
├── local.settings.json.template       ← Azure Functions local dev config
├── .gitignore                         ← Blocks .env, logs/, data/, __pycache__
│
├── config/
│   └── settings.py                    ← All Azure config loaded from env vars
│
├── src/
│   ├── error_catalog.py               ← All error definitions (ADD NEW ERRORS HERE)
│   ├── error_resolver.py              ← Auto-resolution handlers (ADD NEW HANDLERS HERE)
│   ├── data_processor.py              ← CSV → Parquet with column sanitisation
│   ├── llm_error_detector.py          ← GPT-4o error classifier
│   ├── adf_pipeline_manager.py        ← Trigger / monitor / restart ADF
│   ├── pipeline_orchestrator.py       ← Full run loop + retry logic
│   └── logger.py                      ← Rotating file + console logger
│
├── functions/
│   ├── timer_trigger_fn.py            ← Runs pipeline on schedule (6 AM UTC)
│   ├── event_trigger_fn.py            ← Auto-fires on ADF failure via Event Grid
│   └── http_trigger_fn.py             ← REST endpoints: /run-pipeline /detect-error /health
│
├── tests/
│   └── test_pipeline.py               ← 52 unit tests (run without Azure)
│
├── data/
│   └── csv/sample_incidents.csv       ← Sample CSV for local testing
│
├── infra/
│   └── main.bicep                     ← Provisions all Azure resources
│
└── .github/
    └── workflows/
        └── ci.yml                     ← pytest on PR + deploy on push to main
```

---

## 3. Quick start

```bash
# Clone the repo
git clone https://github.com/<your-org>/adf-llm-datarend.git
cd adf-llm-datarend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.template .env            # Then open .env and paste your Azure values

# Run tests — no Azure credentials needed
pytest tests/ -v

# Run fully offline demo
python main.py --mode mock

# Test a specific error message
python main.py --detect-only "Column name cannot contain these characters [,;{}]"

# Run all 5 error type demos
python main.py --demo-errors
```

---

## 4. Deploy to Azure

### Prerequisites
- Azure CLI installed and logged in (`az login`)
- Azure Functions Core Tools v4 (`npm install -g azure-functions-core-tools@4`)
- Service Principal created with Contributor role (see `WHAT_TO_DO.txt`)

### Step 1 — Provision infrastructure
```bash
az group create --name rg-datarend-poc --location eastus

az deployment group create \
  --resource-group rg-datarend-poc \
  --template-file infra/main.bicep \
  --parameters env=poc
```

### Step 2 — Add GitHub Secrets
Go to your GitHub repo → Settings → Secrets and variables → Actions
Add all secrets listed in `WHAT_TO_DO.txt` Section 4.

### Step 3 — Push to deploy
```bash
git push origin main
# Tests run automatically → deploys to Azure Functions if tests pass
```

### Step 4 — Connect Event Grid to ADF
In Azure Portal → `pf-observability-datafactory` → Events → New Subscription
- Event type: `PipelineRunStatusChanged`
- Filter: `data.status = Failed`
- Endpoint: Azure Function → `adf_failure_handler`

---

## 5. How to add a new error

> **This is the most important section.**
> Every time you see a new error in ADF that is not in the list below,
> follow these 4 steps to add it. Takes about 10 minutes.

---

### Step 1 — Paste your error here (fastest option)

Just paste the raw ADF error message in the Claude chat and say
"add this as a new error". The full code for all 4 steps will be generated
for you automatically.

---

### Step 2 — Or add it yourself (manual option)

#### File 1: `src/error_catalog.py`

Open the file and find this comment at the bottom of `ADF_ERROR_CATALOG`:

```python
# ADD NEW ERRORS BELOW THIS LINE
```

Add a new `AdfError` block. Copy the pattern below and fill it in:

```python
AdfError(
    error_code="ADF-007",                        # increment from last one
    error_name="Your Error Name Here",
    common_error_message="The exact message ADF shows",
    main_cause=(
        "Why this error happens — "
        "be specific about the root cause"
    ),
    business_impact="What breaks when this error occurs",
    troubleshooting_steps=[
        "Step 1 to fix it",
        "Step 2 to fix it",
        "Step 3 to fix it",
    ],
    keywords=[
        "word1", "word2", "word3",               # words from the error message
        "errorcode", "specific phrase",           # used by LLM keyword fallback
    ],
    auto_resolvable=True,                        # True if code can fix it
    resolution_action="your_action_name_here",   # must match Step 2 below
    severity="HIGH",                             # HIGH | MEDIUM | LOW
),
```

**Rules for keywords:**
- Use lowercase
- Include the exact ErrorCode value (e.g. `sqlfailedtoconnect`)
- Include 2-3 distinctive phrases from the error message
- More keywords = better LLM fallback matching

---

#### File 2: `src/error_resolver.py`

**Part A** — Add one line to the dispatch table inside `resolve()`.
Find this comment:

```python
# ADD NEW RESOLUTION ACTIONS BELOW THIS LINE
```

Add your action:

```python
"your_action_name_here": self._resolve_your_error,
```

**Part B** — Add the handler method to the class.
Find this comment at the bottom of the class:

```python
# ADD NEW RESOLUTION HANDLERS BELOW THIS LINE
```

Add your method:

```python
def _resolve_your_error(self, detection: Dict[str, Any]) -> Dict[str, Any]:
    """
    ADF-007 — Your Error Name.
    Actions:
      1. What this method does
      2. What it checks or fixes
    """
    logger.info("  [ADF-007] Handling your error ...")

    actions_taken = [
        "Identified the error",
        "Recommended: specific fix step 1",
        "Recommended: specific fix step 2",
    ]

    return {
        "success": True,
        "actions_taken": actions_taken,
        "message": "Your resolution message here.",
        "requires_manual_followup": True,
    }
```

---

#### File 3: `tests/test_pipeline.py`

Add two tests. Find the `TestErrorCatalog` class and update:

```python
# Change this line:
def test_catalog_has_six_errors(self):
    assert len(ADF_ERROR_CATALOG) == 6

# To this (increment the number):
def test_catalog_has_seven_errors(self):
    assert len(ADF_ERROR_CATALOG) == 7
```

Add to the parametrize lists — find both of these and add `"ADF-007"`:

```python
@pytest.mark.parametrize("code", [
    "ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005", "ADF-006", "ADF-007",
])
def test_each_error_has_resolution_action(self, code):
    ...

@pytest.mark.parametrize("code", [
    "ADF-001", "ADF-002", "ADF-003", "ADF-004", "ADF-005", "ADF-006", "ADF-007",
])
def test_each_error_has_keywords(self, code):
    ...
```

Add a resolver test inside `TestErrorResolver`:

```python
@patch("src.error_resolver.DataFactoryManagementClient")
@patch("src.error_resolver.ClientSecretCredential")
def test_resolve_your_error(self, mock_cred, mock_adf):
    from src.error_resolver import AdfErrorResolver
    resolver = AdfErrorResolver()
    result = resolver.resolve(
        self._make_detection("ADF-007", "your_action_name_here")
    )
    assert result["error_code"] == "ADF-007"
    assert result["success"] is True
```

---

#### Verify everything works

```bash
pytest tests/ -v
# All tests must pass before you commit
```

```bash
git add src/error_catalog.py src/error_resolver.py tests/test_pipeline.py
git commit -m "feat: add ADF-007 <your error name>"
git push origin main
# GitHub Actions deploys automatically
```

---

### Complete checklist for adding a new error

```
[ ] 1. error_catalog.py  → add AdfError block with new error_code
[ ] 2. error_resolver.py → add line to dispatch table
[ ] 3. error_resolver.py → add _resolve_<name>() method
[ ] 4. test_pipeline.py  → update count assertion
[ ] 5. test_pipeline.py  → add "ADF-00X" to both parametrize lists
[ ] 6. test_pipeline.py  → add test_resolve_<name>() method
[ ] 7. Run pytest — all tests must pass
[ ] 8. git commit + git push → auto-deploys
```

---

### Real example — how ADF-006 was added

ADF-006 was added after seeing this real error in Teams:

```
ErrorCode=ParquetInvalidColumnName
Type=Microsoft.DataTransfer.Common.Shared.HybridDeliveryException
Message=The column name is invalid.
Column name cannot contain these characters:[,;{}()\n\t=]
Source=Microsoft.DataTransfer.Common
Pipeline: pipeline3
Data factory: pf-observability-datafactory
```

What was added:
- `error_catalog.py`   → AdfError block with code ADF-006
- `error_resolver.py`  → `sanitise_parquet_column_names` in dispatch table
- `error_resolver.py`  → `_resolve_parquet_column_names()` method
- `data_processor.py`  → `sanitise_column_names()` function applied to every CSV read
- `test_pipeline.py`   → 5 new tests including end-to-end column sanitisation test

Result: the error can never happen again because column names are
automatically cleaned before writing Parquet.

---

## 6. Current error catalog

| Code    | Error Name                              | Triggered By                                      | Auto-Fixed |
|---------|-----------------------------------------|---------------------------------------------------|------------|
| ADF-001 | Linked Service Connection Failure       | SqlFailedToConnect, wrong credentials, firewall   | Yes        |
| ADF-002 | Copy Activity Schema Mismatch           | Column mapping failed, schema drift               | Yes        |
| ADF-003 | File Not Found in Source Path           | UserErrorSourceBlobNotExist, 404 on blob path     | Yes        |
| ADF-004 | Pipeline Timeout                        | Activity timed out, slow IR, large data volume    | Yes        |
| ADF-005 | Stored Procedure / SQL Script Failure   | Deadlock, duplicate key, constraint violation     | Yes        |
| ADF-006 | Parquet Invalid Column Name             | ParquetInvalidColumnName, special chars in header | Yes        |

Next error to add will be **ADF-007**.

---

## 7. API endpoints

After deployment, three HTTP endpoints are available:

### GET /api/health
```bash
curl https://func-datarend-poc.azurewebsites.net/api/health \
  -H "x-functions-key: YOUR_KEY"

# Response:
{ "status": "healthy", "service": "adf-llm-datarend" }
```

### POST /api/run-pipeline
```bash
curl -X POST https://func-datarend-poc.azurewebsites.net/api/run-pipeline \
  -H "Content-Type: application/json" \
  -H "x-functions-key: YOUR_KEY" \
  -d '{ "mode": "full", "retries": 3 }'

# Response: full run report JSON
```

### POST /api/detect-error
```bash
curl -X POST https://func-datarend-poc.azurewebsites.net/api/detect-error \
  -H "Content-Type: application/json" \
  -H "x-functions-key: YOUR_KEY" \
  -d '{ "error_message": "Column name cannot contain these characters [,;{}]" }'

# Response:
{
  "detection": {
    "matched_error_code": "ADF-006",
    "confidence": 0.95,
    "resolution_action": "sanitise_parquet_column_names"
  },
  "resolution": {
    "success": true,
    "message": "Parquet column name sanitisation applied..."
  }
}
```

---

## 8. Run modes

| Mode    | CSV → Parquet   | ADF Pipeline    | LLM (GPT-4o)    | Use for                        |
|---------|-----------------|-----------------|-----------------|--------------------------------|
| `mock`  | Simulated       | Simulated       | Simulated       | Unit tests, CI, offline demo   |
| `local` | Local disk      | Simulated       | Real GPT-4o     | Dev testing with real LLM      |
| `full`  | ADLS Gen2       | Real ADF        | Real GPT-4o     | Production                     |

```bash
python main.py --mode mock           # fully offline
python main.py --mode local          # local files + real LLM
python main.py --mode full           # everything on Azure
python main.py --mode full --retries 5    # override retry count
python main.py --detect-only "error message"   # test detection only
python main.py --demo-errors                   # test all 6 error types
```

---

## 9. Environment variables

All variables live in `.env` (local) and GitHub Secrets (CI/CD).
See `.env.template` for the full list with descriptions.

| Variable                        | Required | Where to get it                              |
|---------------------------------|----------|----------------------------------------------|
| AZURE_SUBSCRIPTION_ID           | Yes      | Azure Portal → Subscriptions                 |
| AZURE_TENANT_ID                 | Yes      | Azure AD → Overview                          |
| AZURE_CLIENT_ID                 | Yes      | App Registrations → your app → Overview      |
| AZURE_CLIENT_SECRET             | Yes      | App Registrations → Certificates & secrets   |
| AZURE_RESOURCE_GROUP            | Yes      | Your resource group name                     |
| ADF_FACTORY_NAME                | Yes      | pf-observability-datafactory                 |
| ADF_PIPELINE_NAME               | Yes      | Your pipeline name in ADF Studio             |
| AZURE_STORAGE_ACCOUNT           | Yes      | pfobservabilitystorage                       |
| AZURE_STORAGE_KEY               | Yes      | Storage → Access keys → key1                 |
| AZURE_STORAGE_CONNECTION_STRING | Yes      | Storage → Access keys → Connection string    |
| AZURE_OPENAI_ENDPOINT           | Yes      | Azure OpenAI → Keys and Endpoint             |
| AZURE_OPENAI_API_KEY            | Yes      | Azure OpenAI → Keys and Endpoint → KEY 1     |
| AZURE_OPENAI_API_VERSION        | Yes      | 2024-02-15-preview (leave as is)             |
| LLM_DEPLOYMENT_NAME             | Yes      | Your gpt-4o deployment name in AI Foundry    |
| KEY_VAULT_URL                   | No       | Azure Key Vault → Overview (optional)        |

---

## 10. Troubleshooting

### LLM detection fails
```
Check AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env
Verify gpt-4o deployment exists in Azure AI Foundry → Deployments
The system will fall back to keyword matching automatically
```

### Pipeline restart fails
```
Check that the Service Principal has Data Factory Contributor role on ADF
Verify AZURE_CLIENT_ID and AZURE_CLIENT_SECRET are correct
Go to ADF → Monitor → Pipeline runs to see the error manually
```

### Blob not found (ADF-003)
```
Check pfobservabilitystorage → pf-observability-blob-container → raw/
Verify the source folder path matches SOURCE_FOLDER in .env
The resolver will poll for the file up to 3 times (30s intervals)
```

### Parquet column error (ADF-006)
```
This is now auto-fixed in data_processor.py
sanitise_column_names() runs on every CSV before writing Parquet
If still seeing in ADF Copy Activity: add column mapping manually in ADF Studio
```

### Tests failing locally
```bash
pip install -r requirements.txt      # reinstall dependencies
pytest tests/ -v --tb=long           # see full error details
```

### GitHub Actions deploy fails
```
Check all GitHub Secrets are added (Settings → Secrets → Actions)
Check the AZURE_CREDENTIALS secret contains valid JSON
Check the Function App name matches AZURE_FUNCTIONAPP_NAME in ci.yml
```

---

## Files you should never edit directly

| File                     | Reason                                              |
|--------------------------|-----------------------------------------------------|
| `.env`                   | Contains secrets — never commit, never share        |
| `local.settings.json`    | Local Azure Functions config — never commit         |
| `logs/`                  | Auto-generated run reports — gitignored             |
| `data/landing/`          | Auto-generated Parquet files — gitignored           |

---

## Files you edit regularly

| File                     | When to edit                                        |
|--------------------------|-----------------------------------------------------|
| `src/error_catalog.py`   | Every time a new ADF error is found                 |
| `src/error_resolver.py`  | Every time a new ADF error is found                 |
| `tests/test_pipeline.py` | Every time a new error or feature is added          |
| `config/settings.py`     | If new Azure resources or config values are needed  |
| `.env`                   | When Azure resource names or keys change            |

---

*Document prepared by: Capgemini DataRend Team | For: Munich RE | April 2026*
