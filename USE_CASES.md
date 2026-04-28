# Unified Self-Healing Platform (AWS Glue + Azure Data Factory + Azure Databricks + GCP Composer + Airflow)

This repository is a **single, complete** implementation of the layout you asked for:

- **Frontend**: Streamlit UI
- **VectorDB / KB**: FAISS (default) + SOP YAML store
- **Integration Points**: 
  - AWS Glue
  - Azure Data Factory
  - Azure Databricks
  - GCP Composer
  - Airflow (Self-hosted, MWAA, Composer)

## Design principles (matches your constraints)
- **No code changes** in DAGs/pipelines/jobs
- **SOP-first remediation** (YAML) and auditable
- If SOP not found: **LLM fallback plan** (stub provider included) but still constrained by allow-list + safety gates
- Execution is gated via `/approve` (defense-in-depth)

## Quick start (runs in plan-mode without cloud creds)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/secrets_manager.py init
export SELF_HEALING_MASTER_KEY='<paste printed key>'

python scripts/build_index.py
uvicorn backend.api.main:app --reload
streamlit run frontend/app.py
```

## Make it real (minimal edits)
1) Put real instances & credentials into `config/integrations.sample.yaml`
2) Encrypt it: `python scripts/secrets_manager.py init`
3) Ensure your execution identity has least-privilege permissions for allowlisted actions.

## Core APIs
- `GET /integrations` -> shows each integration + #instances
- `POST /self-heal` -> generates plan (SOP-first / LLM fallback) + safety evaluation
- `POST /approve` -> executes allowlisted LOW risk plans

## Notes
- The repo uses **FakeEmbeddings** by default so FAISS works without external keys.
- Plug embeddings + LLM providers later with minimal changes.
