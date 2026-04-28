from __future__ import annotations
from typing import Dict, Any
import requests

from backend.integrations.azure_data_factory.azure_auth import get_arm_token


class AzureDataFactoryExecutor:
    """ADF operational remediations using Azure Management REST API.

    Actions:
      - rerun_pipeline -> pipelines/{pipeline}/createRun
      - cancel_run -> pipelineRuns/{runId}/cancel
      - toggle_trigger -> triggers/{trigger}/start|stop
    """

    API_VERSION = '2018-06-01'

    def _base(self, inst: Dict[str, Any]) -> str:
        meta = inst.get('meta', inst)
        sub = meta['subscription_id']
        rg = meta['resource_group']
        factory = meta['factory_name']
        return f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{factory}"

    def _headers(self, inst: Dict[str, Any]) -> Dict[str, str]:
        auth = inst.get('auth') or (inst.get('meta') or {}).get('auth') or {}
        token = get_arm_token(auth)
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def execute_step(self, instance: Dict[str, Any], run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        action = step.get('action')
        params = step.get('parameters', {}) or {}
        base = self._base(instance)
        headers = self._headers(instance)

        if action == 'rerun_pipeline':
            pipeline = params['pipeline_name']
            body = {'parameters': params.get('parameters', {})}
            url = f"{base}/pipelines/{pipeline}/createRun?api-version={self.API_VERSION}"
            r = requests.post(url, headers=headers, json=body, timeout=30)
            r.raise_for_status()
            return {'action': action, 'pipeline_name': pipeline, 'adf_run_id': r.json().get('runId')}

        if action == 'cancel_run':
            prun = params['pipeline_run_id']
            url = f"{base}/pipelineruns/{prun}/cancel?api-version={self.API_VERSION}"
            r = requests.post(url, headers=headers, timeout=30)
            r.raise_for_status()
            return {'action': action, 'pipeline_run_id': prun, 'status': 'CANCEL_REQUESTED'}

        if action == 'toggle_trigger':
            trigger = params['trigger_name']
            mode = params.get('mode', 'start')  # start|stop
            if mode not in ('start', 'stop'):
                raise ValueError('toggle_trigger.mode must be start|stop')
            url = f"{base}/triggers/{trigger}/{mode}?api-version={self.API_VERSION}"
            r = requests.post(url, headers=headers, timeout=30)
            r.raise_for_status()
            return {'action': action, 'trigger_name': trigger, 'mode': mode}

        raise NotImplementedError(f"ADF action not implemented: {action}")

    def validate(self, instance: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        # optional: implement GET pipelineruns/{runId}
        return {'status': 'VALIDATION_NOT_IMPLEMENTED'}
