from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import yaml

ROOT = Path(__file__).resolve().parents[2]
SOPS_DIR = ROOT / "data" / "kb" / "sops"


def load_sops() -> List[Dict]:
    sops: List[Dict] = []
    for p in sorted(SOPS_DIR.glob("*.yaml")):
        sops.append(yaml.safe_load(p.read_text(encoding="utf-8")))
    return sops
