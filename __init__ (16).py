from __future__ import annotations
from typing import Dict, Any
import requests


class AzureDatabricksExecutor:
    """Azure Databricks operational remediations using Databricks REST APIs.

    Actions:
      - rerun_job -> /api/2.1/jobs/run-now
      - repair_run -> /api/2.1/jobs/runs/repair
      - restart_cluster -> /api/2.0/clusters/restart
    """

    def _base(self, inst: Dict[str, Any]) -> str:
        meta = inst.get('meta', inst)
        return meta['workspace_url'].rstrip('/')

    def _headers(self, inst: Dict[str, Any]) -> Dict[str, str]:
        auth = inst.get('auth') or (inst.get('meta') or {}).get('auth') or {}
        token = auth.get('token')
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def execute_step(self, instance: Dict[str, Any], run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        action = step.get('action')
        params = step.get('parameters', {}) or {}
        base = self._base(instance)
        headers = self._headers(instance)

        if action == 'rerun_job':
            # supports job_id OR existing run_id for re-run logic
            url = f"{base}/api/2.1/jobs/run-now"
            body = {}
            if 'job_id' in params:
                body['job_id'] = params['job_id']
            if 'notebook_params' in params:
                body['notebook_params'] = params['notebook_params']
            if 'jar_params' in params:
                body['jar_params'] = params['jar_params']
            if 'python_params' in params:
                body['python_params'] = params['python_params']
            r = requests.post(url, headers=headers, json=body, timeout=30)
            r.raise_for_status()
            return {'action': action, 'run_id': r.json().get('run_id')}

        if action == 'repair_run':
            url = f"{base}/api/2.1/jobs/runs/repair"
            body = {'run_id': params['run_id']}
            if 'rerun_tasks' in params:
                body['rerun_tasks'] = params['rerun_tasks']
            if 'latest_repair_id' in params:
                body['latest_repair_id'] = params['latest_repair_id']
            r = requests.post(url, headers=headers, json=body, timeout=30)
            r.raise_for_status()
            return {'action': action, 'repair_id': r.json().get('repair_id'), 'run_id': params['run_id']}

        if action == 'restart_cluster':
            url = f"{base}/api/2.0/clusters/restart"
            r = requests.post(url, headers=headers, json={'cluster_id': params['cluster_id']}, timeout=30)
            r.raise_for_status()
            return {'action': action, 'cluster_id': params['cluster_id'], 'status': 'RESTART_REQUESTED'}

        raise NotImplementedError(f"Databricks action not implemented: {action}")

    def validate(self, instance: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        base = self._base(instance)
        headers = self._headers(instance)
        rid = (instance.get('meta') or {}).get('run_id')
        if not rid:
            return {'status': 'VALIDATION_SKIPPED', 'reason': 'instance.meta.run_id missing'}
        url = f"{base}/api/2.1/jobs/runs/get"
        r = requests.get(url, headers=headers, params={'run_id': rid}, timeout=30)
        r.raise_for_status()
        return r.json().get('state', {})
