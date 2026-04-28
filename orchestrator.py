from __future__ import annotations
from typing import TypedDict, Dict, Any, List

from langgraph.graph import StateGraph, START, END

from backend.kb.sop_store import load_sops
from backend.kb.sop_match import match_sops
from backend.llm.providers.stub import StubLLM
from backend.safety.checks import evaluate_plan

from backend.integrations.aws_glue.executor import AwsGlueExecutor
from backend.integrations.azure_data_factory.executor import AzureDataFactoryExecutor
from backend.integrations.azure_databricks.executor import AzureDatabricksExecutor
from backend.integrations.gcp_composer.executor import GcpComposerExecutor
from backend.integrations.airflow.executor import AirflowExecutor

EXECUTORS = {
    'aws_glue': AwsGlueExecutor(),
    'azure_data_factory': AzureDataFactoryExecutor(),
    'azure_databricks': AzureDatabricksExecutor(),
    'gcp_composer': GcpComposerExecutor(),
    'airflow': AirflowExecutor(),
}

class HealState(TypedDict, total=False):
    platform: str
    instance: Dict[str, Any]
    run_id: str
    collected: Dict[str, Any]
    classification: Dict[str, Any]
    sop_hits: List
    plan: Dict[str, Any]
    safety: Dict[str, Any]
    executed: bool
    execution_result: Dict[str, Any]


def collect_node(state: HealState) -> Dict[str, Any]:
    # For production: implement platform-specific collectors.
    # For now: accept logs via instance.meta.sample_logs.
    logs = (state.get('instance', {}).get('meta') or {}).get('sample_logs')
    if not logs:
        logs = 'Sample failure: Timeout'
    return {'collected': {'logs': logs, 'metrics': {}, 'retry_history': []}}


def classify_node(state: HealState) -> Dict[str, Any]:
    llm = StubLLM()
    return {'classification': llm.classify({'logs': state['collected']['logs'], 'platform': state['platform']})}


def sop_node(state: HealState) -> Dict[str, Any]:
    sops = load_sops()
    hits = match_sops(sops, state['platform'], state['collected']['logs'])
    return {'sop_hits': hits}


def plan_node(state: HealState) -> Dict[str, Any]:
    if state.get('sop_hits'):
        sop, score = state['sop_hits'][0]
        plan = {
            'source': 'SOP',
            'plan_id': sop.get('id', 'SOP'),
            'description': (sop.get('plan') or {}).get('description', sop.get('name', 'SOP remediation')),
            'steps': (sop.get('plan') or {}).get('steps', []) or [],
            'risk': (sop.get('controls') or {}).get('risk', 'MED'),
            'requires_approval': bool((sop.get('controls') or {}).get('requires_approval', True)),
            'confidence': float(score),
        }
        return {'plan': plan}

    llm = StubLLM()
    p = llm.propose({'logs': state['collected']['logs'], 'platform': state['platform']})
    return {'plan': {'source': 'LLM', **p}}


def safety_node(state: HealState) -> Dict[str, Any]:
    allowlist = state.get('instance', {}).get('allowlisted_actions', [])
    return {'safety': evaluate_plan(state['platform'], state['plan'], allowlist)}


def execute_node(state: HealState) -> Dict[str, Any]:
    # Plan-only by default; execution happens via /approve.
    return {'executed': False, 'execution_result': {'status': 'PLAN_ONLY'}}


def build_graph():
    g = StateGraph(HealState)
    g.add_node('collect', collect_node)
    g.add_node('classify', classify_node)
    g.add_node('sop', sop_node)
    g.add_node('plan', plan_node)
    g.add_node('safety', safety_node)
    g.add_node('execute', execute_node)

    g.add_edge(START, 'collect')
    g.add_edge('collect', 'classify')
    g.add_edge('classify', 'sop')
    g.add_edge('sop', 'plan')
    g.add_edge('plan', 'safety')
    g.add_edge('safety', 'execute')
    g.add_edge('execute', END)

    return g.compile()


def run_execution(platform: str, instance: Dict[str, Any], run_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    ex = EXECUTORS[platform]
    results = []
    for step in plan.get('steps', []) or []:
        results.append(ex.execute_step(instance, run_id, step))
    validation = ex.validate(instance, run_id)
    return {'steps': results, 'validation': validation}
