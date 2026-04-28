from __future__ import annotations
from typing import Dict, Any
import json

import boto3


class AwsGlueExecutor:
    """AWS Glue operational remediations (no code changes).

    Actions:
      - rerun_job: startJobRun
      - add_s3_permission: put_role_policy with scoped S3 PutObject
      - update_iam_policy: put_role_policy (generic inline)
    """

    def _clients(self, region: str):
        return boto3.client('glue', region_name=region), boto3.client('iam', region_name=region)

    def execute_step(self, instance: Dict[str, Any], run_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        region = instance.get('meta', {}).get('region') or instance.get('region') or 'us-east-1'
        glue, iam = self._clients(region)
        action = step.get('action')
        params = step.get('parameters', {}) or {}

        if action == 'rerun_job':
            job_name = params['job_name']
            args = params.get('arguments')
            if args:
                resp = glue.start_job_run(JobName=job_name, Arguments=args)
            else:
                resp = glue.start_job_run(JobName=job_name)
            return {'action': action, 'job_name': job_name, 'job_run_id': resp.get('JobRunId')}

        if action == 'add_s3_permission':
            role_name = params['role_name']
            bucket_arn = params['bucket_arn']
            prefix = params.get('prefix', '')
            policy_name = params.get('policy_name', 'selfhealing-inline')

            resource = f"{bucket_arn}/{prefix}*" if prefix else f"{bucket_arn}/*"
            policy_doc = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
                        "Resource": [resource],
                    }
                ],
            }
            iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(policy_doc))
            return {'action': action, 'role_name': role_name, 'policy_name': policy_name, 'resource': resource}

        if action == 'update_iam_policy':
            role_name = params['role_name']
            policy_name = params.get('policy_name', 'selfhealing-inline')
            policy_document = params['policy_document']  # expects dict
            iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(policy_document))
            return {'action': action, 'role_name': role_name, 'policy_name': policy_name}

        raise NotImplementedError(f"AWS Glue action not implemented: {action}")

    def validate(self, instance: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        region = instance.get('meta', {}).get('region') or instance.get('region') or 'us-east-1'
        glue = boto3.client('glue', region_name=region)
        job_name = (instance.get('meta') or {}).get('job_name')
        job_run_id = (instance.get('meta') or {}).get('job_run_id')
        if not job_name or not job_run_id:
            return {'status': 'VALIDATION_SKIPPED', 'reason': 'job_name/job_run_id not provided in instance.meta'}
        resp = glue.get_job_run(JobName=job_name, RunId=job_run_id, PredecessorsIncluded=False)
        jr = resp.get('JobRun', {})
        return {'job_name': job_name, 'job_run_id': job_run_id, 'state': jr.get('JobRunState'), 'error': jr.get('ErrorMessage')}
