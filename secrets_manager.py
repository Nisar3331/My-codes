from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "safety.policy.yaml"


def load_policy() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
