from __future__ import annotations
from typing import Dict, Any

from azure.identity import ManagedIdentityCredential, ClientSecretCredential


def get_credential(auth: Dict[str, Any]):
    method = auth.get('method', 'managed_identity')
    if method == 'managed_identity':
        return ManagedIdentityCredential()
    if method == 'client_secret':
        return ClientSecretCredential(tenant_id=auth['tenant_id'], client_id=auth['client_id'], client_secret=auth['client_secret'])
    raise ValueError(f"Unsupported Azure auth method: {method}")


def get_arm_token(auth: Dict[str, Any]) -> str:
    cred = get_credential(auth)
    tok = cred.get_token('https://management.azure.com/.default')
    return tok.token
