from __future__ import annotations
from typing import Dict, Any

from backend.integrations.airflow.client import build_airflow_client, AirflowRequest


class AirflowExecutor:
    """Airflow executor (Self-hosted, MWAA, Composer) – operational remediations only."""

    def execute_step(self, instance: Dict[str, Any], run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        client = build_airflow_client(instance)
        action = step.get('action')
        params = step.get('parameters', {}) or {}

        if action == 'rerun_dag':
            dag_id = params['dag_id']
            conf = params.get('conf', {})
            return client.request(AirflowRequest('POST', f"/dags/{dag_id}/dagRuns", json={"conf": conf}))

        if action == 'pause_dag':
            dag_id = params['dag_id']
            return client.request(AirflowRequest('PATCH', f"/dags/{dag_id}", json={"is_paused": True}))

        if action == 'unpause_dag':
            dag_id = params['dag_id']
            return client.request(AirflowRequest('PATCH', f"/dags/{dag_id}", json={"is_paused": False}))

        if action == 'clear_task':
            dag_id = params['dag_id']
            payload = {
                "dry_run": bool(params.get('dry_run', False)),
                "only_failed": bool(params.get('only_failed', True)),
                "include_subdags": bool(params.get('include_subdags', True)),
                "include_parentdag": bool(params.get('include_parentdag', True)),
                "reset_dag_runs": bool(params.get('reset_dag_runs', True)),
            }
            for k in ["start_date", "end_date", "task_ids", "dag_run_id", "include_downstream", "include_upstream"]:
                if k in params:
                    payload[k] = params[k]
            return client.request(AirflowRequest('POST', f"/dags/{dag_id}/clearTaskInstances", json=payload))

        raise NotImplementedError(f"Airflow action not implemented: {action}")

    def validate(self, instance: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        return {'status': 'VALIDATION_NOT_IMPLEMENTED'}
