from __future__ import annotations
from typing import Dict, Any

from backend.integrations.airflow.client import ComposerAirflowClient, AirflowRequest


class GcpComposerExecutor:
    """GCP Composer executor: operational remediations via Airflow REST API behind IAP."""

    def _client(self, instance: Dict[str, Any]) -> ComposerAirflowClient:
        base_url = instance.get('base_url') or (instance.get('meta') or {}).get('base_url')
        auth = instance.get('auth') or (instance.get('meta') or {}).get('auth') or {}
        if auth.get('method') != 'iap_oidc':
            raise ValueError('Composer requires auth.method=iap_oidc')
        return ComposerAirflowClient(base_url=base_url, audience=auth['audience'])

    def execute_step(self, instance: Dict[str, Any], run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        client = self._client(instance)
        action = step.get('action')
        params = step.get('parameters', {}) or {}

        if action in ('rerun_dag', 'pause_dag', 'unpause_dag', 'clear_task'):
            # reuse same payloads as AirflowExecutor
            from backend.integrations.airflow.executor import AirflowExecutor
            return AirflowExecutor().execute_step({**instance, 'variant': 'composer'}, run_id, step)

        raise NotImplementedError(f"Composer action not implemented: {action}")

    def validate(self, instance: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        return {'status': 'VALIDATION_NOT_IMPLEMENTED'}
