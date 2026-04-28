from __future__ import annotations
from fastapi import FastAPI, HTTPException
from datetime import datetime

from backend.core.config import load_config
from backend.core.startup import startup_event, shutdown_event
from backend.models.schemas import FailureEvent, SelfHealResult, ErrorClassification, RemediationPlan
from backend.orchestration.graph import build_graph, run_execution
from backend.api.admin import router as admin_router
from backend.api.reporting import router as reporting_router

app = FastAPI(title='Unified Self-Healing Platform', version='0.1')

# Register routers
app.include_router(admin_router)
app.include_router(reporting_router)

# Startup and shutdown events
app.add_event_handler("startup", startup_event)
app.add_event_handler("shutdown", shutdown_event)


def _instances(cfg: dict):
    out = {}
    for plat, arr in (cfg.get('integrations') or {}).items():
        out[plat] = {x['name']: x for x in (arr or [])}
    return out


def _public_instance(inst: dict):
    # Do NOT return secrets.
    safe = {k: v for k, v in inst.items() if k not in ('auth',)}
    safe['allowlisted_actions'] = inst.get('allowlisted_actions', [])
    # Keep auth only inside encrypted file.
    return safe


def _internal_instance(inst: dict):
    # For execution we need auth – keep this internal.
    return inst


@app.get('/health')
def health():
    return {'ok': True, 'ts': datetime.utcnow().isoformat() + 'Z'}


@app.get('/integrations')
def integrations():
    cfg = load_config()
    integrations = cfg.get('integrations', {})
    return {
        plat: {
            'connected_workspaces': len(arr or []),
            'instances': [x.get('name') for x in (arr or [])],
        }
        for plat, arr in integrations.items()
    }


@app.post('/self-heal', response_model=SelfHealResult)
def self_heal(event: FailureEvent):
    cfg = load_config()
    idx = _instances(cfg)

    inst = idx.get(event.platform, {}).get(event.instance_name)
    if not inst:
        raise HTTPException(404, f'Unknown instance: {event.platform}/{event.instance_name}')

    state = {
        'platform': event.platform,
        'instance': _public_instance(inst),
        'run_id': event.run_id,
    }

    graph = build_graph()
    out = graph.invoke(state)

    classification = ErrorClassification(**out['classification'])
    plan = RemediationPlan(**out['plan'])

    return SelfHealResult(
        event=event,
        classification=classification,
        plan=plan,
        safety=out['safety'],
        executed=bool(out.get('executed', False)),
        execution_result=out.get('execution_result'),
    )


@app.post('/approve')
def approve(payload: dict):
    """Execute a previously returned plan.

    Input: { platform, instance_name, run_id, plan }

    This starter executes only if:
      - allowlisted
      - risk == LOW
    """
    cfg = load_config()
    idx = _instances(cfg)

    platform = payload.get('platform')
    instance_name = payload.get('instance_name')
    run_id = payload.get('run_id')
    plan = payload.get('plan')

    inst = idx.get(platform, {}).get(instance_name)
    if not inst:
        raise HTTPException(404, f'Unknown instance: {platform}/{instance_name}')

    from backend.safety.checks import evaluate_plan
    safety = evaluate_plan(platform, plan, inst.get('allowlisted_actions', []))
    if not safety.get('allowlisted', False):
        raise HTTPException(400, {'message': 'Plan violates allow-list', 'safety': safety})
    if safety.get('risk') != 'LOW':
        raise HTTPException(400, {'message': 'Only LOW risk plans can be executed in this starter', 'safety': safety})

    execution = run_execution(platform, _internal_instance(inst), run_id, plan)
    return {'executed': True, 'safety': safety, 'execution': execution}
