from __future__ import annotations
from typing import Dict, Any, List
from backend.safety.policy import load_policy


def evaluate_plan(platform: str, plan: Dict[str, Any], instance_allowlist: List[str]) -> Dict[str, Any]:
    policy = load_policy()

    global_allow = set(policy.get("allowlist", {}).get(platform, []) or [])
    inst_allow = set(instance_allowlist or [])
    effective_allow = global_allow & inst_allow if inst_allow else global_allow

    deny_actions = set(policy.get("deny", {}).get("actions", []) or [])

    violations = []
    for step in plan.get("steps", []) or []:
        action = step.get("action")
        if action in deny_actions:
            violations.append({"action": action, "reason": "Denied by policy"})
        elif action not in effective_allow:
            violations.append({"action": action, "reason": "Not in allow-list"})

    risk = plan.get("risk", "MED")
    confidence = float(plan.get("confidence", 0.5))

    auto_cfg = policy.get("defaults", {}).get("auto_execute", {})
    auto_risk = auto_cfg.get("risk", "LOW")
    auto_conf = float(auto_cfg.get("min_confidence", 0.90))

    auto_execute = (len(violations) == 0) and (risk == auto_risk) and (confidence >= auto_conf) and (not plan.get("requires_approval", True))

    return {
        "allowlisted": len(violations) == 0,
        "violations": violations,
        "risk": risk,
        "confidence": confidence,
        "blast_radius": "SINGLE_RUN",
        "idempotent": True,
        "auto_execute": auto_execute,
    }
